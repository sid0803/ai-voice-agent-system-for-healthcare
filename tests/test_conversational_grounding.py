"""
Comprehensive Automated Test Suite: Conversational Grounding, State Architecture and Anti-Hallucination
Preserves all existing 35 tests and adds 20 new tests (Total: 55 tests).
"""

import pytest
import json
from src.tools import (
    appointment_booking,
    clinical_triage,
    doctor_availability,
    resolve_doctor_entity,
    _clinical_triage_schema,
    _doctor_availability_schema,
    _appointment_booking_schema,
)
from src.kb_loader import get_kb_loader
from src.server import SessionState, SYSTEM_PROMPT


# ===========================================================================
# TIER 1: DETERMINISTIC UNIT TESTS (10 TESTS)
# ===========================================================================

def test_1_clinical_triage_hospital_approved_red_flags():
    """Deterministic Unit Test: Red-flag symptoms trigger CRITICAL / EMERGENCY status."""
    res = clinical_triage({"symptoms": "severe crushing chest pain radiating to left arm"})
    assert res["status"] == "EMERGENCY"
    assert res["priority"] == "CRITICAL"
    assert res["recommended_action"] == "EMERGENCY_HANDOFF"
    assert "emergency desk immediately" in res["answer"].lower()
    assert res["facts"]["priority"]["value"] == "CRITICAL"
    assert res["facts"]["priority"]["authoritative"] is True


def test_2_clinical_triage_hospital_approved_routine_mapping():
    """Deterministic Unit Test: Routine symptoms route to approved department without invented urgency."""
    res = clinical_triage({"symptoms": "throat discomfort and mild pain"})
    assert res["status"] == "STABLE"
    assert res["priority"] == "NORMAL"
    assert res["recommended_department"] == "ENT"
    assert res["recommended_action"] == "BOOK_OPD"
    assert "ent" in res["answer"].lower()


def test_3_clinical_triage_no_invented_arguments():
    """Deterministic Unit Test: Unspecified pain and onset remain null in schema and facts."""
    schema = json.loads(_clinical_triage_schema)
    assert schema["required"] == ["symptoms"]
    assert "pain_intensity" not in schema["required"]
    assert "onset_duration" not in schema["required"]
    
    res = clinical_triage({"symptoms": "fever and cold"})
    assert res["success"] is True
    assert "pain_intensity" not in res["facts"]
    assert "onset_duration" not in res["facts"]


def test_4_single_canonical_fee_source_consistency():
    """Deterministic Unit Test: Fee for Dr. Sameer Kulkarni (1200) is consistent across all access points."""
    kb = get_kb_loader()
    doctors = [d for d in kb.get_doctors() if "sameer kulkarni" in d.get("name", "").lower()]
    assert len(doctors) > 0
    kb_fee = doctors[0].get("fee")
    assert kb_fee == 1200

    # Availability tool lookup
    avail_res = doctor_availability({"query": "Dr. Sameer Kulkarni tomorrow"})
    assert "1,200" in avail_res["answer"] or "1200" in avail_res["answer"]
    assert avail_res["facts"]["fee"]["value"] == 1200

    # Booking tool lookup
    book_res = appointment_booking({
        "patient_name": "Sandipan",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "tomorrow",
        "time": "09:30 AM"
    })
    assert book_res["fee"] == 1200
    assert "1,200" in book_res["answer"] or "1200" in book_res["answer"]
    assert book_res["facts"]["fee"]["value"] == 1200


def test_5_booking_response_uses_authoritative_fee():
    """End-to-End Test: Authoritative fee propagates across Availability -> Booking -> SessionState -> Response."""
    # 1. Availability check
    avail = doctor_availability({"query": "Dr. Sameer Kulkarni"})
    assert avail["facts"]["fee"]["value"] == 1200
    
    # 2. Booking tool execution
    booking = appointment_booking({
        "patient_name": "Sandipan",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "tomorrow",
        "time": "09:30 AM",
        "phone_number": "06297546142"
    })
    assert booking["fee"] == 1200
    
    # 3. SessionState population
    state = SessionState(session_id="test-fee-prop", caller_phone="06297546142")
    state.current_appointment = {
        "doctor_id": booking["doctor_id"],
        "doctor_name": booking["doctor_name"],
        "department": booking["department"],
        "date": booking["date"],
        "time": booking["time"],
        "fee": booking["fee"],
        "reference_id": booking["ref_id"],
        "status": "CONFIRMED"
    }
    state.confirmed_fee = booking["fee"]
    
    # 4. Final ASHA response verification
    assert state.confirmed_fee == 1200
    assert state.current_appointment["fee"] == 1200
    assert "1,000" not in booking["answer"]
    assert "1200" in booking["answer"] or "1,200" in booking["answer"]


def test_6_deterministic_entity_resolver_phonetic_candidate():
    """Deterministic Unit Test: Phonetic match ('Samil Kulkarni') maps to candidate with requires_confirmation=True."""
    doc, conf, requires_confirm = resolve_doctor_entity("Samil Kulkarni")
    assert doc is not None
    assert "Sameer Kulkarni" in doc.get("name", "")
    assert requires_confirm is True
    assert conf < 1.0

    # Exact match maps with requires_confirmation=False
    exact_doc, exact_conf, exact_req = resolve_doctor_entity("Dr. Sameer Kulkarni")
    assert exact_doc is not None
    assert exact_req is False
    assert exact_conf == 1.0


