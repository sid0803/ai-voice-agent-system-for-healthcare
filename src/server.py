"""FastAPI server with Exotel integration for Nova Sonic speech-to-speech AI."""

import asyncio
import base64
import json
import os
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Response
from fastapi.responses import PlainTextResponse

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup - console + file
# ---------------------------------------------------------------------------
import pathlib
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.DEBUG,
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
from src.audio_utils import exotel_to_pcm, pcm_to_exotel, AudioHardener
from src.memory_manager import AgentCoreMemoryManager, build_system_prompt_with_memory
from src.routing.intent_router import intent_router
from src.cache.response_cache import response_cache

logger = logging.getLogger(__name__)

# Track active background tasks to ensure safe shutdown
_background_tasks = set()

# ---------------------------------------------------------------------------
# Security & Concurrency Utilities
# ---------------------------------------------------------------------------
_session_lock = asyncio.Lock()

def mask_phone(phone: str) -> str:
    """Mask phone number for privacy (PII protection)."""
    if not phone:
        return "unknown"
    p = str(phone).strip()
    if len(p) < 7:
        return p
    return f"{p[:3]}******{p[-4:]}"

# ---------------------------------------------------------------------------
# Greeting audio (read once at module level)
# ---------------------------------------------------------------------------
hello_audio_bytes = (_PROJECT_ROOT / "assets" / "hello.pcm").read_bytes()

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
EXOTEL_API_BASE = f"https://{exotel_subdomain}/v1/Accounts/{exotel_sid}"

# Exotel HTTP client
exotel_http = httpx.AsyncClient(
    auth=(exotel_api_key, exotel_api_token),
    timeout=30.0,
)

from src.transcript_store import save_transcript
from src.analytics.processor import analytics_processor
from src.analytics.rds_client import rds_analytics


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are Asha, a professional, efficient, and empathetic female hospital receptionist representing the InDiiServe Nova Sonic Voice Agent for Healthcare, speaking on a voice call.

## IDENTITY & ROLE
You are an AI receptionist named Asha. You exclusively help callers with healthcare services at InDiiServe Healthcare: booking appointments, checking doctor availability, report status, and hospital information. Your goal is to be helpful while ensuring patient safety through quick escalation when needed.

---

## GREETING
When the conversation FIRST starts or user says hi/hello at the BEGINNING:
- If you have PREVIOUS CONVERSATION CONTEXT with the caller's name, greet them personally: "Hello [Name], welcome back to InDiiServe Healthcare! This is Asha. How can I assist you today?"
- If this is a new caller (no context), say: "Hello, welcome to InDiiServe Healthcare! This is Asha. How can I help you today?"
Only greet ONCE at the start.

---

## SAFETY & EMERGENCY (ABSOLUTE PRIORITY)
If the caller mentions any of the following symptoms or signs of emergency:
- Chest pain, difficulty breathing, severe bleeding, unconsciousness, severe injury, or potential stroke.
- If they say "Help" or "Emergency" urgently.
- If they insist on speaking to a doctor or human staff immediately.

ACTION: 
1. Say: "I understand this is an emergency. Please stay on the line, I am transferring you to our emergency medical staff right now."
2. IMMEDIATELY call the handoffTool.
3. Once the handoffTool is called, STOP speaking. Do NOT attempt to give medical advice or diagnosis.

---

## LANGUAGE (CRITICAL)
- You support English, Hindi, and **Hinglish** (a mix of both).
- Reply in the same language/style the caller uses.
- If the caller speaks in Hindi, you should respond in Hindi.
- If the caller uses a mix (Hinglish like "Appointment book karna hai"), you should respond in natural Hinglish (e.g., "Ji sure, main aapka appointment book karne mein help kar sakti hoon").
- Ensure your Hindi/Hinglish is polite and formal ("Aap", "Ji").

---

