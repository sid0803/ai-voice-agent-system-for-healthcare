"""FastAPI server with Exotel integration for Nova Sonic speech-to-speech AI."""

import os
import src.compat  # Applies platform patches (e.g. Windows WMI deadlock fix) idempotently

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
import re

import httpx
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from src.diagnostics.latency_tracker import latency_telemetry

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup - console + file
# ---------------------------------------------------------------------------
import pathlib
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,  # Use INFO in production; DEBUG is very noisy
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)

# IST timezone for logs and DynamoDB timestamps
IST = timezone(timedelta(hours=5, minutes=30))
logging.Formatter.converter = lambda *args: datetime.now(IST).timetuple()

# Reduce noise from AWS SDK debug logs
logging.getLogger("smithy_core").setLevel(logging.WARNING)
logging.getLogger("smithy_aws_event_stream").setLevel(logging.WARNING)
logging.getLogger("smithy_http").setLevel(logging.WARNING)

from src.nova_client import S2SBidirectionalStreamClient
from src.audio_utils import exotel_to_pcm, pcm_to_exotel, AudioHardener, AudioPolisher
from src.memory_manager import AgentCoreMemoryManager, LocalFileMemoryManager, build_system_prompt_with_memory
from src.routing.intent_router import intent_router
from src.cache.response_cache import response_cache
from src.security.audit_logger import audit_logger
from src.analytics.dynamodb_client import dynamodb_analytics
from src.analytics.processor import analytics_processor
from src.transcript_store import save_transcript

# ---------------------------------------------------------------------------
# [AI-04] Known consultation fees & Spoken Fact Gate (Delegated to src.guards)
# ---------------------------------------------------------------------------
from src.guards import (
    _KNOWN_CONSULTATION_FEES,
    sanitize_spoken_text,
    apply_spoken_fact_gate,
)
from src.idle_monitor import IdleMonitorSession

# ---------------------------------------------------------------------------
# [AI-22] KB version logging and staleness check — runs at startup
# ---------------------------------------------------------------------------
def _check_kb_version():
    try:
        import json as _json
        from datetime import datetime, timezone, timedelta
        kb_path = _PROJECT_ROOT / "data" / "unified_hospital_kb.json"
        if not kb_path.exists():
            return
        meta = _json.loads(kb_path.read_text(encoding="utf-8")).get("metadata", {})
        version     = meta.get("version", "unknown")
        last_updated = meta.get("last_updated", "")
        logger = logging.getLogger(__name__)
        logger.info("[KB] Loaded unified_hospital_kb.json version=%s last_updated=%s", version, last_updated)
        if last_updated:
            try:
                updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - updated_dt).days
                if age_days > 30:
                    logger.warning(
                        "[KB-STALE] unified_hospital_kb.json is %d days old (version=%s). "
                        "Consider refreshing doctor schedules and tariffs.",
                        age_days, version
                    )
            except ValueError:
                pass  # Unrecognised date format — skip staleness check
    except Exception:
        pass

_check_kb_version()


logger = logging.getLogger(__name__)

# Track active background tasks to ensure safe shutdown and prevent garbage collection
_background_tasks = set()


def safe_background_task(coro, name: Optional[str] = None, on_error_msg: str = "Background task failed") -> asyncio.Task:
    """Schedules a coroutine safely, retains reference in _background_tasks, and logs any unhandled exceptions."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _done_cb(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.error("%s (task=%s): %s", on_error_msg, name or t.get_name(), exc, exc_info=exc)

    task.add_done_callback(_done_cb)
    return task


# ---------------------------------------------------------------------------
# Rate Limiter (CRIT-05)
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# Security & Concurrency Utilities
# ---------------------------------------------------------------------------
_session_lock = asyncio.Lock()

# [CRIT-02] Exotel WebSocket shared secret for HMAC validation
# Set EXOTEL_WS_SECRET in .env to enable.
_EXOTEL_WS_SECRET = os.environ.get("EXOTEL_WS_SECRET", "")

# Known Exotel IP ranges (CIDR blocks from Exotel docs - update if Exotel changes)
# We also include all AWS Mumbai (ap-south-1) IP ranges since Exotel dialers run on them
_EXOTEL_IP_PREFIXES = (
    "52.66.", "13.234.", "15.207.", "3.7.", "3.108.",
    "43.204.", "65.0.", "54.169.",
    "13.202.", "13.201.", "13.233.", "13.235.", "43.205.", "15.206.",
    "3.6.", "35.154.", "13.126.", "13.204.", "13.232.", "65.1.",
    "3.109.", "3.110.", "3.111.", "3.8.", "3.9.", "43.206.", "43.207.",
    "13.235.209.", "13.204.230.",
    "103.251.", "103.10.", "103.240.", "182.76.", "182.79.", "182.72."
)

def _is_exotel_ip(client_ip: str) -> bool:
    """Check if the connecting IP is from a known Exotel IP range."""
    return any(client_ip.startswith(prefix) for prefix in _EXOTEL_IP_PREFIXES)

def _verify_exotel_ws_token(token: str, call_sid: str = "") -> bool:
    """Verify the time-limited HMAC nonce appended to the WebSocket URL.

    [AI-03] The raw EXOTEL_WS_SECRET is never sent over the wire. Instead,
    /incoming-call generates a HMAC-SHA256(secret, "exotel:<call_sid>:<minute_bucket>")
    nonce. The nonce is valid for the current 2-minute bucket and the previous one
    (to cover clock skew between Exotel and our server).
    """
    if not _EXOTEL_WS_SECRET:
        return False
    import time as _time
    secret_bytes = _EXOTEL_WS_SECRET.encode()
    current_bucket = int(_time.time()) // 120
    for delta in (0, 1):  # Accept current and previous bucket
        bucket = current_bucket - delta
        msg = f"exotel:{call_sid}:{bucket}".encode()
        expected = hmac.new(secret_bytes, msg, "sha256").hexdigest()
        if hmac.compare_digest(token, expected):
            return True
    return False

def _generate_exotel_ws_nonce(call_sid: str = "") -> str:
    """Generate the HMAC nonce that /incoming-call appends to the WebSocket URL.

    [AI-03] Replaces the previous approach of appending the raw secret.
    The nonce is a 64-char hex string valid for 2 minutes.
    """
    import time as _time
    bucket = int(_time.time()) // 120
    msg = f"exotel:{call_sid}:{bucket}".encode()
    return hmac.new(_EXOTEL_WS_SECRET.encode(), msg, "sha256").hexdigest()

def _append_query_params(url: str, params: list[tuple[str, str]]) -> str:
    """Append URL-encoded query params while preserving any existing query string."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params if value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

def mask_phone(phone: str) -> str:
    """Mask phone number for privacy (PII protection)."""
    if not phone:
        return "unknown"
    p = str(phone).strip()
    if len(p) < 7:
        return p
    return f"{p[:3]}******{p[-4:]}"

# [MED-07] HTTP Bearer security for /health endpoint metrics
_HEALTH_TOKEN = os.environ.get("HEALTH_CHECK_TOKEN", "")
_bearer_scheme = HTTPBearer(auto_error=False)

async def _verify_health_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> bool:
    """Full health metrics require a configured bearer token."""
    if not _HEALTH_TOKEN:
        return False
    return bool(credentials and hmac.compare_digest(credentials.credentials, _HEALTH_TOKEN))

# [CRIT-03] Admin token security for outbound and failover actions
_ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

async def _verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> bool:
    """Protect endpoints that can trigger Exotel side effects or call transfers."""
    if not _ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized - Admin API key not configured")
    if credentials and hmac.compare_digest(credentials.credentials, _ADMIN_API_KEY):
        return True
    raise HTTPException(status_code=401, detail="Unauthorized")

def _get_websocket_client_ip(websocket: WebSocket) -> str:
    """Resolve client IP, honoring reverse proxy forwarding headers."""
    forwarded_for = websocket.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = websocket.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    return websocket.client.host if websocket.client else ""

# ── Multilingual & Persona Handling (Delegated to src.language) ──────────────
from src.language import (
    detect_language,
    LANGUAGE_INSTRUCTIONS,
    _apply_gender_guard,
    _is_liveness_check,
    _ALL_LIVENESS_PHRASES,
    _FILLER_PHRASES,
    _FILLER_COOLDOWN_SEC,
)



nova_voice = os.environ.get("NOVA_VOICE_ID", "")
if not nova_voice:
    logger.warning("[CONFIG] NOVA_VOICE_ID not set in .env — defaulting to 'kiara'. Set NOVA_VOICE_ID=kiara explicitly.")

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
exotel_api_key = os.environ.get("EXOTEL_API_KEY")
exotel_api_token = os.environ.get("EXOTEL_API_TOKEN")
exotel_sid = os.environ.get("EXOTEL_SID")
exotel_subdomain = os.environ.get("EXOTEL_SUBDOMAIN")
exotel_from_number = os.environ.get("EXOTEL_FROM_NUMBER")
exotel_app_id = os.environ.get("EXOTEL_APP_ID")
sip_endpoint = os.environ.get("SIP_ENDPOINT")
ws_public_url = os.environ.get("WS_PUBLIC_URL", "")  

aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
default_aws_region = os.environ.get("AWS_REGION", "us-east-1")
bedrock_region = os.environ.get("BEDROCK_REGION", default_aws_region)
memory_id = os.environ.get("MEMORY_ID")  # AgentCore Memory ID
memory_region = os.environ.get("MEMORY_REGION", os.environ.get("AWS_REGION", "ap-south-1"))

# ---------------------------------------------------------------------------
# Exotel credential validation
# ---------------------------------------------------------------------------
from src.credential_validation import validate_exotel_credentials

validate_exotel_credentials({
    "EXOTEL_API_KEY": exotel_api_key,
    "EXOTEL_API_TOKEN": exotel_api_token,
    "EXOTEL_SID": exotel_sid,
    "EXOTEL_SUBDOMAIN": exotel_subdomain,
})

# ---------------------------------------------------------------------------
# Derived Exotel API base URL
# ---------------------------------------------------------------------------
# [D-05] Guard against None values when Exotel creds are not set
if exotel_subdomain and exotel_sid:
    EXOTEL_API_BASE = f"https://{exotel_subdomain}/v1/Accounts/{exotel_sid}"
else:
    EXOTEL_API_BASE = ""
    logger.warning("[CONFIG] EXOTEL_SUBDOMAIN or EXOTEL_SID not set. Outbound call/failover endpoints will not work.")

# Exotel HTTP client
exotel_auth = (exotel_api_key, exotel_api_token) if (exotel_api_key and exotel_api_token) else None
exotel_http = httpx.AsyncClient(
    auth=exotel_auth,
    timeout=30.0,
)

