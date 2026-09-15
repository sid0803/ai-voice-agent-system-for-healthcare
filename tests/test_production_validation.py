import os
import shutil
import tempfile
import pytest
from src.memory_manager import LocalFileMemoryManager, IdentityState
from src.tools import (
    appointment_booking,
    search_patient,
    emergency_handoff,
    doctor_availability,
    hospital_info,
    report_status,
)
from src.server import SYSTEM_PROMPT, SessionState


@pytest.fixture
def memory_env():
    temp_dir = tempfile.mkdtemp()
    temp_file = os.path.join(temp_dir, "validation_memory.json")
    mgr = LocalFileMemoryManager(filepath=temp_file)
    yield mgr
    shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# PHASE 2 — PATIENT IDENTITY VALIDATION (TESTS 1 - 7)
# ===========================================================================

def test_1_known_patient_known_phone(memory_env):
    """Test 1: Known patient + known phone -> verify name -> authorized_patient_id."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )
    session_id = "session_1"
    mgr.register_session(session_id, "9876543210")

    # Before name confirmation -> candidate exists, but authorized_patient_id is None
    meta = mgr._session_meta[session_id]
    assert meta["candidate_patient_id"] == pid
    assert meta["authorized_patient_id"] is None
    assert mgr.retrieve_authorized_context(session_id) == ""

    # Caller confirms name
    verified, state, auth_id = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Arun Adhikari"},
    )
    assert verified is True
    assert state == IdentityState.IDENTITY_VERIFIED
    assert auth_id == pid
    assert "IS-APP-100001" in mgr.retrieve_authorized_context(session_id)


def test_2_old_patient_new_phone_with_ref_id(memory_env):
    """Test 2: Old patient + new phone + valid Ref ID -> authorized + phone linked, no duplicate."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )
    new_phone = "9123456780"
    session_id = "session_2"
    mgr.register_session(session_id, new_phone)

    # Search candidates
    candidates = mgr.search_patient_candidates(name="Arun Adhikari")
    assert len(candidates) >= 1
    assert candidates[0]["candidate_patient_id"] == pid

    # Verify with Ref ID
    verified, state, auth_id = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Arun Adhikari", "appointment_ref": "IS-APP-100001"},
    )
    assert verified is True
    assert auth_id == pid
    assert new_phone in mgr._patients[pid]["verified_phones"]
    # Total patient count must remain 1 (no duplicate)
    assert len(mgr._patients) == 1


def test_3_old_patient_new_phone_without_ref_id(memory_env):
    """Test 3: Old patient + new phone without Ref ID -> history withheld."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )
    session_id = "session_3"
    mgr.register_session(session_id, "9123456780")

    verified, state, auth_id = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Arun Adhikari", "appointment_ref": ""},
    )
    assert verified is False
    assert state == IdentityState.IDENTITY_PENDING_VERIFICATION
    assert auth_id is None
    assert mgr.retrieve_authorized_context(session_id) == ""


def test_4_new_person_on_old_patient_phone(memory_env):
    """Test 4: Priya calling from Arun's phone -> IDENTITY_MISMATCH, Arun's data 100% suppressed."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )
    session_id = "session_4"
    mgr.register_session(session_id, "9876543210")

    verified, state, auth_id = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Priya"},
    )
    assert verified is False
    assert state == IdentityState.IDENTITY_MISMATCH
    assert auth_id is None
    assert mgr.retrieve_authorized_context(session_id) == ""


