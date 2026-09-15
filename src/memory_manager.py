import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


class IdentityState:
    UNKNOWN = "UNKNOWN"
    PHONE_MATCH_ONLY = "PHONE_MATCH_ONLY"
    CANDIDATE_PATIENT = "CANDIDATE_PATIENT"
    IDENTITY_PENDING_VERIFICATION = "IDENTITY_PENDING_VERIFICATION"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CARETAKER_CONTEXT = "CARETAKER_CONTEXT"
    NEW_PATIENT = "NEW_PATIENT"


@dataclass
class PatientProfile:
    patient_id: str
    patient_name: str
    patient_name_verified: bool = False
    verified_phones: list[str] = field(default_factory=list)
    last_department: Optional[str] = None
    last_doctor: Optional[str] = None
    last_appointment_ref: Optional[str] = None
    recent_interactions: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "patient_name": self.patient_name,
            "patient_name_verified": self.patient_name_verified,
            "verified_phones": self.verified_phones,
            "last_department": self.last_department,
            "last_doctor": self.last_doctor,
            "last_appointment_ref": self.last_appointment_ref,
            "recent_interactions": self.recent_interactions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _actor_id(phone: str) -> str:
    """caller-<last 10 digits> from any phone format."""
    digits = re.sub(r"[^0-9]", "", str(phone or ""))
    if len(digits) >= 10:
        digits = digits[-10:]
    return f"caller-{digits}" if digits else "caller-unknown"


def _normalize_phone(phone: str) -> str:
    """Extract standard 10-digit phone string."""
    digits = re.sub(r"[^0-9]", "", str(phone or ""))
    return digits[-10:] if len(digits) >= 10 else digits


class AgentCoreMemoryManager:

    def __init__(self, memory_id: str, region: str = None):
        if region is None:
            region = os.getenv("MEMORY_REGION") or os.getenv("AWS_REGION") or "ap-south-1"
        self.memory_id = memory_id

        from src.nova_client import get_ec2_iam_role_credentials
        
        # AgentCore needs EC2 IAM role, not the .env Bedrock creds.
        ec2_creds = get_ec2_iam_role_credentials(timeout=2)
        
        try:
            if ec2_creds.get("aws_access_key_id"):
                session = boto3.Session(
                    region_name=region,
                    aws_access_key_id=ec2_creds.get("aws_access_key_id"),
                    aws_secret_access_key=ec2_creds.get("aws_secret_access_key"),
                    aws_session_token=ec2_creds.get("aws_session_token")
                )
                logger.info("[MEMORY] Using explicitly fetched EC2 IAM role credentials")
            else:
                session = boto3.Session(region_name=region)
                
            self.data_client = session.client("bedrock-agentcore")
            control = session.client("bedrock-agentcore-control")
        except Exception as e:
            logger.warning("[MEMORY] Failed to initialize Boto3 session explicitly: %s", e)
            raise

        self._sessions: dict[str, str] = {}  # session_id -> actor_id
        self._strategy_ids: dict[str, str] = {}

        try:
            mem = control.get_memory(memoryId=memory_id)
            for s in mem.get("memory", {}).get("strategies", []):
                self._strategy_ids[s["type"]] = s["strategyId"]
            logger.info("[MEMORY] Strategies: %s", self._strategy_ids)
        except Exception as e:
            logger.warning("[MEMORY] Could not fetch strategies: %s", e)

        logger.info("[MEMORY] Initialized with ID: %s (using IAM role)", memory_id[:40])

    def register_session(self, session_id: str, caller_phone: str) -> str:
        aid = _actor_id(caller_phone)
        self._sessions[session_id] = aid
        logger.info("[MEMORY] Registered session %s for %s (phone: %s)",
                     session_id[:8], aid, caller_phone)
        return aid

    async def retrieve_context(self, session_id: str) -> str:
        """Single retrieve call using caller-<last10> actor ID."""
        aid = self._sessions.get(session_id)
        if not aid:
            return ""

        context_parts = []
        for sid in self._strategy_ids.values():
            ns = f"/strategies/{sid}/actors/{aid}/"
            try:
                logger.info("[MEMORY] Querying: %s", ns)
                resp = self.data_client.retrieve_memory_records(
                    memoryId=self.memory_id,
                    namespace=ns,
                    searchCriteria={
                        "searchQuery": "patient name identity medical history symptoms last appointment",
                        "topK": 5,
                    },
                    maxResults=5,
                )
                for rec in resp.get("memoryRecordSummaries", []):
                    text = rec.get("content", {}).get("text", "")
                    if text:
                        context_parts.append(text)
                        logger.info("[MEMORY] Record: %s", text[:120])
            except Exception as e:
                logger.warning("[MEMORY] Retrieve failed: %s", e)

        if context_parts:
            logger.info("[MEMORY] Got %d records for %s", len(context_parts), aid)
            return "\n\n".join(context_parts)

        logger.info("[MEMORY] No context for %s (new caller)", aid)
        return ""

    async def save_interaction(self, session_id: str,
                              user_text: str, assistant_text: str) -> bool:
        aid = self._sessions.get(session_id)
        if not aid:
            return False
        try:
            await asyncio.to_thread(
                self.data_client.create_event,
                memoryId=self.memory_id,
                actorId=aid,
                sessionId=session_id,
                eventTimestamp=datetime.now(),
                payload=[
                    {"conversational": {"content": {"text": user_text}, "role": "USER"}},
                    {"conversational": {"content": {"text": assistant_text}, "role": "ASSISTANT"}},
                ],
            )
            logger.info("[MEMORY] Saved for %s", aid)
            return True
        except Exception as e:
            logger.warning("[MEMORY] Save failed for %s: %s", aid, e)
            return False

    def cleanup_session(self, session_id: str) -> None:
        aid = self._sessions.pop(session_id, None)
        if aid:
            logger.info("[MEMORY] Cleaned up session %s for %s", session_id[:8], aid)


