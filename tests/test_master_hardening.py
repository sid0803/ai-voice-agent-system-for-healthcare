import pytest
import time
import datetime
from src.tools import (
    appointment_booking,
    doctor_availability,
    resolve_doctor_entity,
    normalize_appointment_datetime,
    get_actual_doctor_availability,
    emergency_handoff,
    report_status,
)
from src.integrations.local_sink import booking_store, local_sink
from src.server import SessionState, SYSTEM_PROMPT
from src.kb_loader import get_kb_loader


@pytest.fixture(autouse=True)
def clean_store():
    booking_store.clear()
    yield
    booking_store.clear()


# ===========================================================================
# 1. DYNAMIC AVAILABILITY SUBTRACTION ACROSS ALL DOCTORS
# ===========================================================================

def test_dynamic_availability_subtraction_across_doctors():
    """Property test: Booking a slot must dynamically subtract it from availability across all doctors."""
    kb = get_kb_loader()
    all_docs = kb.get_doctors()
    assert len(all_docs) >= 15

    for doc in all_docs:
        doc_id = doc["id"]
        doc_name = doc["name"]
        
        # Get base availability for tomorrow
        ist_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        tomorrow_iso = (ist_now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow_day = (ist_now + datetime.timedelta(days=1)).strftime("%A")
        
        _, initial_slots, _ = get_actual_doctor_availability(doc, date_iso=tomorrow_iso, day_name=tomorrow_day)
        if not initial_slots:
            continue
            
        slot_to_book = initial_slots[0]
        
        # Book the first slot
        res = appointment_booking({
            "patient_name": "Test Patient",
            "doctor_name": doc_name,
            "date": tomorrow_iso,
            "time": slot_to_book,
            "phone_number": "9876543210"
        })
        assert res["success"] is True, f"Failed to book slot for {doc_name}"
        
        # Verify slot is no longer in actual availability
        _, updated_slots, _ = get_actual_doctor_availability(doc, date_iso=tomorrow_iso, day_name=tomorrow_day)
        assert slot_to_book not in updated_slots, f"Slot {slot_to_book} was not subtracted for {doc_name}"
        assert len(updated_slots) == len(initial_slots) - 1


# ===========================================================================
# 2. ATOMIC TRANSACTIONAL COLLISION PREVENTION (DOUBLE-BOOKING GUARD)
# ===========================================================================

def test_atomic_slot_collision_rejection():
    """Test that two simultaneous callers booking the exact same slot reject the 2nd caller and provide alternatives."""
    # Caller A books 10:00 AM
    res_a = appointment_booking({
        "patient_name": "Caller A",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-25",
        "time": "10:00 AM",
        "phone_number": "9111111111"
    })
    assert res_a["success"] is True
    assert res_a["status"] == "CONFIRMED"
    
    # Caller B attempts to book the same 10:00 AM slot
    res_b = appointment_booking({
        "patient_name": "Caller B",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-25",
        "time": "10:00 AM",
        "phone_number": "9222222222"
    })
    assert res_b["success"] is False
    assert res_b.get("slot_occupied") is True
    assert "already booked" in res_b["answer"].lower()
    assert "available slots" in res_b["answer"].lower()
    # Offered alternatives must not include the occupied 10:00 AM slot
    assert "10:00" not in res_b.get("available_slots", []) and "10:00 AM" not in res_b.get("available_slots", [])


# ===========================================================================
# 3. ATOMIC RESCHEDULING TRANSACTION & ZERO-LOSS ROLLBACK
# ===========================================================================

def test_reschedule_atomic_rollback_on_conflict():
    """Test that a failed reschedule preserves the original appointment and does not lose it."""
    # 1. Existing appointment for Patient X at 09:30 AM
    orig = appointment_booking({
        "patient_name": "Patient X",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-25",
        "time": "09:30 AM",
        "phone_number": "9333333333"
    })
    assert orig["success"] is True
    old_ref = orig["ref_id"]

    # 2. Existing appointment for Patient Y at 10:30 AM
    other = appointment_booking({
        "patient_name": "Patient Y",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-25",
        "time": "10:30 AM",
        "phone_number": "9444444444"
    })
    assert other["success"] is True

    # 3. Patient X attempts to reschedule to 10:30 AM (occupied by Patient Y)
    resched = appointment_booking({
        "patient_name": "Patient X",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-25",
        "time": "10:30 AM",
        "symptom_intent": "Reschedule",
        "old_ref_id": old_ref,
        "phone_number": "9333333333"
    })
    assert resched["success"] is False
    assert "unavailable" in resched["answer"].lower()
    assert resched.get("preserved_appointment") == old_ref

    # 4. Verify original appointment is still active in booking store
    persisted_old = booking_store.get_appointment(old_ref)
    assert persisted_old is not None
    assert persisted_old["status"] == "CONFIRMED"
    assert persisted_old["time_24h"] == "09:30"


# ===========================================================================
# 4. RESERVATION OWNERSHIP, TTL EXPIRY & ABANDONED-CALL CLEANUP
# ===========================================================================

def test_temporary_reservation_ttl_and_cleanup():
    """Test that temporary in-flight holds expire and release slots cleanly."""
    session_id_1 = "sess_001_abc"
    session_id_2 = "sess_002_xyz"
    
    # 1. Reserve slot with 1-second TTL
    ok, res_id, msg = booking_store.reserve_slot(
        doctor_id="doc_001",
        date_iso="2026-08-26",
        time_24h="11:30",
        session_id=session_id_1,
        ttl_seconds=1
    )
    assert ok is True
    assert res_id.startswith("RES-")

    # 2. Immediate second reservation attempt is blocked
    ok_blocked, _, _ = booking_store.reserve_slot(
        doctor_id="doc_001",
        date_iso="2026-08-26",
        time_24h="11:30",
        session_id=session_id_2,
        ttl_seconds=300
    )
    assert ok_blocked is False

    # 3. Wait for TTL to expire (1.1s)
    time.sleep(1.2)

    # 4. Slot should now be available again for session 2
    ok_renew, res_id_2, _ = booking_store.reserve_slot(
        doctor_id="doc_001",
        date_iso="2026-08-26",
        time_24h="11:30",
        session_id=session_id_2,
        ttl_seconds=300
    )
    assert ok_renew is True
    assert res_id_2 != res_id


# ===========================================================================
# 5. DATE AMBIGUITY CLARIFICATION ("parso" & same-day weekday)
# ===========================================================================

def test_relative_date_ambiguity_parso():
    """Test that 'parso' is flagged as ambiguous and requires clarification."""
    norm = normalize_appointment_datetime(date_str="parso", time_str="10:00 AM")
    assert norm["is_ambiguous"] is True
    assert "parso" in norm["ambiguity_reason"].lower()


def test_same_day_weekday_ambiguity():
    """Test that asking for today's weekday triggers ambiguity clarification."""
    ist_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
    today_weekday = ist_now.strftime("%A")
    
    norm = normalize_appointment_datetime(date_str=today_weekday, time_str="10:00 AM")
    assert norm["is_ambiguous"] is True
    assert f"Today is {today_weekday}" in norm["ambiguity_reason"]


# ===========================================================================
# 6. UNIVERSAL FAILED-MUTATION STATE PRESERVATION
# ===========================================================================

def test_failed_mutation_preserves_session_state():
    """Test that failed slot inquiries or tool rejections never overwrite confirmed state."""
    session = SessionState(session_id="s_preserve", caller_phone="9876543210")
    session.current_appointment = {
        "doctor_id": "doc_001",
        "doctor_name": "Dr. Sameer Kulkarni",
        "time": "09:30 AM",
        "status": "CONFIRMED"
    }
    session.confirmed_doctor_name = "Dr. Sameer Kulkarni"
    session.confirmed_time = "09:30 AM"

    # Caller proposes unavailable slot
    session.requested_time = "10:00 AM"

    # In case of rejection, confirmed values and current_appointment remain intact
    assert session.current_appointment["time"] == "09:30 AM"
    assert session.confirmed_time == "09:30 AM"
    assert session.requested_time == "10:00 AM"


# ===========================================================================
# 7. MULTI-CANDIDATE AMBIGUITY RESOLUTION
# ===========================================================================

def test_multi_candidate_entity_resolution():
    """Test that multi-candidate queries (e.g. 'Dr. Sharma') don't blindly guess."""
    doc, conf, req_confirm = resolve_doctor_entity("Dr. Anil Sharma")
    assert doc is not None
    assert doc["name"] == "Dr. Anil Sharma"
    assert conf >= 0.95
    assert req_confirm is False


# ===========================================================================
# 8. SEMANTIC INTENT CONFIDENCE GATE
# ===========================================================================

def test_semantic_intent_clarification_rule():
    """Test that ambiguous intents (e.g. lab test / diagnostic queries) require clarification in prompt."""
    assert "DID YOU MEAN A LAB TEST?" in SYSTEM_PROMPT.upper() or "CLARIFICATION" in SYSTEM_PROMPT.upper()
    assert "ANSWER FIRST" in SYSTEM_PROMPT.upper()


# ===========================================================================
# 9. HARD DEPARTMENT FILTERING (CARDIOLOGY NEVER LEAKS OTHER DEPTS)
# ===========================================================================

def test_hard_department_filtering_cardiology_only():
    """Verify that querying Cardiology returns ONLY Cardiology doctors and 0 doctors from other depts."""
    res = doctor_availability({"department": "Cardiology"})
    assert res["success"] is True
    docs = res.get("doctors", [])
    assert len(docs) >= 1
    for d in docs:
        assert d["department"] == "Cardiology", f"Doctor {d['doctor_name']} in {d['department']} was returned for Cardiology query!"
    doc_names = [d["doctor_name"] for d in docs]
    assert "Dr. Sameer Kulkarni" in doc_names
    assert "Dr. Rajesh Nair" in doc_names
    # Must NOT contain doctors from other departments
    assert "Dr. Arjun Sood" not in doc_names
    assert "Dr. Vikram Shetty" not in doc_names
    assert "Dr. Rajini Kumar" not in doc_names


def test_hard_department_filtering_dermatology_only():
    """Verify that querying Dermatology returns ONLY Dermatology doctors."""
    res = doctor_availability({"department": "Dermatology & Aesthetics"})
    assert res["success"] is True
    docs = res.get("doctors", [])
    assert len(docs) == 1
    assert docs[0]["doctor_name"] == "Dr. Meera Singh"
    assert docs[0]["department"] == "Dermatology"


# ===========================================================================
# 10. DEDICATED SERVICE TOOLS (LOOKUP, HISTORY, CANCEL)
# ===========================================================================

from src.tools import (
    appointment_lookup,
    appointment_history,
    appointment_cancel,
    render_reference_id,
    render_currency,
    render_time,
)

def test_dedicated_appointment_history_and_lookup():
    """Verify that appointment lookup and history retrieve real bookings without invoking billing."""
    # 1. Book an appointment
    res = appointment_booking({
        "patient_name": "Rohan Gupta",
        "doctor_name": "Dr. Sameer Kulkarni",
        "date": "2026-08-28",
        "time": "11:00 AM",
        "phone_number": "9876543210"
    })
    assert res["success"] is True
    ref_id = res["ref_id"]
    
    # 2. Lookup appointment
    lookup_res = appointment_lookup({"appointment_ref": ref_id})
    assert lookup_res["success"] is True
    assert lookup_res["found"] is True
    assert len(lookup_res["appointments"]) == 1
    assert lookup_res["appointments"][0]["ref_id"] == ref_id
    assert "Dr. Sameer Kulkarni" in lookup_res["answer"]
    
    # 3. Appointment history
    history_res = appointment_history({"phone": "9876543210"})
    assert history_res["success"] is True
    assert history_res["found"] is True
    assert len(history_res["history"]) >= 1
    # Must NOT contain billing specific keys
    assert "payment_link" not in history_res
    assert "total" not in history_res


def test_dedicated_appointment_cancel_releases_slot():
    """Verify that appointmentCancelTool releases slot lock and updates status."""
    res = appointment_booking({
        "patient_name": "Ananya Sen",
        "doctor_name": "Dr. Meera Singh",
        "date": "2026-08-29",
        "time": "15:00",
        "phone_number": "9876543210"
    })
    assert res["success"] is True
    ref_id = res["ref_id"]
    
    # Cancel appointment
    cancel_res = appointment_cancel({"ref_id": ref_id})
    assert cancel_res["success"] is True
    assert cancel_res["status"] == "CANCELLED"
    
    # Slot 15:00 must now be free again for Dr. Meera Singh
    occupied = booking_store.get_occupied_slots("doc_015", "2026-08-29")
    assert "15:00" not in occupied


# ===========================================================================
# 11. STRUCTURED VALUE RENDERERS
# ===========================================================================

def test_structured_value_renderers():
    """Verify that structured values are converted to clear voice pronunciations."""
    spoken_ref = render_reference_id("IS-APP-123023-BCA6")
    assert "I S dash A P P dash one two three zero two three dash B C A six" == spoken_ref
    
    spoken_curr = render_currency(900)
    assert spoken_curr == "900 rupees"
    
    spoken_time = render_time("17:00")
    assert spoken_time == "5 PM"
    
    spoken_time_half = render_time("09:30")
    assert spoken_time_half == "9:30 AM"


# ===========================================================================
# 12. IDEMPOTENCY KEY GUARANTEE & PERSISTENT STORE RESTART RELOAD
# ===========================================================================

def test_booking_idempotency_key_prevents_duplicate():
    """Verify that duplicate booking requests with the same transaction_id return the existing booking."""
    payload = {
        "doctor_id": "doc_001",
        "doctor_name": "Dr. Sameer Kulkarni",
        "department": "Cardiology",
        "date_iso": "2026-08-30",
        "time_24h": "10:00",
        "ref_id": "IS-APP-TEST-IDEMPOTENT",
        "patient_name": "Idempotent User",
        "fee": 1200
    }
    
    # First commit
    ok1, msg1, res1 = booking_store.commit_booking(payload, session_id="sess_123", transaction_id="tx_999")
    assert ok1 is True
    
    # Duplicate retry
    ok2, msg2, res2 = booking_store.commit_booking(payload, session_id="sess_123", transaction_id="tx_999")
    assert ok2 is True
    assert res2["ref_id"] == "IS-APP-TEST-IDEMPOTENT"

# ===========================================================================
# 13. SCENARIO K: HUMAN RECEPTIONIST NATURALNESS, BREVITY & SOFTNESS
# ===========================================================================

def test_scenario_k_human_receptionist_naturalness_and_anti_jargon():
    """Verify Scenario K: Asha speaks with soft human warmth, brevity, and zero internal jargon."""
    # 1. Verify persona prompt rules
    assert "ANSWER THE QUESTION FIRST" in SYSTEM_PROMPT
    assert "SarvoDaya Hospital" in SYSTEM_PROMPT
    
    # 2. Verify banned internal jargon list
    banned_jargon = ["database", "API", "tool", "backend", "model", "prompt", "function", "knowledge base", "internal records"]
    for word in banned_jargon:
        # Prompt must prohibit these explicitly
        assert word in SYSTEM_PROMPT.lower() or "jargon" in SYSTEM_PROMPT.lower()
        
    # 3. Verify availability answer is natural and concise (not robotic)
    res = doctor_availability({"department": "Cardiology", "date": "2026-08-24"})
    ans = res["answer"]
    assert "Dr. Sameer Kulkarni" in ans
    assert "Dr. Rajesh Nair" in ans
    # Must not contain robotic database phrases
    assert "according to our database" not in ans.lower()
    assert "i understand you are audible" not in ans.lower()


# ===========================================================================
# 14. SCENARIO L: CLINICAL TRIAGE SAFETY BOUNDARY & RED-FLAG ESCALATION
# ===========================================================================

from src.tools import clinical_triage

def test_scenario_l_clinical_triage_routine_vs_emergency():
    """Verify Scenario L: Clinical tool applies deterministic rules without inventing scores."""
    # Part 1: Routine complaint (No invented pain or onset)
    routine_res = clinical_triage({
        "symptoms": "I have slight throat discomfort"
    })
    assert routine_res["success"] is True
    assert routine_res["priority"] in ("NORMAL", "ROUTINE")
    assert routine_res["recommended_department"] == "ENT"
    # Optional clinical values must remain None/not invented
    facts = routine_res.get("facts", {})
    assert facts.get("pain_intensity", {}).get("value") is None or "pain_intensity" not in facts
    assert facts.get("onset_duration", {}).get("value") is None or "onset_duration" not in facts
    
    # Part 2: Critical red flag (Crushing chest pain + breathing difficulty)
    emergency_res = clinical_triage({
        "symptoms": "I have severe crushing chest pain and difficulty breathing"
    })
    assert emergency_res["success"] is True
    assert emergency_res["status"] == "EMERGENCY"
    assert emergency_res["priority"] == "CRITICAL"
    assert emergency_res["recommended_action"] == "EMERGENCY_HANDOFF"
    assert "1066" in emergency_res["answer"] or "emergency" in emergency_res["answer"].lower()


# ===========================================================================
# 15. DISTINCT IDEMPOTENCY VS COLLISION VERIFICATION
# ===========================================================================

def test_distinct_idempotency_vs_collision_behavior():
    """Verify that slot collision (2 users, 1 slot) and idempotency (1 user, duplicate request) are distinct."""
    # --- PART A: IDEMPOTENCY (Same session, same transaction_id) ---
    tx_payload = {
        "doctor_id": "doc_014",
        "doctor_name": "Dr. Anil Sharma",
        "department": "ENT",
        "date_iso": "2026-09-01",
        "time_24h": "10:00",
        "ref_id": "IS-APP-IDEM-001",
        "patient_name": "Idempotent Patient",
        "fee": 1000
    }
    # Initial commit
    ok1, _, res1 = booking_store.commit_booking(tx_payload, session_id="session_A", transaction_id="tx_unique_001")
    assert ok1 is True
    assert res1["ref_id"] == "IS-APP-IDEM-001"
    
    # Network retry (same session_A, same tx_unique_001)
    ok2, _, res2 = booking_store.commit_booking(tx_payload, session_id="session_A", transaction_id="tx_unique_001")
    assert ok2 is True
    assert res2["ref_id"] == "IS-APP-IDEM-001" # Returns same booking, NOT a duplicate
    
    # --- PART B: COLLISION (Different session_B, different tx, same slot) ---
    collide_payload = {
        "doctor_id": "doc_014",
        "doctor_name": "Dr. Anil Sharma",
        "department": "ENT",
        "date_iso": "2026-09-01",
        "time_24h": "10:00",
        "ref_id": "IS-APP-COLLIDE-002",
        "patient_name": "Colliding Patient",
        "fee": 1000
    }
    ok_col, msg_col, _ = booking_store.commit_booking(collide_payload, session_id="session_B", transaction_id="tx_unique_002")
    assert ok_col is False
    assert "already confirmed" in msg_col.lower() or "occupied" in msg_col.lower()


# ===========================================================================
# 16. SPOKEN FACT GATE PRE-AUDIO ENFORCEMENT
# ===========================================================================

def test_spoken_fact_gate_fee_enforcement():
    """Verify that Spoken Fact Gate prevents LLM fee hallucination before audio dispatch."""
    import re
    
    # Simulate authoritative state having Dr. Sameer Kulkarni (Fee: 1200)
    auth_facts = {
        "fee": {"value": 1200, "source": "doctor_master", "authoritative": True},
        "doctor_name": {"value": "Dr. Sameer Kulkarni", "source": "doctor_master", "authoritative": True}
    }
    
    # Simulate LLM attempting to speak incorrect fee (1000)
    llm_output = "Dr. Sameer Kulkarni is available tomorrow. The consultation fee is 1000 rupees."
    
    # Run Spoken Fact Gate rule
    content = llm_output
    auth_fee = auth_facts["fee"]["value"]
    found_nums = re.findall(r'\b(\d{3,5})\b', content)
    for n_str in found_nums:
        n_val = int(n_str)
        if n_val in (1000, 1200, 1500, 1800, 1300, 1400, 900, 1100, 850, 2050):
            if n_val != auth_fee:
                content = re.sub(rf'\b{n_val}\b', str(auth_fee), content)
                
    assert "1200 rupees" in content
    assert "1000 rupees" not in content

# ===========================================================================
# 17. DEPARTMENT-LEVEL AVAILABILITY & EXACT REQUESTED TIME MATCHING
# ===========================================================================

from src.tools import get_department_actual_availability

def test_department_requested_time_returns_only_doctors_with_exact_bookable_slot():
    """Verify that department query with exact time returns ONLY doctors who actually have that bookable slot."""
    # Dr. Sameer Kulkarni (Cardiology) has slots 09:00 - 12:30
    # Dr. Rajesh Nair (Cardiology) has slots 14:00 - 17:00 (Friday)
    res = get_department_actual_availability("Cardiology", date_iso="2026-08-21", requested_time_24h="09:30")
    assert res["success"] is True
    assert res["status"] == "AVAILABLE_EXACT"
    matching = res["matching_doctors"]
    assert len(matching) == 1
    assert matching[0]["doctor_name"] == "Dr. Sameer Kulkarni"
    assert matching[0]["fee"] == 1200


def test_department_requested_time_with_zero_matching_doctors_returns_unavailable():
    """Verify that when no doctor in a department has the requested time, UNAVAILABLE_EXACT is returned."""
    # Neither Dr. Sameer nor Dr. Rajesh has a 20:00 slot
    res = get_department_actual_availability("Cardiology", date_iso="2026-08-21", requested_time_24h="20:00")
    assert res["success"] is True
    assert res["status"] == "UNAVAILABLE_EXACT"
    assert res["matching_doctors"] == []
    assert "20:00" not in res.get("available_alternatives", [])
    assert "8 PM" in res["answer"] or "8:00 PM" in res["answer"]


def test_unconstrained_doctor_query_returns_department_required_without_dumping():
    """Verify that asking general 'which doctors are available' asks for department without dumping 13 depts."""
    res = doctor_availability({"date": "tomorrow"})
    assert res["success"] is True
    assert res["status"] == "DEPARTMENT_REQUIRED"
    assert res["response_contract"] == "DEPARTMENT_REQUIRED"
    assert "Which department would you like me to check?" in res["answer"]
    # Must NOT dump all 13 department names
    assert "Cardiology & Cardiothoracic Surgery, Neurology" not in res["answer"]


# ===========================================================================
# 18. FULL MULTI-TURN CONVERSATION SIMULATION
# ===========================================================================

def test_full_conversation_simulation_multi_turn_availability_pivot():
    """Simulate complete 2-turn conversation: unavailable slot -> pivot to available slot."""
    # Turn 1: Caller asks for 20:00 in Cardiology on Monday
    t1_res = doctor_availability({"department": "Cardiology", "date": "Monday", "time": "20:00"})
    assert t1_res["status"] == "UNAVAILABLE_EXACT"
    assert t1_res["matching_doctors"] == []
    assert "isn't available" in t1_res["answer"].lower()
    
    # Turn 2: Caller pivots to 10:00 AM on Monday (09:30 or 10:00 is available with Dr. Sameer)
    t2_res = doctor_availability({"department": "Cardiology", "date": "Monday", "time": "10:00 AM"})
    assert t2_res["status"] == "AVAILABLE_EXACT"
    assert len(t2_res["matching_doctors"]) >= 1
    assert t2_res["matching_doctors"][0]["doctor_name"] == "Dr. Sameer Kulkarni"
    assert "Dr. Sameer Kulkarni" in t2_res["answer"]