# ---------------------------------------------------------------------------
# Session State Model (Phase 2 & Conversational Grounding)
# ---------------------------------------------------------------------------
@dataclass
class SessionState:
    session_id: str
    caller_phone: str
    
    # 1. REQUESTED (In-flight proposals, unverified)
    requested_doctor: Optional[str] = None
    requested_dept: Optional[str] = None
    requested_date: Optional[str] = None
    requested_time: Optional[str] = None
    
    # 2. VALIDATED (Tool / Doctor Master verified candidates)
    validated_doctor_id: Optional[str] = None
    validated_doctor_name: Optional[str] = None
    validated_date_iso: Optional[str] = None
    validated_time_24h: Optional[str] = None
    candidate_patient_id: Optional[str] = None
    
    # 3. CONFIRMED_BY_CALLER (Explicit caller confirmation)
    confirmed_doctor: Optional[str] = None
    confirmed_date: Optional[str] = None
    confirmed_time: Optional[str] = None
    confirmed_patient: Optional[str] = None
    
    # 4. COMMITTED_BY_BACKEND (Authoritative backend state)
    current_appointment: Optional[dict] = None
    authorized_patient_id: Optional[str] = None
    confirmed_doctor_id: Optional[str] = None
    confirmed_doctor_name: Optional[str] = None
    confirmed_department_id: Optional[str] = None
    confirmed_department_name: Optional[str] = None
    confirmed_date_iso: Optional[str] = None
    confirmed_time_24h: Optional[str] = None
    confirmed_fee: Optional[int] = None
    last_authoritative_facts: Optional[dict] = None
    
    identity_status: str = "UNKNOWN"
    patient_name: Optional[str] = None
    patient_name_verified: bool = False
    pending_confirmation: Optional[dict] = None
    current_language: str = "en"
    previous_language: str = "en"
    language_confidence: float = 1.0
    active_doctor_candidates: Optional[list] = None
    current_intent: str = "general_query"
    intent_confidence: float = 1.0
    pending_action: Optional[str] = None
    last_tool_name: Optional[str] = None
    last_tool_result: Optional[dict] = None


# ---------------------------------------------------------------------------
# System prompt: ASHA Human Receptionist & Grounded Voice Architecture
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
## INTENT TO TOOL ROUTING & CONTEXTUAL SCOPE
1. APPOINTMENT HISTORY vs BILLING:
   - For past consultations, visits, or medical history: ALWAYS call `appointmentHistoryTool`. NEVER call `getBillingInfoTool`.
   - For outstanding balance, charges, or invoices: Call `getBillingInfoTool`.
   - For active / upcoming appointments: Call `appointmentLookupTool`.
   - For booking new slots: Call `appointmentBookingTool`.
   - For rescheduling existing appointments: Call `appointmentRescheduleTool`.
   - For cancelling appointments: Call `appointmentCancelTool`.
2. CONTEXTUAL RESOLUTION & OTHER DOCTOR:
   - When asked "Who is the other doctor?" or "What about the other specialist?": Answer ONLY with the unmentioned doctor from the current department. Do NOT repeat already-discussed doctors.
   - When asked "What is his fee?" or "What time is her slot?": Answer ONLY the specific fact for the currently discussed doctor.
3. FACT PRUNING & ANTI-DUMPING:
   - When answering a specific question (e.g. fee, timing, address), provide ONLY that specific fact in 1 concise sentence. Do NOT recite the entire tool result or roster.

## ABSOLUTE LANGUAGE RULE (CRITICAL — READ FIRST)
1. DYNAMIC LANGUAGE MIRRORING: Detect and mirror the caller's language on EVERY SINGLE TURN.
2. Default language on call startup is ENGLISH. Your first turn MUST be 100% in English.
3. Switch language immediately when the caller speaks in a different language:
   - If caller speaks Hindi, switch to 100% Hindi Devanagari script.
   - If caller speaks Hinglish, switch to 100% Hinglish in Roman Latin script.
   - If caller speaks English, switch to 100% English Latin script.
4. STRICT SCRIPT ISOLATION: Never mix conversational scripts in a single response.
   - Factual entity names (e.g. "Dr. Sameer Kulkarni", "IS-APP-085649", "MRI", "12:00 PM") may remain intact.
5. NO LANGUAGE RESET ON STATE: Switching language must NEVER reset active appointment, doctor, or patient state.

## BEHAVIORAL ACKNOWLEDGMENT POLICY & HUMAN RECEPTIONIST PERSONA
1. You are Asha, a professional, calm, concise, warm female receptionist at SarvoDaya Hospital.
2. NO AUTOMATIC PREPENDED ACKNOWLEDGMENTS:
   - NEVER use robotic cheerleading or fake enthusiasm words.
   - BAN: "Perfect!", "Great news!", "Good news!", "Sure thing!", "Wonderful!", "Certainly!", "Absolutely!", "Of course!", "बेहतरीन!", "बिल्कुल सही!", "शानदार!", "बढ़िया!", "वाह!", "ज़बरदस्त!".
3. ABSOLUTE BAN ON IVR MENU STYLE (CRITICAL):
   - NEVER say "Press 1 for X, Press 2 for Y" — you are NOT an IVR machine, you are a human receptionist.
   - NEVER use numbered options: "Option 1:", "Option 2:", "Say 1 for...", "For appointments press 1", "For billing press 2" etc.
   - When presenting choices, speak conversationally: "Would you like to book an appointment, or did you have a question?" — NEVER as a numbered list.
3. ANSWER THE QUESTION FIRST (ANSWER FIRST):
   - Answer the caller's specific question immediately in 1 short sentence before offering next steps (e.g. Caller: "What is the hospital name?" -> "SarvoDaya Hospital.").
4. RESPONSE CONTRACT ADHERENCE:
   - When response_contract is EXACT_SLOT_UNAVAILABLE: State clearly that the requested time is not available in that department. Offer to check the nearest open times. NEVER claim any doctor is available at that time.
   - When response_contract is DEPARTMENT_REQUIRED: Say: "Which department would you like me to check?"
   - When state is UNKNOWN/ERROR: Say: "I'm sorry, I couldn't confirm that slot right now. Let me check again." NEVER guess or speculate.
5. RESPONSE LENGTH POLICY:
   - Simple factual answers: 1 short sentence.
   - Normal transactional/booking turns: 1–2 short conversational sentences.
   - Clarifications: 1 concise question.
   - Safety/Emergency: direct, calm handoff instruction.
6. DYNAMIC SINGLE-FIELD INTAKE:
   - Collect exactly ONE missing required field per turn.
   - Dynamically ask for whichever field is missing based on what the caller already provided:
     - If patient, doctor, and date are known -> ask for time: "What time would you prefer?"
     - If doctor and time are known -> ask for patient name: "Could you please share the patient's full name?"
     - If booking details are known -> confirm contact number: "Shall I confirm this with the number you are calling from?"
   - NEVER ask for full name, doctor, department, date, time, and phone all in one turn!
7. PHONETIC ENTITY CONFIRMATION GATE:
   - When ASR hears an uncertain or approximate doctor name (e.g. "Samil Kulkarni" for Dr. Sameer Kulkarni), ask: "Did you mean Dr. Sameer Kulkarni?" before booking.
   - When ASR hears an approximate patient name (e.g. "Barun" for Arun), ask: "I have your name as Arun. Is that correct?".
8. NO PARAPHRASING & OPTIONAL ACKNOWLEDGMENT:
   - NEVER repeat or paraphrase the caller's request (BAN: "I understand you need...", "I understand you want...", "I understand you are audible").
   - If caller asks "Hello? Are you there?" -> answer: "Yes, I'm here. Please go ahead."
   - If caller asks "Am I audible?" -> answer: "Yes, I'm right here. Please go ahead."
9. PROACTIVE NEXT-SLOT ASSISTANCE (NO DEAD ENDS):
   - When a requested slot or date is unavailable and the tool result includes 'proactive_next_slot', ALWAYS offer that alternative in the same turn.
10. NO SCRIPTED FAQ ENDINGS:
   - NEVER append "Is there anything else you would like to know about our services?" during active tasks or booking flows.
11. COMPREHENSIVE BAN ON INTERNAL JARGON:
   - NEVER mention: "tools", "systems", "databases", "APIs", "backend", "functions", "models", "AI", "prompts", "knowledge base", "memory", "processing", "internal records", or "algorithms".
   - NEVER say "I need to use the official tool" or "According to database records". Speak naturally as a human receptionist.
12. AVAILABILITY != BOOKING != RESCHEDULING:
    - Checking open slots does NOT confirm a booking or move an existing appointment until the caller explicitly confirms.
13. GENDER-NEUTRAL RESPECTFUL FORMS:
    - For Asha: use feminine verbs ("मैं बताती हूँ", "karti hoon").
    - For Caller: ALWAYS use gender-neutral respectful forms ("आप + चाहिए / बताइए / कीजिए", "aap + chahiye / bataiye"). NEVER use "चाहती/चाहते".

## HOSPITAL CORE DATA & GROUND TRUTH
- Hospital Name: SarvoDaya Hospital
- Address: SarvoDaya Hospital Main Campus, Plot 42, Healthcare Boulevard, Sector 5, Cyber City (Opposite Central Metro Gate 3).
- Hindi Address: सर्वोदय हॉस्पिटल, प्लॉट 42, हेल्थकेयर बुलेवार्ड, सेक्टर 5, साइबर सिटी (सेंट्रल मेट्रो गेट 3 के सामने)।
- Contact Phone: +91 8 0 4 0 0 9 0 0 0 (Speak each digit individually: "8 0 4 0 0 9 0 0 0").
- OPD Hours: 9:00 AM to 6:00 PM (Monday to Saturday). Emergency is 24/7.
- Always use hospital tools for authoritative doctor schedules, fees, lab test prices, and room tariffs.

## CLARIFY WHEN UNSURE (UNDERSTAND -> VALIDATE -> ACT)
1. If caller intent or ASR transcription is ambiguous, NEVER guess. Ask a short, specific clarification question.
2. For ambiguous lab requests ("I need a lab test"), ask: "Would you like information about test prices or would you like to schedule a visit?"
3. If ASR mishears a non-hospital term (e.g. "laptop"), ask: "Sorry, did you mean a lab test?"

## CALLER CORRECTION & GRACIOUS PIVOTING
1. The caller's latest statement always has priority. If they clarify or change their request, pivot immediately.

## PATIENT IDENTITY, PRIVACY & AUTHORIZATION
1. A phone number is ONLY an identity signal, NOT permanent patient identity.
2. NEVER disclose prior medical records, doctors, or lab reports to a caller based solely on a phone number match.
3. If an existing patient calls from a new number or gives their name, call `searchPatientTool` to locate their candidate profile.
4. UNLINKED PHONE VERIFICATION: If calling from an unlinked phone, ask for their prior Appointment Reference ID before disclosing past records.
5. IDENTITY MISMATCH & CAREGIVERS: If a caller gives a different name than the registered patient, do NOT disclose the patient's records. Caregivers must provide the patient's Appointment Reference ID to view existing records.

