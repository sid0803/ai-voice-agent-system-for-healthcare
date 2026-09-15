import os
import shutil
import tempfile
import pytest
from src.memory_manager import LocalFileMemoryManager, IdentityState


@pytest.fixture
def temp_memory_manager():
    temp_dir = tempfile.mkdtemp()
    temp_file = os.path.join(temp_dir, "test_patient_memory.json")
    mgr = LocalFileMemoryManager(filepath=temp_file)
    
    # Seed initial patient Arun
    pid = mgr.create_or_update_patient_profile(
        name="Arun Adhikari",
        phone="9876543210",
        doctor_name="Dr. Vikram Shetty",
        dept="Orthopedics",
        appointment_ref="IS-APP-085649"
    )
    # Seed prior interaction
    mgr._session_meta["seed_session"] = {
        "caller_phone": "9876543210",
        "candidate_patient_id": pid,
        "authorized_patient_id": pid,
        "identity_state": IdentityState.IDENTITY_VERIFIED,
    }
    import asyncio
    asyncio.run(mgr.save_interaction("seed_session", "I need orthopedic doctor", "Booked with Dr. Vikram Shetty"))
    
    yield mgr, pid, "9876543210"
    
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_known_patient_known_phone_verification(temp_memory_manager):
    mgr, pid, phone = temp_memory_manager
    session_id = "session_known_phone"
    mgr.register_session(session_id, phone)
    
    # State should start as PHONE_MATCH_ONLY with candidate_patient_id set but authorized_patient_id None
    meta = mgr._session_meta[session_id]
    assert meta["candidate_patient_id"] == pid
    assert meta["authorized_patient_id"] is None
    assert meta["identity_state"] == IdentityState.PHONE_MATCH_ONLY
    assert mgr.retrieve_authorized_context(session_id) == ""  # Zero access before verification

    # Confirm identity with name
    verified, state, auth_pid = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Arun Adhikari"}
    )
    assert verified is True
    assert state == IdentityState.IDENTITY_VERIFIED
    assert auth_pid == pid
    assert meta["authorized_patient_id"] == pid

    # Context should now be accessible
    context = mgr.retrieve_authorized_context(session_id)
    assert "Arun Adhikari" in context
    assert "Orthopedics" in context
    assert "IS-APP-085649" in context


def test_old_patient_new_phone_with_ref_id(temp_memory_manager):
    mgr, pid, _ = temp_memory_manager
    new_phone = "9123456780"
    session_id = "session_new_phone"
    mgr.register_session(session_id, new_phone)

    meta = mgr._session_meta[session_id]
    assert meta["candidate_patient_id"] is None
    assert meta["identity_state"] == IdentityState.UNKNOWN

    # Candidate discovery
    candidates = mgr.search_patient_candidates(name="Arun Adhikari")
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand["candidate_patient_id"] == pid
    assert cand["verification_required"] is True

    # Verification with Ref ID
    verified, state, auth_pid = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Arun Adhikari", "appointment_ref": "IS-APP-085649"}
    )
    assert verified is True
    assert state == IdentityState.IDENTITY_VERIFIED
    assert auth_pid == pid
    assert new_phone in mgr._patients[pid]["verified_phones"]


def test_old_patient_new_phone_unverified_withholds_data(temp_memory_manager):
    mgr, pid, _ = temp_memory_manager
    new_phone = "9123456780"
    session_id = "session_unverified"
    mgr.register_session(session_id, new_phone)

    # Caller claims to be Arun but has NO Ref ID
    verified, state, auth_pid = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Arun Adhikari", "appointment_ref": ""}
    )
    assert verified is False
    assert state == IdentityState.IDENTITY_PENDING_VERIFICATION
    assert auth_pid is None
    assert mgr.retrieve_authorized_context(session_id) == ""  # Zero access


def test_identity_mismatch_privacy_shield(temp_memory_manager):
    mgr, pid, phone = temp_memory_manager
    session_id = "session_mismatch"
    mgr.register_session(session_id, phone)

    # Priya calls from Arun's phone
    verified, state, auth_pid = mgr.verify_caller_identity(
        session_id=session_id,
        candidate_patient_id=pid,
        verification_proof={"name": "Priya Sharma"}
    )
    assert verified is False
    assert state == IdentityState.IDENTITY_MISMATCH
    assert auth_pid is None
    assert mgr.retrieve_authorized_context(session_id) == ""  # 100% suppressed Arun data


def test_caretaker_flow(temp_memory_manager):
    mgr, pid, _ = temp_memory_manager
    session_id = "session_caretaker"
    mgr.register_session(session_id, "9999999999")

    # Caretaker without Ref ID
    state, auth_id = mgr.handle_caretaker_flow(session_id, "Arun Adhikari", appointment_ref=None)
    assert state == IdentityState.CARETAKER_CONTEXT
    assert auth_id is None
    assert mgr.retrieve_authorized_context(session_id) == ""

    # Caretaker WITH valid Ref ID
    state, auth_id = mgr.handle_caretaker_flow(session_id, "Arun Adhikari", appointment_ref="IS-APP-085649")
    assert state == IdentityState.CARETAKER_CONTEXT
    assert auth_id == pid
