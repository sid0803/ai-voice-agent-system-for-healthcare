"""Guard rails for output validation, factual accuracy, and text sanitization.

Implements the Spoken Fact Gate to prevent model hallucination regarding
consultation fees, appointment booking IDs, and unavailable appointment slots
prior to audio dispatch.
"""

import json
import logging
import pathlib
import re
from typing import Any, Optional, Set

logger = logging.getLogger(__name__)

# Default project root fallback
_DEFAULT_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_known_fees(project_root: Optional[pathlib.Path] = None) -> Set[int]:
    """Dynamically loads known consultation fees from unified_hospital_kb.json."""
    root = project_root or _DEFAULT_PROJECT_ROOT
    try:
        kb_path = root / "data" / "unified_hospital_kb.json"
        if kb_path.exists():
            kb = json.loads(kb_path.read_text(encoding="utf-8"))
            fees: Set[int] = set()
            for doc in kb.get("doctors", []):
                fee = doc.get("consultation_fee") or doc.get("fee")
                if isinstance(fee, int) and 100 <= fee <= 50000:
                    fees.add(fee)
            if fees:
                return fees
    except Exception:
        pass  # Fall through to safe default
    # Safe default — matches standard OPD consultation fees
    return {850, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1800, 2050}


_KNOWN_CONSULTATION_FEES: Set[int] = _load_known_fees()


def sanitize_spoken_text(content: str) -> str:
    """Strips list numbers, line breaks, stray brackets, and extra spaces from TTS text."""
    if not content:
        return ""
    # Strip line breaks
    content = re.sub(r'\n+', ' ', content)
    # Strip list item numbering e.g. "1. " or "1) "
    content = re.sub(r'^\s*\d+[\.\)]\s*', '', content)
    content = re.sub(r'\s+\d+[\.\)]\s*', ' ', content)
    # Strip square and curly brackets, preserving round () for e.g. (Gate 3), (₹1,200)
    content = re.sub(r'[\[\]{}]', '', content)
    content = re.sub(r'\s{2,}', ' ', content).strip()
    return content


def apply_spoken_fact_gate(content: str, session: Any, role: str) -> str:
    """Pre-Audio Factual Claim Verification (Spoken Fact Gate).
    
    Verifies that spoken fees, booking reference IDs, and slot availability match
    the authoritative state stored in session.state.
    """
    if role not in ("ASSISTANT", "assistant") or not session:
        return content

    state = getattr(session, "state", None)
    if not state:
        return content

    facts = getattr(state, "last_authoritative_facts", None) or {}

    # 1. Fee claim verification
    if "fee" in facts and facts["fee"].get("authoritative"):
        auth_fee = facts["fee"].get("value")
        if auth_fee:
            found_nums = re.findall(r'\b(\d{3,5})\b', content)
            for n_str in found_nums:
                n_val = int(n_str)
                if n_val in _KNOWN_CONSULTATION_FEES:
                    if n_val != auth_fee and abs(n_val - auth_fee) > 0:
                        logger.warning(
                            "[SPOKEN-FACT-GATE] Fee Mismatch: Spoken %d != Authoritative %d. Enforcing authoritative fee before audio.",
                            n_val, auth_fee
                        )
                        content = re.sub(rf'\b{n_val}\b', str(auth_fee), content)

    # 2. Reference ID verification
    curr_appt = getattr(state, "current_appointment", None)
    if curr_appt and curr_appt.get("ref_id"):
        auth_ref = curr_appt.get("ref_id")
        m = re.search(r'IS-APP-\d{6}-[A-Z0-9]{4}', content)
        if m and m.group(0) != auth_ref:
            logger.warning(
                "[SPOKEN-FACT-GATE] RefID mismatch: Spoken %s != Authoritative %s. Correcting.",
                m.group(0), auth_ref
            )
            content = content.replace(m.group(0), auth_ref)

    # 3. Semantic Availability Gate: Block positive claims if slot is UNAVAILABLE_EXACT
    is_unavailable = (
        facts.get("status", {}).get("value") == "UNAVAILABLE_EXACT"
        or (getattr(state, "last_tool_result", None) and state.last_tool_result.get("status") == "UNAVAILABLE_EXACT")
    )
    if is_unavailable:
        req_t = facts.get("requested_time", {}).get("value") or "that time"
        dept = facts.get("department", {}).get("value") or ""
        if any(w in content.lower() for w in ["is available", "can see you", "available at", "slot is open", "appointment at"]):
            logger.warning(
                "[SPOKEN-FACT-GATE] Blocked positive availability claim for unavailable slot %s. Enforcing safe rejection.",
                req_t
            )
            content = f"I'm sorry, {req_t} isn't available tomorrow in {dept.title()}."

    return content