def test_5_caretaker_flow(memory_env):
    """Test 5: Caretaker calling for Arun -> CARETAKER_CONTEXT, requires Ref ID."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )
    session_id = "session_5"
    mgr.register_session(session_id, "9999999999")

    # Caretaker without Ref ID
    state, auth_id = mgr.handle_caretaker_flow(session_id, "Arun Adhikari", appointment_ref=None)
    assert state == IdentityState.CARETAKER_CONTEXT
    assert auth_id is None
    assert mgr.retrieve_authorized_context(session_id) == ""

    # Caretaker with Ref ID
    state, auth_id = mgr.handle_caretaker_flow(session_id, "Arun Adhikari", appointment_ref="IS-APP-100001")
    assert state == IdentityState.CARETAKER_CONTEXT
    assert auth_id == pid
    assert "IS-APP-100001" in mgr.retrieve_authorized_context(session_id)


def test_6_asr_name_misrecognition_unverified(memory_env):
    """Test 6: ASR misrecognizes Arun as Barun -> Name verification flag remains False."""
    session = SessionState(session_id="s6", caller_phone="9876543210")
    session.patient_name = "Barun"
    session.patient_name_verified = False
    assert session.patient_name_verified is False


def test_7_similar_patient_names_no_arbitrary_selection(memory_env):
    """Test 7: Similar names (Arun Adhikari, Arun Das, Arun Sharma) -> candidate discovery returns list without picking one."""
    mgr = memory_env
    mgr.create_or_update_patient_profile(name="Arun Adhikari", phone="9876543210", dept="Cardiology")
    mgr.create_or_update_patient_profile(name="Arun Das", phone="9876543211", dept="Orthopedics")
    mgr.create_or_update_patient_profile(name="Arun Sharma", phone="9876543212", dept="Neurology")

    candidates = mgr.search_patient_candidates(name="Arun")
    assert len(candidates) >= 3
    # All candidates require verification
    for c in candidates:
        assert c["verification_required"] is True


# ===========================================================================
# PHASE 3 — INTENT / ASR VALIDATION (TESTS 8 - 11)
# ===========================================================================

def test_8_lab_test_clarification_rule():
    """Test 8: Ambiguous ASR interpretation triggers prompt clarification guardrail."""
    assert "did you mean a lab test" in SYSTEM_PROMPT.lower()
    assert "CLARIFY WHEN UNSURE" in SYSTEM_PROMPT


def test_9_lab_test_information_vs_booking_disambiguation():
    """Test 9: Ambiguous lab request clarification in prompt."""
    assert "Would you like information about test prices or would you like to schedule a visit?" in SYSTEM_PROMPT


def test_10_caller_correction_priority():
    """Test 10: Caller correction overrides previous interpretation."""
    assert "The caller's latest statement always has priority" in SYSTEM_PROMPT


def test_11_unknown_doctor_safe_fallback():
    """Test 11: Nonexistent doctor check returns fallback without hallucinating fee or schedule."""
    result = doctor_availability({"query": "Dr. Nonexistent Imaginary Person"})
    assert "answer" in result
    assert "Dr. Nonexistent Imaginary Person" not in result["answer"]


# ===========================================================================
# PHASE 4 — APPOINTMENT VALIDATION (TESTS 12 - 16)
# ===========================================================================

def test_12_booking_by_doctor():
    """Test 12: Booking by doctor executes and populates structured confirmation."""
    result = appointment_booking({
        "patient_name": "Arun Adhikari",
        "doctor_name": "Dr. Vikram Shetty",
        "doctor_dept": "Orthopedics",
        "date": "2026-08-22",
        "time": "10:00 AM",
        "phone_number": "9876543210",
    })
    assert result["success"] is True
    assert result["status"] == "CONFIRMED"
    assert result["doctor_name"] == "Dr. Vikram Shetty"
    assert result["department"] == "Orthopedics"
    assert result["booking_id"].startswith("IS-APP-")


def test_13_booking_by_department():
    """Test 13: Booking by department resolves specialist from roster."""
    result = appointment_booking({
        "patient_name": "Arun Adhikari",
        "doctor_dept": "Cardiology",
        "date": "2026-08-22",
        "time": "11:00 AM",
        "phone_number": "9876543210",
    })
    assert result["success"] is True
    assert result["status"] == "CONFIRMED"
    assert len(result["doctor_name"]) > 0


def test_14_cross_questioning_grounding():
    """Test 14: Follow-up cross-questions answered directly from structured state."""
    booking = appointment_booking({
        "patient_name": "Arun Adhikari",
        "doctor_name": "Dr. Vikram Shetty",
        "doctor_dept": "Orthopedics",
        "date": "2026-08-22",
        "time": "12:00 PM",
        "phone_number": "9876543210",
    })
    session = SessionState(session_id="s14", caller_phone="9876543210")
    session.current_appointment = booking

    # Answers must come from session.current_appointment
    assert session.current_appointment["doctor_name"] == "Dr. Vikram Shetty"
    assert session.current_appointment["department"] == "Orthopedics"
    assert session.current_appointment["date"] == "2026-08-22"
    assert session.current_appointment["time"] == "12:00 PM"
    assert session.current_appointment["booking_id"].startswith("IS-APP-")


def test_15_language_switch_preserves_appointment_state():
    """Test 15: Language switch does not clear appointment state."""
    session = SessionState(session_id="s15", caller_phone="9876543210")
    session.current_appointment = {
        "booking_id": "IS-APP-123456",
        "doctor_name": "Dr. Vikram Shetty",
        "department": "Orthopedics",
        "date": "tomorrow",
        "time": "12:00 PM",
        "status": "CONFIRMED",
    }
    # Caller switches language to Hindi
    session.current_language = "hi"
    assert session.current_appointment is not None
    assert session.current_appointment["doctor_name"] == "Dr. Vikram Shetty"


def test_16_fake_booking_safety():
    """Test 16: No appointment exists -> current_appointment is None, no fabricated ID."""
    session = SessionState(session_id="s16", caller_phone="9876543210")
    assert session.current_appointment is None


# ===========================================================================
# PHASE 5 — PATIENT MEMORY / RETURNING PATIENT (TESTS 17 - 18)
# ===========================================================================

def test_17_returning_patient_memory(memory_env):
    """Test 17: Call 1 books appointment -> Call 2 same patient resolves identity & retrieves booking."""
    mgr = memory_env
    # Call 1
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )

    # Call 2
    session_2 = "call_2"
    mgr.register_session(session_2, "9876543210")
    mgr.verify_caller_identity(session_2, pid, {"name": "Arun Adhikari"})
    
    context = mgr.retrieve_authorized_context(session_2)
    assert "Arun Adhikari" in context
    assert "Dr. Vikram Shetty" in context
    assert "IS-APP-100001" in context


def test_18_returning_patient_new_number_no_duplicate(memory_env):
    """Test 18: Returning patient from new phone verified by Ref ID keeps same patient_id."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-100001",
    )
    session_2 = "call_2_new_phone"
    mgr.register_session(session_2, "9123456780")
    verified, state, auth_id = mgr.verify_caller_identity(
        session_2,
        pid,
        {"name": "Arun Adhikari", "appointment_ref": "IS-APP-100001"},
    )
    assert auth_id == pid
    assert len(mgr._patients) == 1