class LocalFileMemoryManager:
    """Enterprise JSON-file backed memory manager with stable patient_id and authorization gates."""

    def __init__(self, filepath: str = "data/patient_memory.json", storage_path: Optional[str] = None):
        self.filepath = storage_path or filepath
        self._session_meta: dict[str, dict] = {}  # session_id -> metadata
        self._patients: dict[str, dict] = {}      # patient_id -> dict
        self._phone_index: dict[str, str] = {}    # normalized_phone -> patient_id
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.filepath) and os.path.getsize(self.filepath) > 0:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
            else:
                raw_data = {}
        except Exception as e:
            logger.warning("[LOCAL-MEMORY] Could not load memory file: %s", e)
            raw_data = {}

        self._patients = raw_data.get("patients", {})
        self._phone_index = raw_data.get("phone_index", {})

        # Handle legacy raw format: {"caller-9876543210": [...interactions...]}
        for key, val in raw_data.items():
            if key in ("patients", "phone_index"):
                continue
            if isinstance(val, list) and key.startswith("caller-"):
                phone = key.replace("caller-", "")
                pid = f"PAT-{phone[-6:]}" if len(phone) >= 6 else "PAT-000001"
                if pid not in self._patients:
                    self._patients[pid] = {
                        "patient_id": pid,
                        "patient_name": "Patient",
                        "patient_name_verified": False,
                        "verified_phones": [phone],
                        "last_department": None,
                        "last_doctor": None,
                        "last_appointment_ref": None,
                        "recent_interactions": val,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                    }
                    self._phone_index[phone] = pid

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            temp_path = f"{self.filepath}.tmp"
            payload = {
                "patients": self._patients,
                "phone_index": self._phone_index,
            }
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.filepath)
        except Exception as e:
            logger.warning("[LOCAL-MEMORY] Could not save memory file: %s", e)

    def lookup_by_phone(self, phone: str) -> tuple[Optional[dict], str]:
        """Check if incoming phone number matches an existing patient profile."""
        norm = _normalize_phone(phone)
        if not norm:
            return None, IdentityState.UNKNOWN
        pid = self._phone_index.get(norm)
        if pid and pid in self._patients:
            return self._patients[pid], IdentityState.PHONE_MATCH_ONLY
        return None, IdentityState.UNKNOWN

    def register_session(self, session_id: str, caller_phone: str) -> str:
        """Register session and initialize security boundaries."""
        norm_phone = _normalize_phone(caller_phone)
        candidate, state = self.lookup_by_phone(norm_phone)
        
        self._session_meta[session_id] = {
            "caller_phone": norm_phone,
            "candidate_patient_id": candidate["patient_id"] if candidate else None,
            "authorized_patient_id": None,  # Strictly None until verified
            "identity_state": state,
            "patient_name": candidate.get("patient_name") if candidate else None,
        }
        
        aid = _actor_id(caller_phone)
        logger.info(
            "[LOCAL-MEMORY] Registered session %s (Phone: %s, Candidate: %s, State: %s)",
            session_id[:8], norm_phone, self._session_meta[session_id]["candidate_patient_id"], state
        )
        return aid

    def search_patient_candidates(
        self,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        appointment_ref: Optional[str] = None
    ) -> list[dict]:
        """Candidate discovery tool contract. Strictly returns minimal verification tokens only."""
        results = []
        name_clean = name.strip().lower() if name else ""
        phone_clean = _normalize_phone(phone) if phone else ""
        ref_clean = appointment_ref.strip().upper() if appointment_ref else ""

        for pid, p in self._patients.items():
            p_name = p.get("patient_name", "").lower()
            p_phones = [_normalize_phone(ph) for ph in p.get("verified_phones", [])]
            p_ref = (p.get("last_appointment_ref") or "").upper()

            matched = False
            match_type = ""

            if ref_clean and ref_clean == p_ref:
                matched = True
                match_type = "appointment_ref"
            elif phone_clean and phone_clean in p_phones:
                matched = True
                match_type = "phone"
            elif name_clean and (name_clean in p_name or p_name in name_clean):
                matched = True
                match_type = "name"

            if matched:
                results.append({
                    "candidate_patient_id": pid,
                    "candidate_name": p.get("patient_name", "Patient"),
                    "match_type": match_type,
                    "verification_required": True,
                })

        logger.info("[PATIENT-SEARCH] Found %d candidates for query(name='%s', phone='%s', ref='%s')",
                    len(results), name, phone, appointment_ref)
        return results

    def verify_caller_identity(
        self,
        session_id: str,
        candidate_patient_id: str,
        verification_proof: dict
    ) -> tuple[bool, str, Optional[str]]:
        """Evaluate identity verification against security policy."""
        meta = self._session_meta.get(session_id)
        if not meta:
            return False, IdentityState.UNKNOWN, None

        patient = self._patients.get(candidate_patient_id)
        if not patient:
            return False, IdentityState.NEW_PATIENT, None

        caller_phone = meta.get("caller_phone", "")
        verified_phones = [_normalize_phone(p) for p in patient.get("verified_phones", [])]
        confirmed_name = (verification_proof.get("name") or "").strip().lower()
        patient_name = patient.get("patient_name", "").strip().lower()
        appointment_ref = (verification_proof.get("appointment_ref") or "").strip().upper()
        patient_ref = (patient.get("last_appointment_ref") or "").strip().upper()

        name_matches = bool(confirmed_name and (confirmed_name in patient_name or patient_name in confirmed_name))

        # Scenario A: Known Phone + Name Confirmation
        if caller_phone and caller_phone in verified_phones:
            if name_matches:
                meta["identity_state"] = IdentityState.IDENTITY_VERIFIED
                meta["authorized_patient_id"] = candidate_patient_id
                meta["patient_name"] = patient.get("patient_name")
                logger.info("[VERIFY-SUCCESS] Verified via known phone for %s", candidate_patient_id)
                return True, IdentityState.IDENTITY_VERIFIED, candidate_patient_id
            else:
                # Name mismatch on known phone (Priya calling from Arun's phone)
                meta["identity_state"] = IdentityState.IDENTITY_MISMATCH
                meta["authorized_patient_id"] = None
                logger.warning("[VERIFY-MISMATCH] Identity mismatch on phone %s", caller_phone)
                return False, IdentityState.IDENTITY_MISMATCH, None

        # Scenario B: Unlinked Phone + Appointment Ref ID Verification
        if appointment_ref and patient_ref and appointment_ref == patient_ref:
            meta["identity_state"] = IdentityState.IDENTITY_VERIFIED
            meta["authorized_patient_id"] = candidate_patient_id
            meta["patient_name"] = patient.get("patient_name")
            if caller_phone:
                self.link_phone_number(candidate_patient_id, caller_phone)
            logger.info("[VERIFY-SUCCESS] Verified via appointment ref for %s (Linked phone: %s)",
                        candidate_patient_id, caller_phone)
            return True, IdentityState.IDENTITY_VERIFIED, candidate_patient_id

        # Scenario C: Candidate found, but verification proof insufficient
        meta["identity_state"] = IdentityState.IDENTITY_PENDING_VERIFICATION
        meta["authorized_patient_id"] = None
        logger.info("[VERIFY-PENDING] Candidate %s requires appointment ref ID", candidate_patient_id)
        return False, IdentityState.IDENTITY_PENDING_VERIFICATION, None

    def handle_caretaker_flow(
        self,
        session_id: str,
        patient_name: str,
        appointment_ref: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """Caregiver / proxy caller authorization handler."""
        meta = self._session_meta.get(session_id, {})
        meta["identity_state"] = IdentityState.CARETAKER_CONTEXT
        
        candidates = self.search_patient_candidates(name=patient_name, appointment_ref=appointment_ref)
        if candidates and appointment_ref:
            cand_id = candidates[0]["candidate_patient_id"]
            p = self._patients.get(cand_id, {})
            if appointment_ref.strip().upper() == (p.get("last_appointment_ref") or "").upper():
                meta["authorized_patient_id"] = cand_id
                logger.info("[CARETAKER-AUTH] Authorized caregiver for patient %s via ref ID", cand_id)
                return IdentityState.CARETAKER_CONTEXT, cand_id

        meta["authorized_patient_id"] = None
        logger.info("[CARETAKER-UNAUTH] Caretaker for %s unverified — past history protected", patient_name)
        return IdentityState.CARETAKER_CONTEXT, None

    def handle_identity_mismatch(self, session_id: str) -> str:
        """Handle caller name conflict with registered phone."""
        meta = self._session_meta.get(session_id, {})
        meta["identity_state"] = IdentityState.IDENTITY_MISMATCH
        meta["authorized_patient_id"] = None
        logger.info("[MISMATCH] Purged authorized context for session %s", session_id[:8])
        return IdentityState.IDENTITY_MISMATCH

    def link_phone_number(self, patient_id: str, new_phone: str) -> bool:
        """Append verified phone number to an existing patient profile."""
        norm = _normalize_phone(new_phone)
        if not norm or patient_id not in self._patients:
            return False
        p = self._patients[patient_id]
        if norm not in p.get("verified_phones", []):
            p.setdefault("verified_phones", []).append(norm)
            p["updated_at"] = datetime.now().isoformat()
        self._phone_index[norm] = patient_id
        self._save()
        logger.info("[LINK-PHONE] Linked phone %s to %s", norm, patient_id)
        return True

    def create_or_update_patient_profile(
        self,
        name: str,
        phone: str,
        doctor_name: Optional[str] = None,
        dept: Optional[str] = None,
        appointment_ref: Optional[str] = None
    ) -> str:
        """Create or update canonical patient profile upon booking or intake."""
        norm_phone = _normalize_phone(phone)
        existing_pid = self._phone_index.get(norm_phone)
        
        if existing_pid and existing_pid in self._patients:
            p = self._patients[existing_pid]
            p["patient_name"] = name.strip()
            p["patient_name_verified"] = True
            if doctor_name:
                p["last_doctor"] = doctor_name
            if dept:
                p["last_department"] = dept
            if appointment_ref:
                p["last_appointment_ref"] = appointment_ref
            p["updated_at"] = datetime.now().isoformat()
            self._save()
            return existing_pid

        # Create new patient profile
        pid_num = len(self._patients) + 1
        new_pid = f"PAT-{pid_num:06d}"
        profile = PatientProfile(
            patient_id=new_pid,
            patient_name=name.strip(),
            patient_name_verified=True,
            verified_phones=[norm_phone] if norm_phone else [],
            last_department=dept,
            last_doctor=doctor_name,
            last_appointment_ref=appointment_ref,
        )
        self._patients[new_pid] = profile.to_dict()
        if norm_phone:
            self._phone_index[norm_phone] = new_pid
        self._save()
        logger.info("[CREATE-PROFILE] Created canonical profile %s for %s", new_pid, name)
        return new_pid

    def retrieve_authorized_context(self, session_id: str) -> str:
        """Resource-specific context retriever. Strictly returns history ONLY if authorized_patient_id is set."""
        meta = self._session_meta.get(session_id, {})
        auth_pid = meta.get("authorized_patient_id")
        if not auth_pid or auth_pid not in self._patients:
            return ""

        patient = self._patients[auth_pid]
        lines = [
            f"Patient ID: {patient.get('patient_id')}",
            f"Verified Name: {patient.get('patient_name')}",
        ]
        if patient.get("last_department"):
            lines.append(f"Recent Department: {patient.get('last_department')}")
        if patient.get("last_doctor"):
            lines.append(f"Recent Doctor: {patient.get('last_doctor')}")
        if patient.get("last_appointment_ref"):
            lines.append(f"Last Appointment Ref: {patient.get('last_appointment_ref')}")

        history = patient.get("recent_interactions", [])
        if history:
            lines.append("Recent Dialogue History:")
            for item in history[-3:]:
                lines.append(f"Caller: {item.get('user', '')}\nAsha: {item.get('assistant', '')}")

        return "\n".join(lines)

    async def retrieve_context(self, session_id: str) -> str:
        """Async wrapper for retrieve_authorized_context."""
        return self.retrieve_authorized_context(session_id)

    def get_returning_caller_context(self, session_id: str) -> str:
        """Returns candidate caller context from phone lookup — used to warm-greet returning callers.
        This is NOT identity-verified data. It is only used for a warm greeting offer.
        The actual records remain protected until identity is confirmed."""
        meta = self._session_meta.get(session_id, {})
        candidate_pid = meta.get("candidate_patient_id")
        if not candidate_pid or candidate_pid not in self._patients:
            return ""
        p = self._patients[candidate_pid]
        lines = []
        if p.get("patient_name"):
            lines.append(f"Candidate Patient Name: {p['patient_name']}")
        if p.get("last_doctor"):
            lines.append(f"Last Booked Doctor: {p['last_doctor']}")
        if p.get("last_department"):
            lines.append(f"Last Department: {p['last_department']}")
        if p.get("last_appointment_ref"):
            lines.append(f"Last Appointment Ref: {p['last_appointment_ref']}")
        return "\n".join(lines) if lines else ""

    async def save_interaction(self, session_id: str, user_text: str, assistant_text: str) -> bool:
        """Append conversational interaction turn."""
        meta = self._session_meta.get(session_id, {})
        pid = meta.get("authorized_patient_id") or meta.get("candidate_patient_id")
        if not pid or pid not in self._patients:
            return False
        
        patient = self._patients[pid]
        patient.setdefault("recent_interactions", []).append({
            "user": user_text,
            "assistant": assistant_text,
            "timestamp": datetime.now().isoformat()
        })
        patient["recent_interactions"] = patient["recent_interactions"][-10:]
        patient["updated_at"] = datetime.now().isoformat()
        await asyncio.to_thread(self._save)
        return True

    def cleanup_session(self, session_id: str) -> None:
        self._session_meta.pop(session_id, None)


def build_system_prompt_with_memory(base_prompt: str, memory_context: str = "") -> str:
    if not memory_context:
        return base_prompt
    return (
        f"{base_prompt}\n\n---\n\n"
        "## RETURNING CALLER CONTEXT (PHONE MATCH)\n"
        "The following details are from a prior interaction matching this calling number:\n"
        f"{memory_context}\n\n"
        "RECEPTIONIST RECOGNITION INSTRUCTION:\n"
        "If Candidate Patient Name is present, warmly recognize the returning caller and ask how to help:\n"
        "- English: 'Welcome back [Name], are you calling regarding your appointment with [Doctor] or something else?'\n"
        "- Hindi: 'सर्वोदय हॉस्पिटल में आपका फिर से स्वागत है [Name] जी, क्या आप [Doctor] के साथ अपॉइंटमेंट के बारे में बात करना चाहते हैं या कोई नई मदद चाहिए?'\n"
        "- Hinglish: 'Sarvodaya Hospital me aapka fir se swagat hai [Name] ji, kya aap [Doctor] ke appointment ke baare me call kar rahe hain ya kuch aur help chahiye?'\n"
        "Do NOT disclose sensitive medical records until caller confirms their identity.\n"
    )