## APPOINTMENT BOOKING & AUTHORITATIVE FEE GROUNDING
1. When booking: Collect missing details dynamically (Doctor/Dept, Date, Time, Patient Name, Phone).
2. ANTI-HALLUCINATION GATE: NEVER say an appointment is "booked" or generate any reference ID unless `appointmentBookingTool` has executed successfully.
3. AUTHORITATIVE FEE: Use strictly the exact fee returned by `appointmentBookingTool` (e.g. Dr. Sameer Kulkarni = ₹1,200). Never guess or alter the fee.
4. CROSS-QUESTIONING GROUNDING: If the caller asks "Who did I book?", "Which department?", "What time?", or "What is my reference number?", answer in 1 short direct sentence using the exact details returned by `appointmentBookingTool`.

## SURGERY & OPERATION THEATRE (OT) SAFETY
1. Operation Theatre (OT) slots and surgeries require clinical evaluation by specialist surgeons during OPD consultations.
2. NEVER fabricate surgery slot dates or times. State: "For surgical procedures, our specialist surgeons evaluate patients during OPD consultations. Please visit our OPD or call our hospital desk at 8 0 4 0 0 9 0 0 0 to schedule a consultation."

## CLINICAL SAFETY & APPROVED TRIAGE POLICY
1. Follow hospital-approved clinical triage policy. NEVER invent or infer medical routing logic.
2. If caller mentions red-flag symptoms (severe crushing chest pain, radiating pain, breathlessness, loss of consciousness, stroke symptoms, profuse bleeding):
   - Say immediately: "This sounds urgent. Please stay on the line, I am connecting you to our emergency desk immediately."
   - Execute handoffTool immediately. Do NOT offer medical advice.
3. For non-emergency symptoms: call `clinicalTriageTool` to log symptoms and route to the approved hospital department.

## HANG-UP & CALL DISCONNECT
If caller says "hang up", "disconnect", "that's all", or "end the call":
Say once: "Thank you for calling SarvoDaya Hospital. Take care." and end the call.
Current Date: {{TODAY_DATE}}.
"""



# ---------------------------------------------------------------------------
# AWS Bedrock client
# ---------------------------------------------------------------------------
bedrock_client = S2SBidirectionalStreamClient(
    region=bedrock_region,
    credentials={
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
    },
)

# ---------------------------------------------------------------------------
# AgentCore Memory client
# ---------------------------------------------------------------------------
memory_manager = None
if memory_id:
    memory_manager = AgentCoreMemoryManager(memory_id, memory_region)
    logger.info("[MEMORY] Initialized AgentCoreMemoryManager with ID: %s", memory_id)
else:
    memory_manager = LocalFileMemoryManager()
    logger.info("[MEMORY] Initialized LocalFileMemoryManager (data/patient_memory.json)")

# ---------------------------------------------------------------------------
# Idle timeout configuration
# ---------------------------------------------------------------------------
IDLE_TIMEOUT_SECONDS = 25  # Send idle check after 25s of silence
HANGUP_GRACE_SECONDS = 15 # Hang up if no response within 15s after follow-up

# [AI-05] Hard cap on concurrent sessions. Protects against OOM under traffic spike.
# New connections arriving when the cap is full receive WS close code 1013 (Try Again Later).
MAX_CONCURRENT_SESSIONS: int = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "200"))
session_map: dict = {}


# ---------------------------------------------------------------------------
# FastAPI lifespan: startup tasks + SIGTERM graceful shutdown
# ---------------------------------------------------------------------------
async def run_async_startup_checks():
    """Run all startup initialization checks asynchronously to prevent blocking server startup."""
    # Run System Health Check (P0 Hardening)
    try:
        from src.diagnostics.health import HealthChecker
        diag = await asyncio.to_thread(HealthChecker.run_full_diagnostic)
        
        status_emoji = "🟢" if diag["overall_status"] == "HEALTHY" else "⚠️"
        logger.info(f"\n{'='*40}\n🏥 SYSTEM HEALTH: {diag['overall_status']} {status_emoji}\n{'='*40}")
        
        if diag["overall_status"] != "HEALTHY":
            missing_assets = [k for k,v in diag["assets"].items() if not v]
            if missing_assets:
                logger.warning(f"❌ MISSING AUDIO ASSETS: {', '.join(missing_assets)}")
            
            missing_env = [k for k,v in diag["environment"].items() if not v]
            if missing_env:
                logger.warning(f"❌ MISSING ENV VARS: {', '.join(missing_env)}")
        
        db_ok, db_msg = diag["database"]
        logger.info(f"📁 Database: {db_msg} {'✅' if db_ok else '❌'}")
        
        aws_ok, aws_msg = diag["aws"]
        logger.info(f"☁️ AWS Cloud: {aws_msg} {'✅' if aws_ok else '❌'}")
        logger.info(f"{'='*40}\n")
    except Exception:
        logger.exception("[STARTUP] Health diagnostic failed to run")

    # [LATENCY-01] Pre-warm Unified KB cache and common queries to eliminate cold-start latency
    try:
        from src.tools import _unified_hospital_info
        _PREWARM_QUERIES = [
            "startup warmup",
            "Dr. Amit Sharma timing",
            "Dr. Priya Patel OPD fee",
            "MRI brain with contrast cost",
            "CT scan price",
            "emergency number",
            "cashless mediclaim accepted",
            "hospital address location",
            "blood test timing",
            "Dr. Sameer Kulkarni cardiology appointment",
            "X-ray charges",
            "ultrasound cost",
        ]
        for q in _PREWARM_QUERIES:
            try:
                _unified_hospital_info({"query": q})
            except Exception:
                pass
        logger.info("[STARTUP] ✅ Unified KB cache and common queries pre-warmed successfully.")
    except Exception as e:
        logger.warning("[STARTUP] KB pre-warm skipped: %s", e)

    # Warm up FAISS cache with distilled facts (skip in unified KB mode)
    from src.kb_config import KB_SYSTEM
    if KB_SYSTEM != "unified":
        try:
            from src.tools import sync_community_knowledge
            await asyncio.to_thread(sync_community_knowledge)
            logger.info("[STARTUP] Successfully synchronized distilled facts to FAISS vector cache.")
        except Exception as e:
            logger.error("[STARTUP] Failed to sync distilled facts to FAISS: %s", e)

    # [LOW-02] DynamoDB table auto-creation (idempotent)
    try:
        import boto3
        # Set short timeouts for table check
        from botocore.config import Config
        config = Config(connect_timeout=2.0, read_timeout=2.0, retries={'max_attempts': 0})
        
        def setup_dynamo():
            dynamo = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"), config=config)
            existing = dynamo.list_tables().get("TableNames", [])
            
            # Check Transcripts Table
            table_name = os.environ.get("DYNAMODB_TABLE_NAME", "InDiiServe_Call_Transcript_1")
            if table_name not in existing:
                dynamo.create_table(
                    TableName=table_name,
                    KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
                    AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
                    BillingMode="PAY_PER_REQUEST",
                )
                logger.info("[STARTUP] DynamoDB table '%s' created.", table_name)
            else:
                logger.info("[STARTUP] DynamoDB table '%s' already exists. ✅", table_name)

            # Check Analytics Table with GSI support
            analytics_table_name = os.environ.get("DYNAMODB_ANALYTICS_TABLE", "InDiiServe_Asha_Analytics")
            if analytics_table_name not in existing:
                dynamo.create_table(
                    TableName=analytics_table_name,
                    KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
                    AttributeDefinitions=[
                        {"AttributeName": "session_id", "AttributeType": "S"},
                        {"AttributeName": "hospital_id", "AttributeType": "S"},
                        {"AttributeName": "timestamp", "AttributeType": "S"},
                    ],
                    GlobalSecondaryIndexes=[
                        {
                            "IndexName": "HospitalTimestampIndex",
                            "KeySchema": [
                                {"AttributeName": "hospital_id", "KeyType": "HASH"},
                                {"AttributeName": "timestamp", "KeyType": "RANGE"},
                            ],
                            "Projection": {"ProjectionType": "ALL"},
                        }
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                logger.info("[STARTUP] DynamoDB Analytics table '%s' created with HospitalTimestampIndex GSI.", analytics_table_name)
            else:
                logger.info("[STARTUP] DynamoDB Analytics table '%s' already exists. ✅", analytics_table_name)

            def _seed_tenant_sync(hospital_id: str, seed_file: str):
                try:
                    from src.analytics.dynamodb_client import dynamodb_analytics
                    if not dynamodb_analytics.get_tenant(hospital_id):
                        seed_path = pathlib.Path(__file__).parent.parent / "data" / "seeds" / seed_file
                        if seed_path.exists():
                            with open(seed_path, "r", encoding="utf-8") as f:
                                seed_data = json.load(f)
                            dynamodb_analytics.save_tenant(seed_data)
                            logger.info("[STARTUP] Seeded tenant '%s' from %s", hospital_id, seed_file)
                except Exception as e:
                    logger.error("[STARTUP] Error seeding tenant %s: %s", hospital_id, e)

            # Check Tenants Table
            tenants_table_name = os.environ.get("DYNAMODB_TENANTS_TABLE", "InDiiServe_Tenants")
            if tenants_table_name not in existing:
                dynamo.create_table(
                    TableName=tenants_table_name,
                    KeySchema=[{"AttributeName": "hospital_id", "KeyType": "HASH"}],
                    AttributeDefinitions=[{"AttributeName": "hospital_id", "AttributeType": "S"}],
                    BillingMode="PAY_PER_REQUEST",
                )
                logger.info("[STARTUP] DynamoDB table '%s' created.", tenants_table_name)
                try:
                    dynamo.get_waiter("table_exists").wait(TableName=tenants_table_name, WaiterConfig={"Delay": 2, "MaxAttempts": 10})
                    _seed_tenant_sync("apollo_metro", "apollo_metro_seed.json")
                except Exception as e:
                    logger.error("[STARTUP] Failed to seed default tenant: %s", e)
            else:
                logger.info("[STARTUP] DynamoDB table '%s' already exists. ✅", tenants_table_name)
                try:
                    _seed_tenant_sync("apollo_metro", "apollo_metro_seed.json")
                except Exception as e:
                    logger.error("[STARTUP] Failed to verify/seed default tenant: %s", e)

            # Check Users Table
            users_table_name = os.environ.get("DYNAMODB_USERS_TABLE", "InDiiServe_Users")
            if users_table_name not in existing:
                dynamo.create_table(
                    TableName=users_table_name,
                    KeySchema=[{"AttributeName": "username", "KeyType": "HASH"}],
                    AttributeDefinitions=[{"AttributeName": "username", "AttributeType": "S"}],
                    BillingMode="PAY_PER_REQUEST",
                )
                logger.info("[STARTUP] DynamoDB table '%s' created.", users_table_name)
                try:
                    dynamo.get_waiter("table_exists").wait(TableName=users_table_name, WaiterConfig={"Delay": 2, "MaxAttempts": 10})
                    from src.analytics.dynamodb_client import dynamodb_analytics
                    is_prod = os.environ.get("ENVIRONMENT", "development").lower() == "production"
                    admin_hash = os.environ.get("ADMIN_PASSWORD_HASH")
                    if is_prod and not admin_hash:
                        logger.error("[SECURITY CRITICAL] ADMIN_PASSWORD_HASH is required in production. Refusing to seed default admin user.")
                    else:
                        if not admin_hash:
                            import bcrypt
                            admin_hash = bcrypt.hashpw(b"dev_ephemeral_test_secret_2026", bcrypt.gensalt(rounds=10)).decode()
                            logger.warning("[SECURITY] ADMIN_PASSWORD_HASH not set in development. Using ephemeral dev hash.")
                        dynamodb_analytics.save_user("admin_metro", admin_hash, "apollo_metro", "admin")
                        logger.info("[STARTUP] Seeded user 'admin_metro'.")
                except Exception as e:
                    logger.error("[STARTUP] Failed to seed default user: %s", e)
            else:
                logger.info("[STARTUP] DynamoDB table '%s' already exists. ✅", users_table_name)
                try:
                    from src.analytics.dynamodb_client import dynamodb_analytics
                    if not dynamodb_analytics.get_user("admin_metro"):
                        is_prod = os.environ.get("ENVIRONMENT", "development").lower() == "production"
                        admin_hash = os.environ.get("ADMIN_PASSWORD_HASH")
                        if is_prod and not admin_hash:
                            logger.error("[SECURITY CRITICAL] ADMIN_PASSWORD_HASH is required in production. Refusing to seed missing admin user.")
                        else:
                            if not admin_hash:
                                import bcrypt
                                admin_hash = bcrypt.hashpw(b"dev_ephemeral_test_secret_2026", bcrypt.gensalt(rounds=10)).decode()
                                logger.warning("[SECURITY] ADMIN_PASSWORD_HASH not set in development. Using ephemeral dev hash.")
                            dynamodb_analytics.save_user("admin_metro", admin_hash, "apollo_metro", "admin")
                            logger.info("[STARTUP] Seeded missing user 'admin_metro'.")
                except Exception as e:
                    logger.error("[STARTUP] Failed to verify/seed default user: %s", e)
        
        await asyncio.to_thread(setup_dynamo)
    except Exception as e:
        logger.warning("[STARTUP] DynamoDB table check failed: %s", e)

    # Start Background Sync Worker (SaaS Tier)
    try:
        from src.integrations.sync_engine import sync_engine
        sync_task = asyncio.create_task(sync_engine.scheduled_pull_worker())
        _background_tasks.add(sync_task)
        sync_task.add_done_callback(_background_tasks.discard)
    except Exception:
        logger.warning("[STARTUP] Initial background tasks failed - system may be partially functional.")


async def _session_watchdog():
    """Periodic watchdog to evict zombie sessions (>10 mins) and free memory."""
    while True:
        try:
            await asyncio.sleep(120)  # Check every 2 minutes
            now = time.time()
            stale_sids = []
            async with _session_lock:
                for sid, sess in list(session_map.items()):
                    created_at = getattr(sess, "_created_at", None)
                    if created_at and (now - created_at > 600):  # 10 minutes max call duration
                        stale_sids.append(sid)
            for sid in stale_sids:
                logger.warning("[WATCHDOG] Evicting zombie session %s (>10m old)", sid[:8])
                async with _session_lock:
                    sess = session_map.pop(sid, None)
                if sess:
                    try:
                        await sess.close()
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[WATCHDOG] Session watchdog error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle server startup and graceful shutdown (SIGTERM from Docker/ECS)."""
    # --- STARTUP ---
    logger.info("[STARTUP] InDiiServe Asha Voice Agent starting...")
    
    # Run startup checks and initialization in a background task
    startup_task = asyncio.create_task(run_async_startup_checks())
    _background_tasks.add(startup_task)
    startup_task.add_done_callback(_background_tasks.discard)

    # Launch session watchdog
    watchdog_task = asyncio.create_task(_session_watchdog())
    _background_tasks.add(watchdog_task)
    watchdog_task.add_done_callback(_background_tasks.discard)

    yield  # Server is running and handling requests

    # --- SHUTDOWN (triggered by SIGTERM from container orchestrator) ---
    logger.info("[SHUTDOWN] SIGTERM received. Closing %d active sessions...", len(session_map))
    async with _session_lock:
        close_tasks = [session.close() for session in list(session_map.values())]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
        
    if _background_tasks:
        logger.info("[SHUTDOWN] Cancelling %d pending background tasks...", len(_background_tasks))
        for task in list(_background_tasks):
            task.cancel()
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    
    # Close global HTTP client
    await exotel_http.aclose()
    logger.info("[SHUTDOWN] Exotel HTTP client closed. All sessions cleaned up. Exiting.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    lifespan=lifespan,
    title="InDiiServe Asha Voice Agent",
    version="1.0.0",
)