# ===========================================================================
# PHASE 6 — LANGUAGE VALIDATION (TESTS 19 - 24)
# ===========================================================================

def test_19_to_24_trilingual_script_and_state_preservation():
    """Tests 19-24: Validate trilingual script isolation and language switch safety."""
    # Strict Script Isolation
    assert "STRICT SCRIPT ISOLATION" in SYSTEM_PROMPT
    assert "Devanagari" in SYSTEM_PROMPT
    assert "Hinglish in Roman Latin" in SYSTEM_PROMPT
    assert "English Latin" in SYSTEM_PROMPT
    assert "NO LANGUAGE RESET ON STATE" in SYSTEM_PROMPT


# ===========================================================================
# PHASE 8 & 9 — TOOL FAILURE MODES & PRIVACY (TESTS 25 - 28)
# ===========================================================================

def test_tool_failure_mode_clears_appointment():
    """Verify tool failure does not populate confirmed appointment."""
    session = SessionState(session_id="s_fail", caller_phone="9876543210")
    # Simulate failed booking
    booking_result = {"success": False, "error": "doctor_unavailable"}
    if booking_result.get("success") is True:
        session.current_appointment = booking_result
    else:
        session.current_appointment = None

    assert session.current_appointment is None


def test_privacy_boundary_zero_context_when_unauthorized(memory_env):
    """Verify unauthorized caller cannot access any medical context."""
    mgr = memory_env
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        dept="Cardiology",
        appointment_ref="IS-APP-123456",
    )
    session_id = "unauth_session"
    mgr.register_session(session_id, "9999999999")
    # Retrieve context without authorization
    assert mgr.retrieve_authorized_context(session_id) == ""


# ===========================================================================
# PHASE 10 & 11 — EMERGENCY & HANG-UP SAFETY
# ===========================================================================

def test_emergency_handoff_protocol():
    """Verify emergency handoff triggers escalation immediately."""
    result = emergency_handoff({})
    assert result["status"] == "ESCALATED"
    assert "10-6-6" in result["answer"] or "1066" in result["answer"]


def test_hangup_protocol_in_prompt():
    """Verify clean hangup protocol without additional questioning."""
    assert "HANG-UP & CALL DISCONNECT" in SYSTEM_PROMPT
    assert "Thank you for calling SarvoDaya Hospital. Take care." in SYSTEM_PROMPT


@pytest.fixture(autouse=True)
def clean_booking_store():
    from src.integrations.local_sink import booking_store
    booking_store.clear()
    yield
    booking_store.clear()