## RESPONSE STYLE
- Maximum 2 short sentences per response. 
- Be professional, warm, and concise.
- Never repeat greetings or transfer messages.
- ADDRESS the caller by their first name if known from context.

---

## SCOPE & TOOLS
InDiiServe Healthcare services only. If the caller asks for legal, financial, or non-hospital info, politely decline.

---

## PROACTIVE BOOKING (CRITICAL)
Your goal is to fill the hospital's schedule.
- Whenever you give availability info (e.g., "Dr. Sen is available at 10 AM"), you MUST immediately add: "Should I go ahead and book this slot for you?"
- If the patient says "Yes", "Ji", or "Theek hai", move immediately to Information Gathering.

---

## INFORMATION GATHERING
Collect details one by one (ask only what is missing):
1. Name: "May I know your name, please?"
2. Department/Doctor: "Which department or doctor are you looking for?"
3. Date/Time: "For which date and what time would you like to visit?"
   - DATE VALIDATION: Today is {{TODAY_DATE}}. Do NOT accept past dates.
4. Symptom/Intent: "Can you briefly tell me the reason for the visit? (This helps us prepare for your checkup)."

---

## BOOKING CONFIRMATION & NOTEDOWN
- After gathering all details, say:
  "Thank you [Name]. I have noted your request for [Doctor/Dept] on [Date] at [Time]. I am recording these details in our system now and we will send a confirmation to your WhatsApp."
- The `appointmentBookingTool` will be called internally to save this data.

---

## SCOPE ENFORCEMENT
If the request is outside hospital scope, say: "I apologize, I can only help with hospital-related services. Would you like to check doctor availability?"
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
    logger.info("[MEMORY] Initialized with ID: %s", memory_id)
else:
    logger.warning("[MEMORY] No MEMORY_ID configured - memory features disabled")

# ---------------------------------------------------------------------------
# Idle timeout configuration
# ---------------------------------------------------------------------------
IDLE_TIMEOUT_SECONDS = 25  # Send idle check after 25s of silence
HANGUP_GRACE_SECONDS = 15 # Hang up if no response within 15s after follow-up

# ---------------------------------------------------------------------------
# Session map
# ---------------------------------------------------------------------------
session_map: dict = {}

