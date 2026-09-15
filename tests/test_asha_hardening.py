import os
import json
import pytest
from src.memory_manager import LocalFileMemoryManager, IdentityState
from src.tools import appointment_booking, search_patient, emergency_handoff, doctor_availability, hospital_info
from src.server import SYSTEM_PROMPT, SessionState


def test_hospital_rebranding_in_prompt_and_kb():
    """Verify SarvoDaya Hospital is the patient-facing entity."""
    assert "SarvoDaya Hospital" in SYSTEM_PROMPT
    assert "Indiiserve Healthcare" not in SYSTEM_PROMPT
    
    with open("data/unified_hospital_kb.json", "r", encoding="utf-8") as f:
        kb_data = json.load(f)
    assert kb_data["core_info"]["name"] == "SarvoDaya Hospital"
    assert "सर्वोदय" in kb_data["core_info"]["address_hi"]


def test_session_state_security_boundary():
    """Verify candidate_patient_id vs authorized_patient_id separation."""
    session = SessionState(session_id="test-123", caller_phone="9876543210")
    assert session.candidate_patient_id is None
    assert session.authorized_patient_id is None
    assert session.identity_status == "UNKNOWN"

    # Simulate discovery
    session.candidate_patient_id = "PAT-001"
    session.identity_status = IdentityState.CANDIDATE_PATIENT
    # authorized_patient_id must remain None until verified
    assert session.authorized_patient_id is None


def test_search_patient_candidate_discovery():
    """Verify searchPatientTool returns minimal candidate tokens without medical notes."""
    result = search_patient({"name": "Arun"})
    # Tool output contract
    assert "success" in result
    assert "candidates" in result
    for cand in result.get("candidates", []):
        assert "candidate_patient_id" in cand
        assert "candidate_name" in cand
        assert "verification_required" in cand
        # Must NOT expose sensitive clinical history
        assert "recent_interactions" not in cand
        assert "medical_history" not in cand


def test_flexible_booking_by_department():
    """Verify booking tool accepts department and automatically resolves specialist."""
    result = appointment_booking({
        "patient_name": "Arun Adhikari",
        "doctor_dept": "Cardiology",
        "date": "2026-08-22",
        "time": "11:00 AM",
        "phone_number": "9876543210",
    })
    assert result["success"] is True
    assert result["status"] == "CONFIRMED"
    assert result["booking_id"].startswith("IS-APP-")
    assert result["patient_name"] == "Arun Adhikari"
    assert result["date"] == "2026-08-22"
    assert result["time"] == "11:00 AM"
    assert len(result["doctor_name"]) > 0


def test_cross_questioning_grounding():
    """Verify structured booking state answers all follow-up questions."""
    booking = appointment_booking({
        "patient_name": "Arun Adhikari",
        "doctor_name": "Dr. Vikram Shetty",
        "doctor_dept": "Orthopedics",
        "date": "2026-08-22",
        "time": "12:00 PM",
        "phone_number": "9876543210",
    })
    
    # Grounding state
    session = SessionState(session_id="test-session", caller_phone="9876543210")
    session.current_appointment = booking
    
    # Cross-questioning assertions
    assert session.current_appointment["doctor_name"] == "Dr. Vikram Shetty"
    assert session.current_appointment["department"] == "Orthopedics"
    assert session.current_appointment["date"] == "2026-08-22"
    assert session.current_appointment["time"] == "12:00 PM"
    assert session.current_appointment["status"] == "CONFIRMED"
    assert session.current_appointment["booking_id"].startswith("IS-APP-")


def test_emergency_handoff_safety():
    """Verify emergency handoff returns escalated status and 1066."""
    result = emergency_handoff({})
    assert result["status"] == "ESCALATED"
    assert "10-6-6" in result["answer"] or "emergency" in result["answer"].lower()


def test_trilingual_script_rules():
    """Verify prompt enforces 100% Devanagari Hindi, Roman Hinglish, and English."""
    assert "Devanagari" in SYSTEM_PROMPT
    assert "Hinglish in Roman" in SYSTEM_PROMPT
    assert "English Latin" in SYSTEM_PROMPT
    assert "STRICT SCRIPT ISOLATION" in SYSTEM_PROMPT


@pytest.fixture(autouse=True)
def clean_booking_store():
    from src.integrations.local_sink import booking_store
    booking_store.clear()
    yield
    booking_store.clear()