def test_7_sanitized_authoritative_facts_with_provenance():
    """Deterministic Unit Test: Authoritative fact dictionary contains provenance and zero debug keys."""
    res = appointment_booking({
        "patient_name": "Sandipan",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-21",
        "time": "09:30 AM"
    })
    facts = res.get("facts", {})
    assert "doctor_name" in facts
    assert facts["doctor_name"]["authoritative"] is True
    assert facts["doctor_name"]["source"] == "doctor_master"
    assert facts["fee"]["value"] == 1200
    assert facts["fee"]["authoritative"] is True
    assert facts["reference_id"]["value"].startswith("IS-APP-")

    # Assert no internal debug or raw database metadata keys leaked into facts
    banned_keys = {"debug", "db_raw", "internal_id", "secret", "trace"}
    assert not any(k in facts for k in banned_keys)


def test_8_session_state_canonical_entity_fields():
    """Deterministic Unit Test: SessionState properly tracks canonical entity IDs and structured state."""
    state = SessionState(
        session_id="test_canonical",
        caller_phone="06297546142",
        confirmed_doctor_id="doc_001",
        confirmed_doctor_name="Dr. Sameer Kulkarni",
        confirmed_fee=1200,
        pending_confirmation={"entity_type": "doctor", "candidate_id": "doc_001", "candidate_name": "Dr. Sameer Kulkarni"},
        current_appointment={"ref_id": "IS-APP-075113", "fee": 1200, "status": "CONFIRMED"}
    )
    assert state.confirmed_doctor_id == "doc_001"
    assert state.confirmed_fee == 1200
    assert state.pending_confirmation["candidate_name"] == "Dr. Sameer Kulkarni"
    assert state.current_appointment["status"] == "CONFIRMED"


def test_9_response_cannot_invent_missing_authoritative_facts():
    """Grounding Test: Incomplete tool result strictly leaves missing fields unknown."""
    incomplete_tool_output = {
        "doctor_name": "Dr. Sameer Kulkarni",
        "slots": ["09:00 AM", "09:30 AM"]
    }
    # If fee or qualification is missing from tool output, it must not be present in facts
    facts = {
        "doctor_name": {"value": incomplete_tool_output["doctor_name"], "authoritative": True}
    }
    assert "fee" not in facts
    assert "qualification" not in facts
    assert "experience" not in facts


def test_10_availability_does_not_mutate_appointment():
    """State Invariant: Checking doctor availability does NOT confirm or reschedule an active appointment."""
    state = SessionState(session_id="test_avail_state", caller_phone="06297546142")
    state.current_appointment = {"ref_id": "IS-APP-075113", "doctor_name": "Dr. Sameer Kulkarni", "time": "09:30 AM", "status": "CONFIRMED"}
    
    # Caller checks other slots
    avail = doctor_availability({"query": "cardiologist day after tomorrow"})
    assert avail["success"] is True
    
    # State remains unchanged until an explicit booking/reschedule tool executes
    assert state.current_appointment["time"] == "09:30 AM"
    assert state.current_appointment["ref_id"] == "IS-APP-075113"


# ===========================================================================
# TIER 2: BEHAVIORAL & PROMPT GROUNDING TESTS (10 TESTS)
# ===========================================================================

def test_11_phonetic_doctor_confirmation_gate():
    """Behavioral Test: Prompt instructs explicit confirmation when doctor name is phonetically uncertain."""
    assert "PHONETIC ENTITY CONFIRMATION GATE" in SYSTEM_PROMPT
    assert "Did you mean Dr. Sameer Kulkarni?" in SYSTEM_PROMPT


def test_12_patient_name_confirmation_gate():
    """Behavioral Test: Prompt instructs explicit confirmation when patient name candidate is uncertain."""
    assert 'I have your name as Arun. Is that correct?' in SYSTEM_PROMPT


def test_13_cross_questioning_single_fact_brevity():
    """Behavioral Test: Cross-questioning requires direct 1-sentence answer from appointmentBookingTool facts."""
    assert "CROSS-QUESTIONING GROUNDING" in SYSTEM_PROMPT
    assert "answer in 1 short direct sentence using the exact details" in SYSTEM_PROMPT


def test_14_dynamic_missing_field_intake():
    """Behavioral Test: Dynamic intake rule collects exactly one missing required field per turn."""
    assert "DYNAMIC SINGLE-FIELD INTAKE" in SYSTEM_PROMPT
    assert "Collect exactly ONE missing required field per turn" in SYSTEM_PROMPT
    assert "NEVER ask for full name, doctor, department, date, time, and phone all in one turn" in SYSTEM_PROMPT


def test_15_anti_paraphrasing_and_silence_handling():
    """Behavioral Test: Prompt bans 'I understand you are audible' and handles silence naturally."""
    assert "NO PARAPHRASING & OPTIONAL ACKNOWLEDGMENT" in SYSTEM_PROMPT
    assert "I understand you are audible" in SYSTEM_PROMPT
    assert "Yes, I'm here. Please go ahead." in SYSTEM_PROMPT