# ---------------------------------------------------------------------------
# FastAPI lifespan: startup tasks + SIGTERM graceful shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle server startup and graceful shutdown (SIGTERM from Docker/ECS)."""
    # --- STARTUP ---
    logger.info("[STARTUP] InDiiServe Asha Voice Agent starting...")
    try:
        if rds_analytics.host and "your_aws_rds_endpoint" not in rds_analytics.host.lower() and "mock" not in rds_analytics.host.lower():
            rds_analytics.init_schema()
            logger.info("[STARTUP] RDS analytics schema verified/created.")
        else:
            logger.info("[STARTUP] RDS initialization skipped (Mock/Offline mode).")
    except Exception:
        logger.warning("[STARTUP] RDS init_schema failed - analytics writes will be skipped.")

    yield  # Server is running and handling requests

    # --- SHUTDOWN (triggered by SIGTERM from container orchestrator) ---
    logger.info("[SHUTDOWN] SIGTERM received. Closing %d active sessions...", len(session_map))
    async with _session_lock:
        close_tasks = [session.close() for session in list(session_map.values())]
    if close_tasks:
        await asyncio.gather(*close_tasks, return_exceptions=True)
        
    if _background_tasks:
        logger.info("[SHUTDOWN] Waiting for %d pending background tasks...", len(_background_tasks))
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    
    # Close global HTTP client
    await exotel_http.aclose()
    logger.info("[SHUTDOWN] Exotel HTTP client closed. All sessions cleaned up. Exiting.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(lifespan=lifespan)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Silence favicon 404 logs."""
    return Response(status_code=204)


@app.get("/")
async def root():
    """Root route returning JSON status message."""
    return {"message": "Exotel Media Stream Server is running!"}


@app.get("/health")
async def health():
    """Health check endpoint for AWS load balancers, ECS, and App Runner."""
    return {
        "status": "healthy",
        "active_sessions": len(session_map),
        "service": "InDiiServe-Asha-Voice-Agent",
    }


@app.get("/incoming-call")
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

    # Append call metadata as query params so the WebSocket handler gets them
    if call_sid or call_from:
        params = []
        if call_sid:
            params.append(f"CallSid={call_sid}")
        if call_from:
            params.append(f"CallFrom={call_from}")
        ws_url = f"{ws_url}?{'&'.join(params)}"

    logger.info("Incoming call - CallSid: %s, CallFrom: %s, returning WS URL: %s", call_sid, mask_phone(call_from), ws_url)
    return {"url": ws_url}


@app.api_route("/outbound-call", methods=["GET", "POST"])
async def outbound_call(request: Request):
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
async def failover(request: Request):
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


@app.websocket("/exotel-stream")
async def exotel_stream(websocket: WebSocket):
    """WebSocket route for Exotel voice bot applet connections.

    Exotel protocol:
    - JSON text frames for 'start' and 'stop' events
    - Raw binary frames for PCM audio (16-bit signed LE, 8kHz, mono)
    """
    await websocket.accept()
    logger.info("Exotel client connected")

    # Extract call metadata from WebSocket URL query params
    # (passed from /incoming-call endpoint)
    ws_call_sid = websocket.query_params.get("CallSid", "")
    ws_call_from = websocket.query_params.get("CallFrom", "")
    if ws_call_sid or ws_call_from:
        logger.info("WS query params - CallSid: %s, CallFrom: %s", ws_call_sid, ws_call_from)

    # Create a session for this connection
    session_id = str(uuid4())
    session = bedrock_client.create_stream_session(session_id)
    async with _session_lock:
        session_map[session_id] = session

    # Initiate the Bedrock stream in the background.
    # initiate_session runs forever (_process_response_stream is a while-loop),
    # so we fire-and-forget but poll for session.stream to be ready before setup.
    asyncio.ensure_future(bedrock_client.initiate_session(session_id))

    call_sid = ""
    hardener = AudioHardener()

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

    # -----------------------------------------------------------------------
    # Idle timeout state
    # -----------------------------------------------------------------------
    last_activity_time = time.time()
    idle_prompt_sent = False
    idle_monitor_task = None
    detected_language = "en"
    tool_in_progress = False

    def reset_idle_timer():
        nonlocal last_activity_time, idle_prompt_sent
        last_activity_time = time.time()
        idle_prompt_sent = False

    async def send_idle_followup():
        """Send follow-up via cross-modal text input - Nova will respond with audio."""
        nonlocal idle_prompt_sent, last_activity_time
        if idle_prompt_sent or not call_sid:
            return
        idle_prompt_sent = True
        logger.info("Idle timeout reached - sending cross-modal text to Nova")
        try:
            await bedrock_client.send_text_message(
                session_id,
                "[The caller has been silent for a while. Ask them if they are still there and if they need help with their appointment booking.]"
            )
            last_activity_time = time.time()
            logger.info("Cross-modal text sent successfully, waiting for Nova to generate audio")
        except Exception:
            logger.exception("Error sending idle follow-up text")

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

    async def idle_monitor():
        """Background task: check for idle timeout and hangup grace period."""
        try:
            while True:
                await asyncio.sleep(5)
                if tool_in_progress:
                    continue
                elapsed = time.time() - last_activity_time

                if not idle_prompt_sent and elapsed >= IDLE_TIMEOUT_SECONDS:
                    await send_idle_followup()
                elif idle_prompt_sent and elapsed >= HANGUP_GRACE_SECONDS:
                    await hangup_call()
                    nonlocal transcript_saved
                    if not transcript_saved:
                        save_transcript(
                            caller_phone, session_id, transcripts, call_start_time
                        )
                        transcript_saved = True
                    return
        except asyncio.CancelledError:
            pass

    # -----------------------------------------------------------------------
    # Register Nova Sonic event handlers
    # -----------------------------------------------------------------------

    def _handle_audio_output(data):
        """Decode base64 PCM from Nova, convert via pcm_to_exotel(), send as base64 JSON media event."""
        async def _send():
            try:
                pcm_bytes = base64.b64decode(data["content"])
                exotel_bytes = pcm_to_exotel(pcm_bytes)
                payload_b64 = base64.b64encode(exotel_bytes).decode("utf-8")
                await websocket.send_text(json.dumps({
                    "event": "media",
                    "stream_sid": session.stream_sid,
                    "media": {"payload": payload_b64}
                }))
            except Exception:
                logger.exception("Error sending audio to Exotel")
        asyncio.ensure_future(_send())

    def _handle_content_end(data):
        """Send clear event as JSON text frame on interruption."""
        async def _send():
            try:
                if data.get("stopReason") == "INTERRUPTED":
                    await websocket.send_text(json.dumps({"event": "clear"}))
            except Exception:
                logger.exception("Error sending clear to Exotel")
        asyncio.ensure_future(_send())

    def _handle_tool_use(data):
        """Log tool invocation and pause idle timer."""
        nonlocal tool_in_progress
        tool_in_progress = True
        logger.info("Tool called: %s", data.get("toolName", "unknown"))

    def _handle_text_output(data):
        nonlocal detected_language, current_user_text, current_assistant_text
        content = str(data.get("content", ""))
        role = data.get("role", "")
        logger.info("Text output [%s]: %s", role, content[:80])

        # Dedup key - defined before any branch so it's always available
        dedup_key = (role, content)
        is_new = content.strip() and dedup_key not in seen_transcript_entries

        # Store transcript (deduplicate)
        if is_new:
            seen_transcript_entries.add(dedup_key)
            transcripts.append({"role": role, "content": content})

        if role == "USER" and len(content.strip()) > 2:
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
                        asyncio.ensure_future(stream_cached_audio(cached_audio))
            # --- END OPTIMIZATION ---

            hinglish_keywords = ["hai", "kare", "kaise", "booking", "chahiye", "mera", "mujhe", "aap", "karna", "sunye"]
            if any("\u0900" <= ch <= "\u097F" for ch in content) or any(kw in content.lower() for kw in hinglish_keywords):
                detected_language = "hi"
            else:
                detected_language = "en"

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
        logger.error("Error in session: %s", data)

    def _handle_tool_result(data):
        nonlocal tool_in_progress
        tool_in_progress = False
        logger.info("Tool result received")
        reset_idle_timer()

    def _handle_stream_complete():
        logger.info("Stream completed for client: %s", session.stream_sid)

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

    session.on_event("audioOutput", _handle_audio_output)
    session.on_event("contentEnd", _handle_content_end)
    session.on_event("toolUse", _handle_tool_use)
    session.on_event("textOutput", _handle_text_output)
    session.on_event("error", _handle_error)
    session.on_event("toolResult", _handle_tool_result)
    session.on_event("streamComplete", _handle_stream_complete)

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
                            or "default_tier2"
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
                        session.stream_sid = (
                            data.get("stream_sid")
                            or data.get("streamSid")
                            or start_data.get("stream_sid")
                            or start_data.get("streamSid")
                            or ""
                        )

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

                        # Send greeting audio IMMEDIATELY so Exotel hears something
                        # before the Nova session setup (which takes time)
                        greeting_b64 = base64.b64encode(hello_audio_bytes).decode("utf-8")
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "stream_sid": session.stream_sid,
                            "media": {"payload": greeting_b64}
                        }))
                        logger.info("Greeting audio sent to Exotel (%d bytes PCM)", len(hello_audio_bytes))

                        # Build system prompt - enrich with memory context if available (parallelized)
                        ist = timezone(timedelta(hours=5, minutes=30))
                        today_ist = datetime.now(ist).strftime("%d %B %Y")
                        system_prompt = SYSTEM_PROMPT.replace("{{TODAY_DATE}}", today_ist)
                        
                        memory_context_task = None
                        if memory_manager and caller_phone:
                            memory_manager.register_session(session_id, caller_phone)
                            memory_context_task = asyncio.create_task(memory_manager.retrieve_context(session_id))

                        # Wait for Bedrock stream to be ready before sending setup events.
                        # initiate_session runs forever (response loop), so we poll
                        # for session.stream to be set (means connection + sessionStart done).
                        for _ in range(60):  # up to ~30s
                            if bedrock_client._active_sessions.get(session_id) and \
                               bedrock_client._active_sessions[session_id].stream is not None:
                                break
                            await asyncio.sleep(0.5)
                        else:
                            logger.error("Bedrock stream not ready after 30s - aborting session")
                            break

                        # Now set up Nova session
                        # 3. Setup system prompt (with memory if task finished)
                        if memory_context_task:
                            try:
                                # Wait a max of 2s for memory to avoid stalling the call
                                memory_context = await asyncio.wait_for(memory_context_task, timeout=2.0)
                                if memory_context:
                                    system_prompt = build_system_prompt_with_memory(system_prompt, memory_context)
                                    logger.info("[MEMORY] Using personalized prompt for %s", caller_phone)
                            except (asyncio.TimeoutError, Exception):
                                logger.warning("[MEMORY] Context retrieval timed out or failed, using base prompt.")

                        await session.setup_system_prompt(system_prompt=system_prompt)
                        await session.setup_start_audio()
                        await session.stream_audio(hello_audio_bytes)

                        idle_monitor_task = asyncio.ensure_future(idle_monitor())
                        logger.info("Nova session setup complete, idle monitor started")

                    elif event_type == "media":
                        media_data = data.get("media", {})
                        payload = media_data.get("payload", "")
                        if payload:
                            try:
                                raw_bytes = base64.b64decode(payload)
                                pcm_samples = exotel_to_pcm(raw_bytes)
                                # Apply Noise Gate & Auto-Gain before AI ingestion
                                hardened_pcm = hardener.process_chunk(pcm_samples)
                                await session.stream_audio(hardened_pcm)
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

                except json.JSONDecodeError:
                    logger.exception("Error parsing Exotel JSON")
                except Exception:
                    logger.exception("Error handling Exotel message")

            # Fallback: raw binary frame
            elif "bytes" in msg and msg["bytes"]:
                try:
                    pcm_samples = exotel_to_pcm(msg["bytes"])
                    hardened_pcm = hardener.process_chunk(pcm_samples)
                    await session.stream_audio(hardened_pcm)
                except Exception:
                    logger.exception("Error processing Exotel audio frame")

    except WebSocketDisconnect:
        logger.info("Exotel client disconnected.")
    finally:
        if idle_monitor_task:
            idle_monitor_task.cancel()
        # Save transcript on disconnect
        if not transcript_saved:
            save_transcript(
                caller_phone, session_id, transcripts, call_start_time
            )
            transcript_saved = True
        # Clean up memory manager session
        if memory_manager:
            memory_manager.cleanup_session(session_id)
        
        # Trigger AI Analytics Processor (Post-call Data Science)
        if transcripts:
            # Run in background to not block the WebSocket closure
            task = asyncio.create_task(analytics_processor.process_call(
                session_id=session_id,
                phone=caller_phone,
                hospital_id=session.hospital_id,
                transcript=transcripts,
                duration=int((datetime.now(IST) - call_start_time.astimezone(IST)).total_seconds()) if call_start_time else 0
            ))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        await session.close()
        async with _session_lock:
            session_map.pop(session_id, None)