# [MED-01] CORS — restrict to known origins in production
cors_origins_env = os.environ.get("CORS_ORIGINS", "")
if not cors_origins_env or cors_origins_env.strip() == "*":
    # Fallback: derive origin from WS_PUBLIC_URL to avoid wildcard "*" allow_credentials issue
    from urllib.parse import urlsplit
    def _derive_cors_origin(ws_url: str) -> str:
        if not ws_url:
            return "http://localhost:3000"
        parts = urlsplit(ws_url)
        scheme = "https" if parts.scheme in ("wss", "https") else "http"
        netloc = parts.netloc
        if not netloc:
            return "http://localhost:3000"
        return f"{scheme}://{netloc}"
    _ALLOWED_ORIGINS = [_derive_cors_origin(os.environ.get("WS_PUBLIC_URL", ""))]
else:
    _ALLOWED_ORIGINS = [
        o.strip() for o in cors_origins_env.split(",") if o.strip()
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# [CRIT-05] Rate limiter error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Admin API Router Mount (/api/v1)
# ---------------------------------------------------------------------------
from src.admin.routes.auth import router as admin_auth_router
from src.admin.routes.dashboard import router as admin_dashboard_router
from src.admin.routes.calls import router as admin_calls_router
from src.admin.routes.appointments import router as admin_appointments_router
from src.admin.routes.triage import router as admin_triage_router
from src.admin.routes.events import router as admin_events_router
from src.admin.routes.knowledge import router as admin_knowledge_router
from src.admin.routes.telephony import router as admin_telephony_router
from src.admin.routes.sandbox import router as admin_sandbox_router
from src.admin.routes.analytics import router as admin_analytics_router

app.include_router(admin_auth_router, prefix="/api/v1")
app.include_router(admin_dashboard_router, prefix="/api/v1")
app.include_router(admin_calls_router, prefix="/api/v1")
app.include_router(admin_appointments_router, prefix="/api/v1")
app.include_router(admin_triage_router, prefix="/api/v1")
app.include_router(admin_events_router, prefix="/api/v1")
app.include_router(admin_knowledge_router, prefix="/api/v1")
app.include_router(admin_telephony_router, prefix="/api/v1")
app.include_router(admin_sandbox_router, prefix="/api/v1")
app.include_router(admin_analytics_router, prefix="/api/v1")



@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Silence favicon 404 logs."""
    return Response(status_code=204)


# [AI-02] Serve the admin portal SPA at root and all non-API paths.
# portal/dist is built by `npm run build` inside the portal/ directory.
_PORTAL_DIST = pathlib.Path(__file__).resolve().parent.parent / "portal" / "dist"

if _PORTAL_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    _assets_dir = _PORTAL_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="portal-assets")
    logger.info("[SPA] Portal dist found at %s — serving SPA at /", _PORTAL_DIST)
else:
    logger.warning("[SPA] portal/dist not found. Root / will return JSON status only.")


@app.get("/", include_in_schema=False)
async def spa_root():
    """Serve the React portal SPA index at /. Falls back to JSON if dist is not built."""
    index = _PORTAL_DIST / "index.html"
    if index.exists():
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return {"message": "Exotel Media Stream Server is running!"}


@app.get("/health")
@limiter.limit("120/minute")
async def health(
    request: Request,
    authenticated: bool = Depends(_verify_health_token),
):
    """Health check endpoint for AWS load balancers, ECS, and App Runner.
    Full metrics require HEALTH_CHECK_TOKEN Bearer auth (MED-07).
    """
    if authenticated:
        return {
            "status": "healthy",
            "active_sessions": len(session_map),
            "service": "InDiiServe-Asha-Voice-Agent",
        }
    # Unauthenticated callers (e.g. AWS ALB) get basic status only
    return {"status": "healthy"}


@app.get("/incoming-call")
@limiter.limit("120/minute")
async def incoming_call(request: Request):
    """Dynamic Voicebot URL endpoint for Exotel App Bazar.

    Exotel calls this HTTPS endpoint and expects a JSON response with the
    WebSocket URL. Format: {"url": "wss://..."}

    Exotel sends query params: CallSid, CallFrom, CallTo, Direction, From, To, etc.
    We pass CallSid and CallFrom as query params on the WebSocket URL so the
    WebSocket handler can extract them (Exotel's WS start event may not include them).
    """
    call_sid = request.query_params.get("CallSid", "")
    call_from = request.query_params.get("CallFrom", "") or request.query_params.get("From", "")

    if ws_public_url:
        ws_url = ws_public_url
    else:
        host = request.headers.get("host", "localhost:3000")
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        scheme = "wss" if forwarded_proto == "https" else "ws"
        ws_url = f"{scheme}://{host}/exotel-stream"

    # Append auth and call metadata so the WebSocket handler can authenticate
    # Exotel and recover call context. Values are URL-encoded because encrypted
    # PII can contain reserved URL characters.
    params = []
    if _EXOTEL_WS_SECRET:
        # [AI-03] Send a time-limited HMAC nonce, NOT the raw secret.
        # This prevents the secret from appearing in Nginx/Exotel access logs.
        params.append(("token", _generate_exotel_ws_nonce(call_sid)))
    if call_sid:
        params.append(("CallSid", call_sid))
    if call_from:
        encrypted_from = dynamodb_analytics.encrypt_data(call_from)
        params.append(("CallFrom", encrypted_from))
    if params:
        ws_url = _append_query_params(ws_url, params)

    # PII Scrubbing: Sanitize the printed URL for logs
    log_ws_url = ws_url
    if call_from:
        log_ws_url = ws_url.replace(call_from, mask_phone(call_from))
    
    logger.info("Incoming call - CallSid: %s, CallFrom: %s, returning WS URL: %s", call_sid, mask_phone(call_from), log_ws_url)
    return {"url": ws_url}


@app.api_route("/outbound-call", methods=["GET", "POST"])
async def outbound_call(
    request: Request,
    authorized: bool = Depends(_verify_admin_token),
):
    """Initiate an outbound call via the Exotel REST API."""
    host = request.headers.get("host", "localhost")

    # Accept target number from query param or form field
    to_number = request.query_params.get("to")
    if to_number is None:
        try:
            form = await request.form()
            to_number = form.get("to")
        except Exception as e:
            logger.warning("Failed to parse form parameter 'to' in outbound_call: %s", e)

    if not to_number:
        return PlainTextResponse("Missing 'to' parameter", status_code=400)

    # Build the Url parameter - use App Bazar flow if configured, else fall back to direct WebSocket
    if exotel_app_id:
        applet_url = f"http://my.exotel.com/exoml/start_voice/{exotel_app_id}"
    else:
        applet_url = f"http://{host}/exotel-stream"

    try:
        resp = await exotel_http.post(
            f"{EXOTEL_API_BASE}/Calls/connect.json",
            data={
                "From": exotel_from_number,
                "To": to_number,
                "CallerId": exotel_from_number,
                "Url": applet_url,
            },
        )
        resp.raise_for_status()
        return PlainTextResponse("Ok")
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Exotel API error initiating outbound call to %s: %s %s",
            to_number,
            exc.response.status_code,
            exc.response.text,
        )
        return PlainTextResponse(
            f"Exotel API error: {exc.response.status_code}",
            status_code=502,
        )
    except Exception:
        logger.exception("Error initiating outbound call to %s", to_number)
        return PlainTextResponse("Internal server error", status_code=500)


@app.api_route("/failover", methods=["GET", "POST"])
async def failover(
    request: Request,
    authorized: bool = Depends(_verify_admin_token),
):
    """Transfer an active call to the SIP endpoint via Exotel call transfer API."""
    # Accept call_sid from query param or form field
    call_sid = request.query_params.get("call_sid")
    if call_sid is None:
        try:
            form = await request.form()
            call_sid = form.get("call_sid")
        except Exception as e:
            logger.warning("Failed to parse form parameter 'call_sid' in failover: %s", e)

    if not call_sid:
        return PlainTextResponse("Missing 'call_sid' parameter", status_code=400)

    try:
        resp = await exotel_http.post(
            f"{EXOTEL_API_BASE}/Calls/{call_sid}/connect.json",
            data={"To": sip_endpoint},
        )
        resp.raise_for_status()
        return PlainTextResponse("Ok")
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Exotel API error transferring call %s to SIP: %s %s",
            call_sid,
            exc.response.status_code,
            exc.response.text,
        )
        return PlainTextResponse(
            f"Exotel API error: {exc.response.status_code}",
            status_code=502,
        )
    except Exception:
        logger.exception("Error transferring call %s to SIP endpoint", call_sid)
        return PlainTextResponse("Internal server error", status_code=500)


# [AI-06] Distiller Fact Review Endpoints moved to /api/v1/knowledge/review-facts
# They are now in src/admin/routes/knowledge.py with proper JWT auth middleware.
# Old paths /admin/review-facts → new paths /api/v1/knowledge/review-facts

@app.websocket("/exotel-stream")
async def exotel_stream(websocket: WebSocket):
    """WebSocket route for Exotel voice bot applet connections.

    Exotel protocol:
    - JSON text frames for 'start' and 'stop' events
    - Raw binary frames for PCM audio (16-bit signed LE, 8kHz, mono)
    """
    # [CRIT-02] WebSocket Authentication: verify caller identity before accepting
    client_ip = _get_websocket_client_ip(websocket)
    ws_token = websocket.query_params.get("token", "")
    # [AI-03] Extract call_sid early so the HMAC nonce verifier can use it
    ws_call_sid = websocket.query_params.get("CallSid", "")

    if not _EXOTEL_WS_SECRET:
        logger.error("[AUTH] EXOTEL_WS_SECRET is not configured/empty. Rejecting WebSocket connection.")
        await websocket.close(code=1008)
        return

    # Allow connection if client provides correct token OR comes from a verified Exotel/AWS IP
    is_valid_token = ws_token and _verify_exotel_ws_token(ws_token, call_sid=ws_call_sid)
    is_verified_exotel = client_ip and _is_exotel_ip(client_ip)

    if not is_valid_token and not is_verified_exotel:
        logger.warning(
            "[AUTH] WebSocket rejected — invalid token and non-Exotel IP: %s (token: %s)",
            client_ip,
            ws_token
        )
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("Exotel client connected from %s", client_ip)

    # Extract call metadata from WebSocket URL query params
    # (passed from /incoming-call endpoint)
    encrypted_call_from = websocket.query_params.get("CallFrom", "")
    
    # Decrypt phone number (PII Hardening P1)
    ws_call_from = dynamodb_analytics.decrypt_data(encrypted_call_from)
    
    if ws_call_sid or ws_call_from:
        logger.info("WS query params - CallSid: %s, CallFrom: %s", ws_call_sid, mask_phone(ws_call_from))

    # Create a session for this connection
    session_id = str(uuid4())
    
    # SaaS Hardening: Validate Tenant Status (P0)
    # Check if the hospital is set in query params or default
    hospital_id = websocket.query_params.get("hospital_id", os.environ.get("HOSPITAL_ID", "default_tier2"))
    from src.integrations.tenant_manager import tenant_manager
    tenant_status = tenant_manager.get_status(hospital_id)
    
    if tenant_status == "pending":
        logger.warning(f"[AUTH] Rejecting call for PENDING tenant {hospital_id}")
        await websocket.close(code=1008) # Policy Violation
        return

    # [AI-05] Enforce session cap — reject new connections when limit is reached
    if len(session_map) >= MAX_CONCURRENT_SESSIONS:
        logger.warning(
            "[CAP] Session map full (%d/%d). Rejecting new WebSocket connection from %s.",
            len(session_map), MAX_CONCURRENT_SESSIONS, client_ip
        )
        await websocket.close(code=1013)  # 1013 = Try Again Later
        return

    session = bedrock_client.create_stream_session(session_id)
    session._created_at = time.time()
    session.hospital_id = hospital_id # Inject for downstream use
    session.call_sid = ws_call_sid or ""
    session.state = SessionState(
        session_id=session_id,
        caller_phone=ws_call_from or "",
        identity_status="UNKNOWN",
    )
    
    # Audit: Log connection start
    audit_logger.log_event(session_id, "SESSION_START", hospital_id, {"caller": mask_phone(ws_call_from)})
    
    async with _session_lock:
        session_map[session_id] = session
    # [HIGH-03] All session_map mutations are now lock-guarded.

    # Initiate the Bedrock stream in the background.
    # initiate_session runs forever (_process_response_stream is a while-loop),
    # so we fire-and-forget but poll for session.stream to be ready before setup.
    task_initiate = asyncio.ensure_future(bedrock_client.initiate_session(session_id))
    _background_tasks.add(task_initiate)
    task_initiate.add_done_callback(_background_tasks.discard)

    call_sid = ""
    hardener = AudioHardener()
    polisher = AudioPolisher()

    # [FIX CRIT-03] Initialize idle_monitor_task to None to prevent UnboundLocalError
    # if the WebSocket disconnects before the 'start' event is received.
    idle_monitor_task = None

    # -----------------------------------------------------------------------
    # Transcript tracking
    # -----------------------------------------------------------------------
    transcripts: list[dict] = []
    seen_transcript_entries: set = set()
    caller_phone = ""

    # Track conversation turns for memory saving
    current_user_text = ""
    current_assistant_text = ""
    transcript_saved = False
    call_start_time = None
    turn_index = 1

    # -----------------------------------------------------------------------
    # Refined Silence Thresholds (Requirement: Clinical Safety)
    # -----------------------------------------------------------------------
    last_activity_time = time.time()
    idle_prompt_sent = False
    escalation_triggered = False
    
    DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"

    # [FIX LOW-03] Warn loudly if DEMO_MODE is on with real Exotel credentials
    if DEMO_MODE and exotel_api_key and exotel_api_token:
        logger.warning("[SECURITY] DEMO_MODE=true with real Exotel credentials detected! "
                       "The chat backdoor is active. Set DEMO_MODE=false for production.")

    SOFT_FOLLOW_UP_SEC = 30 if not DEMO_MODE else 45
    ESCALATION_SEC = 50 if not DEMO_MODE else 60 
    
    detected_language = "en"
    previous_language = "en"   # [LANG-FIX] Tracks prior turn's language to detect mid-call switches
    active_language = "en"
    pending_lang_code = "en"
    pending_lang_streak = 0
    is_first_user_turn = True  # Tracks initial turn to flush Nova Sonic opening buffer
    tool_in_progress = False
    call_start_time = datetime.now(timezone.utc)
    pending_audio_outputs: list[dict] = []

    # ── Acoustic Filler Tracking ──────────────────
    _filler_index: dict[str, int] = {"en": 0, "hi": 0, "hi-en": 0, "bn": 0}
    _last_filler_time = 0.0

    async def flush_pending_audio_outputs() -> None:
        if not session.stream_sid:
            return
        if not pending_audio_outputs:
            return
        logger.info("Flushing %d pending Nova audio chunks for session %s", len(pending_audio_outputs), session.stream_sid)
        while pending_audio_outputs:
            data = pending_audio_outputs.pop(0)
            await _send_nova_audio_output(data)

    async def hangup_call():
        """Terminate the call by closing the WebSocket connection.

        Exotel doesn't have a REST API to terminate an active voicebot call.
        Closing the WebSocket signals Exotel to end the call.
        """
        if not call_sid:
            return
        logger.info("No response after idle follow-up - hanging up call %s", call_sid)
        try:
            await websocket.close()
            logger.info("WebSocket closed to terminate call %s", call_sid)
        except Exception:
            logger.exception("Error closing WebSocket for call %s", call_sid)

    idle_monitor_session = IdleMonitorSession(
        call_sid=call_sid,
        session_id=session_id,
        hospital_id=hospital_id,
        caller_phone=caller_phone,
        send_message_fn=lambda prompt: bedrock_client.send_text_message(session_id, prompt),
        hangup_fn=hangup_call,
        audit_log_fn=audit_logger.log_event,
        soft_follow_up_sec=SOFT_FOLLOW_UP_SEC,
        escalation_sec=ESCALATION_SEC,
        poll_interval_sec=2.0,
        is_tool_in_progress_fn=lambda: tool_in_progress,
        mask_phone_fn=mask_phone,
    )

    def reset_idle_timer():
        nonlocal last_activity_time, idle_prompt_sent
        last_activity_time = time.time()
        idle_prompt_sent = False
        idle_monitor_session.record_activity()

    async def send_idle_followup(is_escalation: bool = False):
        """Send follow-up or trigger emergency escalation on silence."""
        nonlocal idle_prompt_sent, escalation_triggered
        if not call_sid:
            return
        if is_escalation:
            escalation_triggered = True
        else:
            idle_prompt_sent = True
        await idle_monitor_session.trigger_followup(is_escalation=is_escalation)

    async def idle_monitor():
        """Background task: Clinical safety silence monitoring."""
        await idle_monitor_session.run()

    # -----------------------------------------------------------------------
    # Register Nova Sonic event handlers
    # -----------------------------------------------------------------------

    async def _send_nova_audio_output(data):
        try:
            if not session.stream_sid:
                if len(pending_audio_outputs) >= 20:
                    pending_audio_outputs.pop(0)
                pending_audio_outputs.append(data)
                logger.warning(
                    "Nova audio output received before stream_sid was ready; buffering chunk (%d buffered)",
                    len(pending_audio_outputs),
                )
                return

            pcm_bytes = base64.b64decode(data["content"])
            polished_bytes = polisher.process_chunk(pcm_bytes)
            exotel_bytes = pcm_to_exotel(polished_bytes)
            payload_b64 = base64.b64encode(exotel_bytes).decode("utf-8")
            media_event = {
                "event": "media",
                "stream_sid": session.stream_sid,
                "media": {"payload": payload_b64},
            }
            await websocket.send_text(json.dumps(media_event))
            
            # [PILLAR 0] Mark T6 = first audio packet sent to Exotel
            turn_t = latency_telemetry.get_or_create_turn(session_id, turn_index)
            turn_t.mark_t6()
            logger.debug("Forwarded Nova audio chunk to Exotel for session %s", session.stream_sid)

        except Exception:
            logger.exception("Error sending audio to Exotel")

    def _handle_audio_output(data):
        """Decode base64 PCM from Nova, convert via pcm_to_exotel(), send as base64 JSON media event."""
        # [PILLAR 0] Mark T5 = first audio output chunk generated
        turn_t = latency_telemetry.get_or_create_turn(session_id, turn_index)
        turn_t.mark_t5()
        safe_background_task(_send_nova_audio_output(data), name="send_nova_audio")

    def _handle_content_end(data):
        """Send clear event as JSON text frame on interruption."""
        async def _send():
            try:
                if data.get("stopReason") == "INTERRUPTED":
                    clear_payload = {"event": "clear"}
                    if session.stream_sid:
                        clear_payload["stream_sid"] = session.stream_sid
                    await websocket.send_text(json.dumps(clear_payload))
            except Exception:
                logger.exception("Error sending clear to Exotel")
        safe_background_task(_send(), name="send_clear")

    def _handle_tool_use(data):
        """Log tool invocation and pause idle timer."""
        nonlocal tool_in_progress
        tool_in_progress = True
        tool_name = data.get("name") or data.get("toolName", "unknown")
        tool_args = data.get("content") or data.get("input") or "{}"
        logger.info("Tool called: %s with args: %s", tool_name, tool_args)
        
        # [PILLAR 0] Mark T_tool_start
        turn_t = latency_telemetry.get_or_create_turn(session_id, turn_index)
        turn_t.mark_tool_start(tool_name)
        
        # [DEAD-AIR-FIX] Inject immediate acoustic filler so caller doesn't hear silence
        safe_background_task(_inject_filler(), name="inject_filler")

        # [AUTO-PHONE-INJECT] Automatically add the caller's phone number to
        # searchPatientTool queries that only provide a name, avoiding disambiguation loops.
        if tool_name.lower() in ("searchpatienttool", "search_patient", "findpatient"):
            try:
                args_dict = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                if isinstance(args_dict, dict) and not args_dict.get("phone") and caller_phone:
                    args_dict["phone"] = caller_phone
                    tool_args = json.dumps(args_dict)
                    data["content"] = tool_args
                    logger.info("[AUTO-PHONE] Injected caller phone %s into patient search", mask_phone(caller_phone))
            except Exception:
                pass

        # [TOOL SCOPE GUARD] Prevent concurrent/speculative tool calls for unrelated departments
        if tool_name.lower() == "doctoravailabilitytool" and session.state.requested_dept:
            try:
                args_dict = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                called_dept = args_dict.get("department", "")
                if called_dept and called_dept.lower() != session.state.requested_dept.lower():
                    logger.warning("[SCOPE-GUARD] Blocked unsolicited department query: %s (Active scope: %s)", called_dept, session.state.requested_dept)
                    return
            except Exception:
                pass
        if DEMO_MODE:
            asyncio.ensure_future(websocket.send_text(json.dumps({"event": "tool", "name": tool_name})))

    def _handle_text_output(data):
        nonlocal detected_language, previous_language, is_first_user_turn, current_user_text, current_assistant_text
        nonlocal active_language, pending_lang_code, pending_lang_streak
        content = str(data.get("content", ""))
        role = data.get("role", "")

        # [FIX-2] Skip empty/whitespace-only/punctuation-only text output events — prevents dead air and lonely dandas
        if not content.strip() or not re.sub(r'[\s।\.,\?!;:।॥\-_]+', '', content):
            return

        # Filter out Bedrock system/interruption events from voice stream
        if "interrupted" in content and "true" in content:
            return

        # [FIX-5] Suppress & Strip AI-generated welcome greetings.
        # The pre-recorded greeting.pcm already played on call connect.
        # If Nova Sonic attempts to output a welcome greeting on its response turn,
        # we strip out the greeting prefix so ONLY the answer/query response is spoken.
        if role in ("ASSISTANT", "assistant"):
            content = _apply_gender_guard(content)
            data["content"] = content
            greeting_regexes = [
                r'(?i)^\s*(hello|hi|namaste|namaskar)?[\s,!]*(welcome to (sarvodaya|indiiserve) (hospital|healthcare)!?)?[\s,!]*(this is asha\.?)?[\s,!]*(how can i (help|assist) you (today)?)?[\s,!]*',
                r'^\s*(नमस्ते|हेलो)?[\s,!]*(सर्वोदय हॉस्पिटल|इंडीसर्व हेल्थकेयर में आपका स्वागत है।?)?[\s,!]*(मैं आशा हूँ।?)?[\s,!]*(आज मैं आपकी क्या मदद कर सकती हूँ\??)?[\s,!]*'
            ]
            has_greeting_keyword = any(kw in content.lower() for kw in [
                "welcome to sarvodaya", "welcome to indiiserve", "this is asha", "how can i help you today",
                "सर्वोदय हॉस्पिटल में आपका स्वागत है", "इंडीसर्व हेल्थकेयर में आपका स्वागत है", "मैं आशा हूँ", "आपका स्वागत है! मैं आशा हूँ"
            ])
            if has_greeting_keyword:
                cleaned_content = content
                for reg in greeting_regexes:
                    cleaned_content = re.sub(reg, '', cleaned_content).strip()
                # Also strip leading "Hello," or "Hi," if present before answer
                cleaned_content = re.sub(r'(?i)^\s*(hello|hi|namaste|namaskar)[\s,!]+', '', cleaned_content).strip()
                cleaned_content = re.sub(r'^\s*(नमस्ते|हेलो)[\s,!]+', '', cleaned_content).strip()

                if not cleaned_content:
                    logger.info("[GREET-SUPPRESS] Suppressed pure AI welcome greeting: %s", content[:60])
                    return
                else:
                    logger.info("[GREET-STRIP] Stripped welcome prefix from AI output. Remaining: %s", cleaned_content[:60])
                    data["content"] = cleaned_content
                    content = cleaned_content

            # [FIX-6] Strip list numbers, linebreaks, and stray brackets from spoken output
            content = sanitize_spoken_text(content)
            data["content"] = content

        # [SPOKEN FACT GATE] Pre-Audio Factual Claim Verification
        content = apply_spoken_fact_gate(content, session, role)
        data["content"] = content

        # Filter out injected system commands/instructions
        if content.strip().startswith("["):
            logger.info("⚙️ [SYSTEM EVENT] %s", content.strip())
            return
            
        # Dedup key - check if this content was already processed in a previous turn
        dedup_key = (role, content)
        is_new = content.strip() and dedup_key not in seen_transcript_entries

        # Only process fresh assistant text or user inputs to avoid duplication
        if not is_new and role == "ASSISTANT":
            return

        if is_new:
            if role == "USER":
                logger.info("🎙️ [USER] (Call: %s): %s", mask_phone(caller_phone), content)
            elif role == "ASSISTANT":
                logger.info("👩‍⚕️ [ASHA] (Call: %s): %s", mask_phone(caller_phone), content)

        if DEMO_MODE and role == "ASSISTANT" and is_new:
            safe_background_task(websocket.send_text(json.dumps({"event": "text", "text": content})), name="demo_text_push")

        # Store transcript (deduplicate)
        if is_new:
            seen_transcript_entries.add(dedup_key)
            transcripts.append({"role": role, "content": content})

        if role == "USER" and len(content.strip()) > 2:
            # [LIVENESS-LOCK] If caller checks in during tool execution, do NOT reset context.
            if tool_in_progress and _is_liveness_check(content):
                logger.info("[LIVENESS] Caller check-in '%s' during tool — preserving context, sending ack.", content)
                safe_background_task(
                    bedrock_client.send_text_message(
                        session_id,
                        "Yes, I'm right here — still checking for you. Just a moment.",
                        interactive=False
                    ),
                    name="liveness_ack"
                )
                return  # Skip normal processing; preserve in-flight tool result

            current_user_text += content + " "
            reset_idle_timer()
            
            # --- START CRITICAL OPTIMIZATION: Semantic Router ---
            intent = intent_router.route(content)
            if intent != "UNKNOWN":
                asset_id = intent_router.get_static_response_id(intent)
                if asset_id:
                    logger.info("Semantic Router HIT: %s -> %s", intent, asset_id)
                    cached_audio = response_cache.get_audio(asset_id)
                    if cached_audio:
                        safe_background_task(stream_cached_audio(cached_audio), name="stream_cached_audio")
            # --- END OPTIMIZATION ---

            # --- START LANGUAGE MIRRORING & FIRST TURN FLUSH ---
            lang = detect_language(content)
            if lang in ["hindi", "hinglish"]:
                new_lang_code = "hi"
            elif lang == "bengali":
                new_lang_code = "bn"
            else:
                new_lang_code = "en"

            should_switch = False
            if is_first_user_turn:
                active_language = new_lang_code
                pending_lang_streak = 0
                is_first_user_turn = False
                # Default prompt is English. Only inject on turn 1 if non-English
                if new_lang_code != "en":
                    should_switch = True
            elif new_lang_code != active_language:
                # If explicit switch request (e.g., "speak bengali", "with bengali") or confirmed streak
                is_explicit_request = any(p in content.lower() for p in [
                    "in bengali", "in bangla", "speak bengali", "with bengali", "go with bengali",
                    "in hindi", "speak hindi", "in english", "speak english"
                ])
                if is_explicit_request or pending_lang_streak >= 1 or new_lang_code == pending_lang_code:
                    should_switch = True
                    active_language = new_lang_code
                    pending_lang_streak = 0
                else:
                    pending_lang_code = new_lang_code
                    pending_lang_streak = 1
            else:
                pending_lang_streak = 0

            # Send with interactive=True on initial turn OR actual confirmed language switch
            if should_switch:
                logger.info("[FIRST-TURN/LANG-SWITCH] Active language set to %s (%s) | Injecting language instruction with INTERRUPT", active_language, lang)
                instruction = LANGUAGE_INSTRUCTIONS.get(lang, LANGUAGE_INSTRUCTIONS["english"])
                asyncio.ensure_future(
                    bedrock_client.send_text_message(session_id, instruction, interactive=True)
                )

            previous_language = active_language   # Track current turn's active language
            detected_language = active_language
            # --- END LANGUAGE MIRRORING ---

        elif role == "ASSISTANT":
            # Only accumulate NEW content for memory (skip duplicates)
            if is_new:
                current_assistant_text += content + " "
            if not idle_prompt_sent:
                reset_idle_timer()
            if memory_manager and current_user_text.strip() and current_assistant_text.strip():
                asyncio.ensure_future(
                    memory_manager.save_interaction(
                        session_id,
                        current_user_text.strip(),
                        current_assistant_text.strip(),
                    )
                )
                current_user_text = ""
                current_assistant_text = ""


    def _handle_error(data):
        logger.error("Error in Bedrock session %s: %s", session_id, data)

    def _handle_tool_result(data):
        nonlocal tool_in_progress
        tool_in_progress = False
        logger.info("Tool result received: %s", data)
        reset_idle_timer()
        
        # [PILLAR 0] Mark T_tool_result
        turn_t = latency_telemetry.get_or_create_turn(session_id, turn_index)
        turn_t.mark_tool_result()
        
        # State & Provenance Capture (Phase 2 & Conversational Grounding)
        try:
            raw_content = data.get("content") or data.get("result") or "{}"
            result_dict = json.loads(raw_content) if isinstance(raw_content, str) else (raw_content if isinstance(raw_content, dict) else {})
            
            # 1. Capture sanitized caller-safe facts with provenance
            if "facts" in result_dict and isinstance(result_dict["facts"], dict):
                session.state.last_authoritative_facts = result_dict["facts"]
            
            # 2. Capture confirmed appointment state upon successful booking
            if result_dict.get("status") == "CONFIRMED" and result_dict.get("ref_id"):
                session.state.current_appointment = {
                    "doctor_id": result_dict.get("doctor_id", "doc_001"),
                    "doctor_name": result_dict.get("doctor_name", "Dr. Sameer Kulkarni"),
                    "department": result_dict.get("department", "Cardiology"),
                    "date": result_dict.get("date", "tomorrow"),
                    "time": result_dict.get("time", "10:00 AM"),
                    "fee": result_dict.get("fee", 1200),
                    "reference_id": result_dict.get("ref_id"),
                    "status": "CONFIRMED"
                }
                session.state.confirmed_doctor_id = result_dict.get("doctor_id")
                session.state.confirmed_doctor_name = result_dict.get("doctor_name")
                session.state.confirmed_date_iso = result_dict.get("date_iso")
                session.state.confirmed_time_24h = result_dict.get("time_24h")
                session.state.confirmed_fee = result_dict.get("fee")
                logger.info("[STATE-UPDATE] Captured COMMITTED appointment: %s (Fee: Rs. %s)", result_dict.get("ref_id"), result_dict.get("fee"))
            elif result_dict.get("status") == "CANCELLED":
                if session.state.current_appointment:
                    session.state.current_appointment["status"] = "CANCELLED"
                logger.info("[STATE-UPDATE] Updated appointment status to CANCELLED")
        except Exception as ex:
            logger.warning("[STATE-UPDATE] Failed to parse tool result for state update: %s", ex)

    def _handle_completion_end(data):
        nonlocal turn_index
        logger.info("[SYSTEM] Completion ended (stopReason: %s) for Turn #%d", data.get("stopReason", "unknown"), turn_index)
        # Advance turn index for next conversation exchange
        turn_index += 1
        latency_telemetry.reset_turn(session_id, turn_index)


    def _handle_stream_complete(data=None):
        logger.info("Stream completed for client: %s (data: %s)", session.stream_sid, data)
        if isinstance(data, dict) and data.get("reason") == "natural_timeout":
            logger.info("[SESSION-END] 8-minute natural timeout reached. Triggering clean hangup.")
            asyncio.create_task(hangup_call())

    async def stream_cached_audio(pcm_bytes: bytes):
        """Helper to stream cached audio bytes back to Exotel while model is thinking."""
        try:
            exotel_bytes = pcm_to_exotel(pcm_bytes)
            payload_b64 = base64.b64encode(exotel_bytes).decode("utf-8")
            await websocket.send_text(json.dumps({
                "event": "media",
                "stream_sid": session.stream_sid,
                "media": {"payload": payload_b64}
            }))
        except Exception:
            logger.error("Failed to stream cached audio")

    async def _inject_filler():
        """Inject a rotating acoustic filler to bridge tool-execution dead air."""
        nonlocal _last_filler_time
        now = time.time()
        if now - _last_filler_time < _FILLER_COOLDOWN_SEC:
            return
        _last_filler_time = now
        lang_key = detected_language if detected_language in _FILLER_PHRASES else "en"
        phrases = _FILLER_PHRASES.get(lang_key, _FILLER_PHRASES["en"])
        idx = _filler_index.get(lang_key, 0) % len(phrases)
        phrase = phrases[idx]
        _filler_index[lang_key] = idx + 1
        logger.info("[FILLER] Injecting dead-air bridge: '%s'", phrase)
        
        # [PILLAR 0] Mark T_first_filler
        turn_t = latency_telemetry.get_or_create_turn(session_id, turn_index)
        turn_t.mark_first_filler()
        try:
            await bedrock_client.send_text_message(session_id, phrase, interactive=False)
        except Exception:
            logger.warning("[FILLER] Failed to inject filler phrase")

    session.on_event("audioOutput", _handle_audio_output)
    session.on_event("contentEnd", _handle_content_end)
    session.on_event("toolUse", _handle_tool_use)
    session.on_event("textOutput", _handle_text_output)
    session.on_event("error", _handle_error)
    session.on_event("toolResult", _handle_tool_result)
    session.on_event("completionEnd", _handle_completion_end)
    session.on_event("streamComplete", _handle_stream_complete)

    # VAD State tracking (per session)
    user_speaking = False
    speech_frames = 0
    silence_frames = 0
    last_interrupt_time = 0.0  # [FIX-1C] Cooldown timestamp to prevent interrupt storm

    # [LATENCY-OPT] Minimum real-speech gate & adaptive End-of-Turn thresholds
    MIN_SPEECH_FRAMES_TO_COMMIT = 4  # ~80ms of sustained voice required

    def _get_eot_threshold(sf: int) -> int:
        """Adaptive EOT: short query (sf<10) fires at 120ms (6 frames), standard at 160ms (8 frames), long at 220ms (11 frames)."""
        if sf < 10:
            return 6   # 120ms
        elif sf < 25:
            return 8   # 160ms
        else:
            return 11  # 220ms

    # Helper to process incoming audio with VAD and interruption detection
    async def process_incoming_audio(pcm_samples: bytes):
        nonlocal user_speaking, speech_frames, silence_frames
        try:
            samples = np.frombuffer(pcm_samples, dtype=np.int16).astype(np.float32)
            raw_rms = np.sqrt(np.mean(samples**2)) if len(samples) > 0 else 0.0

            assistant_speaking = bedrock_client.is_assistant_speaking(session_id)
            
            if (datetime.now(timezone.utc) - call_start_time).total_seconds() < 5.0:
                # Ignore VAD during initial greeting playback to prevent false EOT
                user_speaking = False
                speech_frames = 0
                silence_frames = 0
            elif assistant_speaking or tool_in_progress:
                if assistant_speaking:
                    # Sustained user speech tracking during assistant playback turn
                    # [FIX-1A] Raised RMS threshold from 2500 → 7500 with 350ms persistence.
                    # Normal speech on Exotel SIP is RMS 1000–2000; loud voice peaks > 6000.
                    # 7500 + 17 frames (~350ms) prevents breath and ambient line noise from
                    # interrupting Asha mid-sentence.
                    if raw_rms > 2000:
                        speech_frames += 1
                        if speech_frames >= 6:  # ~120ms sustained conversational voice
                            # [FIX-1C] Cooldown: max 1 interrupt per 2 seconds.
                            # Prevents interrupt storm (100+ events per call) that
                            # causes stutter, dead air, and latency.
                            nonlocal last_interrupt_time
                            now = time.time()
                            if (now - last_interrupt_time) < 2.0:
                                # Cooldown active — skip, just reset frames
                                speech_frames = 0
                            else:
                                last_interrupt_time = now
                                # 1. Silence handset immediately with stream_sid
                                asyncio.create_task(websocket.send_text(json.dumps({
                                    "event": "clear",
                                    "stream_sid": session.stream_sid
                                })))

                                # 2. Trigger Bedrock interruption and flag content block to discard audio output
                                session_data = bedrock_client._active_sessions.get(session_id)
                                if session_data:
                                    session_data.audio_paused = False
                                    session_data.interrupted_content_id = session_data.current_content_id

                                logger.info("[INTERRUPT] User speech detected (RMS=%.1f). Cleared handset buffer and triggered interruption.", raw_rms)
                                user_speaking = True
                                speech_frames = 0
                                silence_frames = 0
                    else:
                        speech_frames = max(0, speech_frames - 1)
                else:
                    # Reset VAD state during background tool runs
                    user_speaking = False
                    speech_frames = 0
                    silence_frames = 0
            else:
                # VAD logic when idle (listening to user)
                turn_t = latency_telemetry.get_or_create_turn(session_id, turn_index)
                if raw_rms > 400:
                    speech_frames += 1
                    silence_frames = 0
                    if speech_frames >= 3:  # ~60ms of continuous voice
                        if not user_speaking:
                            turn_t.mark_t0()  # T0 = caller speech detected
                        user_speaking = True
                elif user_speaking:
                    if silence_frames == 0:
                        turn_t.mark_t1()  # T1 = end of speech (silence onset)
                    silence_frames += 1
                    target_eot_frames = _get_eot_threshold(speech_frames)
                    if silence_frames >= target_eot_frames and speech_frames >= MIN_SPEECH_FRAMES_TO_COMMIT:
                        logger.info(
                            "[VAD] User finished speaking (%dms silence, %d speech frames). Triggering end of turn.",
                            silence_frames * 20,
                            speech_frames
                        )
                        # [PILLAR 0] Mark T2 (turn committed) and T3 (Bedrock request dispatched)
                        turn_t.mark_t2()
                        turn_t.mark_t3()
                        # Send contentEnd to trigger Bedrock completion response
                        await session.end_audio_content()
                        # Reset VAD state
                        user_speaking = False
                        speech_frames = 0
                        silence_frames = 0
                    elif silence_frames >= 20 and speech_frames < MIN_SPEECH_FRAMES_TO_COMMIT:
                        # Noise gate: reset without triggering (background noise / dead air)
                        logger.debug(
                            "[VAD] Noise gate: only %d speech frames (need %d). Resetting without EOT.",
                            speech_frames, MIN_SPEECH_FRAMES_TO_COMMIT
                        )
                        user_speaking = False
                        speech_frames = 0
                        silence_frames = 0
            
            # Apply Noise Gate & Auto-Gain before AI ingestion
            hardened_pcm = hardener.process_chunk(pcm_samples)
            await session.stream_audio(hardened_pcm)
        except Exception:
            logger.exception("Error processing incoming audio chunk")

    # -----------------------------------------------------------------------
    # Receive loop - process Exotel WebSocket messages
    # -----------------------------------------------------------------------
    try:
        while True:
            msg = await websocket.receive()

            if msg["type"] == "websocket.disconnect":
                break

            # All Exotel messages come as JSON text frames
            if "text" in msg and msg["text"]:
                try:
                    data = json.loads(msg["text"])
                    event_type = data.get("event")

                    if event_type == "connected":
                        logger.info("Exotel connected event received")

                    elif event_type == "start":
                        start_data = data.get("start", {})
                        # 1. Resolve Hospital ID from WS query params or start event
                        hospital_id = (
                            websocket.query_params.get("HospitalId") 
                            or websocket.query_params.get("hospital_id")
                            or start_data.get("hospital_id")
                            or os.environ.get("HOSPITAL_ID", "default_tier2")
                        )
                        session.hospital_id = hospital_id
                        
                        call_sid = (
                            start_data.get("call_sid")
                            or start_data.get("callSid")
                            or data.get("call_sid")
                            or data.get("callSid")
                            or ws_call_sid  # fallback from /incoming-call query params
                            or ""
                        )
                        session.call_sid = call_sid
                        session.stream_sid = (
                            data.get("stream_sid")
                            or data.get("streamSid")
                            or start_data.get("stream_sid")
                            or start_data.get("streamSid")
                            or ""
                        )
                        if session.stream_sid:
                            logger.info("Exotel stream_sid set for session %s", session.stream_sid)
                            safe_background_task(flush_pending_audio_outputs(), name="flush_pending_audio")

                        # Extract caller phone - try WS start data, then /incoming-call query param
                        caller_phone = (
                            start_data.get("from")
                            or start_data.get("From")
                            or start_data.get("caller_number")
                            or ws_call_from  # fallback from /incoming-call query params
                            or ""
                        )

                        logger.info(
                            "Exotel stream started - streamSid: %s, callSid: %s, caller: %s, raw start keys: %s",
                            session.stream_sid,
                            call_sid,
                            mask_phone(caller_phone),
                            list(start_data.keys()),
                        )

                        call_start_time = datetime.now(timezone.utc)

                        # Send pre-recorded greeting immediately so caller hears Asha speak first
                        greeting_pcm = response_cache.get_audio("greeting")
                        if greeting_pcm:
                            logger.info(
                                "Loaded greeting.pcm (%d bytes) for caller %s",
                                len(greeting_pcm),
                                mask_phone(caller_phone),
                            )
                            if not session.stream_sid:
                                logger.warning(
                                    "Attempting to send greeting before stream_sid is available; Exotel may ignore this media event"
                                )

                            exotel_greeting = pcm_to_exotel(greeting_pcm)
                            greeting_b64 = base64.b64encode(exotel_greeting).decode("utf-8")
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "stream_sid": session.stream_sid,
                                "media": {"payload": greeting_b64}
                            }))
                            logger.info(
                                "Sent initial greeting audio to Exotel (stream_sid=%s, bytes=%d)",
                                session.stream_sid,
                                len(exotel_greeting),
                            )
                        else:
                            logger.info("greeting.pcm not in cache/disk — dynamic conversational greeting via Nova Sonic.")

                        # Build system prompt - enrich with memory context if available (parallelized)
                        ist = timezone(timedelta(hours=5, minutes=30))
                        today_ist = datetime.now(ist).strftime("%d %B %Y")
                        system_prompt = SYSTEM_PROMPT.replace("{{TODAY_DATE}}", today_ist)
                        
                        # Strip out the DEMO STABILITY section when not in demo mode
                        if not DEMO_MODE:
                            system_prompt = re.sub(
                                r"## DEMO STABILITY \(FOR PRESENTATIONS\).*?\n+---",
                                "",
                                system_prompt,
                                flags=re.DOTALL
                            )
                        
                        # Sandbox Transparency (Requirement: 1-line disclosure)
                        if tenant_status == "sandbox":
                            sandbox_notice = "\n\n[SYSTEM NOTICE: This AI is currently in SANDBOX/TESTING mode. You MUST disclose this by starting your first response with: 'Hello, this is Asha, the AI assistant currently in testing mode for your hospital.']"
                            system_prompt += sandbox_notice
                            logger.info("[SANDBOX] Injected testing disclosure for %s", hospital_id)
                        
                        returning_caller_ctx = ""
                        if memory_manager and caller_phone:
                            memory_manager.register_session(session_id, caller_phone)
                            try:
                                returning_caller_ctx = memory_manager.get_returning_caller_context(session_id)
                            except Exception:
                                logger.exception("Error getting returning caller context")

                        # [MED-06] Use asyncio.Event instead of busy-poll for stream readiness.
                        # nova_client sets session._stream_ready when Bedrock stream is open.
                        session_data = bedrock_client._active_sessions.get(session_id)
                        if session_data and hasattr(session_data, "_stream_ready"):
                            try:
                                await asyncio.wait_for(
                                    session_data._stream_ready.wait(), timeout=30.0
                                )
                            except asyncio.TimeoutError:
                                logger.error("Bedrock stream not ready after 30s - aborting session")
                                break
                        else:
                            # Fallback: lightweight poll (max 30s) for backward compatibility
                            for _ in range(60):
                                if bedrock_client._active_sessions.get(session_id) and \
                                   bedrock_client._active_sessions[session_id].stream is not None:
                                    break
                                await asyncio.sleep(0.5)
                            else:
                                logger.error("Bedrock stream not ready after 30s - aborting session")
                                break

                        # Now set up Nova session
                        # 3. Setup system prompt (with memory and caller phone context)
                        if caller_phone:
                            masked_caller = mask_phone(caller_phone)
                            system_prompt += f"\n\nCaller Phone: {masked_caller}\n(When confirming appointment, ask whether to use this calling number or a different mobile number.)\n"

                        if returning_caller_ctx:
                            system_prompt = build_system_prompt_with_memory(system_prompt, returning_caller_ctx)
                            logger.info("[MEMORY] Injected returning caller context for %s", mask_phone(caller_phone))

                        # [D-06] CRITICAL: promptStart MUST be sent before contentStart (system prompt).
                        await session.setup_prompt_start()
                        await session.setup_system_prompt(system_prompt=system_prompt)
                        # Explicitly prime initial audio content block for incoming speech
                        await session.setup_start_audio()
                        # [FIX HIGH-01] Do NOT re-send hello_audio_bytes here.
                        # The greeting was already sent to Exotel at line ~1000 (before Nova was ready).
                        # Sending it again via stream_audio() causes a double greeting for the caller.

                        idle_monitor_task = safe_background_task(
                            idle_monitor(),
                            name=f"idle_monitor_{session_id}",
                            on_error_msg="Clinical silence monitor error",
                        )
                        logger.info("Nova session setup complete, clinical idle monitor started")

                    elif event_type == "media":
                        media_data = data.get("media", {})
                        payload = media_data.get("payload", "")
                        if payload:
                            try:
                                raw_bytes = base64.b64decode(payload)
                                pcm_samples = exotel_to_pcm(raw_bytes)
                                await process_incoming_audio(pcm_samples)
                            except Exception:
                                logger.exception("Error processing Exotel media payload")

                    elif event_type == "stop":
                        stop_data = data.get("stop", {})
                        logger.info(
                            "Exotel stream stop - reason: %s, call_sid: %s",
                            stop_data.get("reason", "unknown"),
                            stop_data.get("call_sid", ""),
                        )
                        break

                    elif data.get("type") == "chat":
                        is_prod = os.environ.get("ENVIRONMENT", "development").lower() == "production"
                        chat_token = data.get("token", "")
                        admin_key = os.environ.get("ADMIN_API_KEY", "")
                        is_authenticated = bool(admin_key and chat_token and hmac.compare_digest(chat_token, admin_key))

                        if is_prod and not is_authenticated:
                            logger.warning("[AUTH] Blocked unauthenticated chat message injection in production environment.")
                        elif not DEMO_MODE and not is_authenticated:
                            logger.warning("[AUTH] Chat message rejected: DEMO_MODE is false and no valid admin token provided.")
                        else:
                            text_input = data.get("text", "")
                            logger.info("[DIAGNOSTIC] Received test text input: %s", text_input)
                            
                            lang = detect_language(text_input)
                            instruction = LANGUAGE_INSTRUCTIONS[lang]
                            combined_text = f"{instruction}\nUser Query: {text_input}"
                            logger.info("Real-time language injection (chat): %s (combined text: '%s')", lang, combined_text)
                            
                            safe_background_task(bedrock_client.send_text_message(session_id, combined_text), name="send_chat_text")

                except json.JSONDecodeError:
                    logger.exception("Error parsing Exotel JSON")
                except Exception:
                    logger.exception("Error handling Exotel message")

            # Fallback: raw binary frame
            elif "bytes" in msg and msg["bytes"]:
                try:
                    pcm_samples = exotel_to_pcm(msg["bytes"])
                    await process_incoming_audio(pcm_samples)
                except Exception:
                    logger.exception("Error processing Exotel audio frame")

    except WebSocketDisconnect:
        logger.info("Exotel client disconnected.")
    finally:
        if idle_monitor_task:
            idle_monitor_task.cancel()
        if "task_initiate" in locals() and not task_initiate.done():
            task_initiate.cancel()
        # Save transcript on disconnect
        if not transcript_saved:
            save_transcript(
                caller_phone, session_id, transcripts, call_start_time
            )
            transcript_saved = True
        # Clean up memory manager session
        if memory_manager:
            memory_manager.cleanup_session(session_id)
        
        # Clean up latency telemetry
        latency_telemetry.remove_session(session_id)
        
        # Trigger AI Analytics Processor (Post-call Data Science)
        # [FIX LOW-06] Guard against call_start_time being None if 'start' event never arrived
        if transcripts and call_start_time:
            # Run in background to not block the WebSocket closure
            # [COST-01] Collect token usage accumulated during the session
            token_usage = bedrock_client.get_usage(session_id)
            task = asyncio.create_task(analytics_processor.process_call(
                session_id=session_id,
                phone=caller_phone,
                hospital_id=session.hospital_id,
                transcript=transcripts,
                # [LOW FIX] Use utc on both sides for consistent timezone math
                duration=int((datetime.now(timezone.utc) - call_start_time).total_seconds()),
                token_usage=token_usage,
            ))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        await session.close()
        async with _session_lock:
            session_map.pop(session_id, None)

if __name__ == "__main__":
    import uvicorn
    # Use 0.0.0.0 for container compatibility, but 127.0.0.1 is fine for local verification
    uvicorn.run(app, host="0.0.0.0", port=8000)