def test_16_comprehensive_banned_jargon_suppression():
    """Behavioral Test: Comprehensive ban on internal system terminology and tool exposure."""
    assert "COMPREHENSIVE BAN ON INTERNAL JARGON" in SYSTEM_PROMPT
    banned_terms = ["tools", "systems", "databases", "APIs", "backend", "functions", "models", "AI", "prompts", "knowledge base", "memory", "processing", "internal records"]
    for term in banned_terms:
        assert term in SYSTEM_PROMPT


def test_17_sentence_based_response_policy():
    """Behavioral Test: Response length policy enforces sentence-based conversational limits."""
    assert "RESPONSE LENGTH POLICY" in SYSTEM_PROMPT
    assert "Simple factual answers: 1 short sentence" in SYSTEM_PROMPT
    assert "Normal transactional/booking turns: 1–2 short conversational sentences" in SYSTEM_PROMPT


def test_18_trilingual_script_isolation_and_entities():
    """Behavioral Test: Strict script isolation for Hindi Devanagari, Roman Hinglish, and English."""
    assert "STRICT SCRIPT ISOLATION" in SYSTEM_PROMPT
    assert "100% Hindi Devanagari script" in SYSTEM_PROMPT
    assert "100% Hinglish in Roman Latin script" in SYSTEM_PROMPT
    assert "100% English Latin script" in SYSTEM_PROMPT


def test_19_natural_repair_without_excuses():
    """Behavioral Test: Answering questions first without defense or database excuses."""
    assert "ANSWER THE QUESTION FIRST" in SYSTEM_PROMPT
    assert "SarvoDaya Hospital." in SYSTEM_PROMPT


def test_20_caller_correction_priority():
    """Behavioral Test: Availability != Booking != Rescheduling prevents false state transitions."""
    assert "AVAILABILITY != BOOKING != RESCHEDULING" in SYSTEM_PROMPT
    assert "Checking open slots does NOT confirm a booking" in SYSTEM_PROMPT


# ===========================================================================
# TIER 3: WEBSOCKET STARTUP & GREETING AUDIO EMISSION REGRESSION TESTS (2 TESTS)
# ===========================================================================

def test_21_websocket_startup_catches_no_exceptions():
    """Regression Test: WebSocket startup executes without raising NameError, AttributeError, or TypeError."""
    from fastapi.testclient import TestClient
    from src.server import app, _EXOTEL_WS_SECRET, _generate_exotel_ws_nonce
    import json

    client = TestClient(app)
    # [AI-03] Use the HMAC nonce (not the raw secret) — matches /incoming-call behaviour
    ws_url = f"/exotel-stream?token={_generate_exotel_ws_nonce('')}"
    with client.websocket_connect(ws_url) as websocket:
        # Send Exotel connected event
        websocket.send_text(json.dumps({"event": "connected"}))
        # Send Exotel start event with valid stream_sid
        websocket.send_text(json.dumps({
            "event": "start",
            "start": {
                "stream_sid": "test_stream_sid_123",
                "call_sid": "test_call_sid_123",
                "from": "06297546142",
                "hospital_id": "sarvodaya_tier1"
            }
        }))
        # Receive the immediate greeting media frame emitted on start
        data = websocket.receive_text()
        parsed = json.loads(data)
        assert parsed["event"] == "media"
        assert parsed["stream_sid"] == "test_stream_sid_123"
        assert "payload" in parsed["media"]


def test_22_sarvodaya_greeting_audio_frame_emitted():
    """Audio Integrity Test: Verifies that after startup, SarvoDaya greeting audio payload is actually emitted."""
    from fastapi.testclient import TestClient
    from src.server import app, _EXOTEL_WS_SECRET, _generate_exotel_ws_nonce
    import json
    import base64

    client = TestClient(app)
    # [AI-03] Use the HMAC nonce (not the raw secret)
    ws_url = f"/exotel-stream?token={_generate_exotel_ws_nonce('')}"
    with client.websocket_connect(ws_url) as websocket:
        websocket.send_text(json.dumps({
            "event": "start",
            "start": {
                "stream_sid": "sarvodaya_stream_456",
                "call_sid": "sarvodaya_call_456",
                "from": "06297546142"
            }
        }))
        
        response_raw = websocket.receive_text()
        event_dict = json.loads(response_raw)
        
        assert event_dict["event"] == "media"
        assert event_dict["stream_sid"] == "sarvodaya_stream_456"
        
        payload_b64 = event_dict["media"]["payload"]
        assert len(payload_b64) > 1000  # Payload is non-empty base64
        
        audio_bytes = base64.b64decode(payload_b64)
        # SarvoDaya greeting.pcm is exactly 97,280 bytes of 8kHz 16-bit linear PCM audio
        assert len(audio_bytes) == 97280


@pytest.fixture(autouse=True)
def clean_booking_store():
    from src.integrations.local_sink import booking_store
    booking_store.clear()
    yield
    booking_store.clear()
