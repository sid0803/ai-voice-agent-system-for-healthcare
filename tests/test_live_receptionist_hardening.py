"""Tests for ASHA Live Receptionist Hardening (Production Release)."""

import re
import pytest
from src.tools import (
    _normalize_department_name,
    _format_slots_concise,
    _find_next_available_slot_in_range,
    get_department_actual_availability,
    tool_processor,
)
from src.kb_loader import get_kb_loader
from src.server import detect_language


def test_hindi_and_english_general_medicine_aliases():
    """Verify Hindi and English General Medicine aliases normalize canonically."""
    aliases = [
        "General Medicine",
        "general medicine",
        "Internal Medicine",
        "internal medicine",
        "physician",
        "general physician",
        "general doctor",
        "family doctor",
        "family medicine",
        "medicine doctor",
        "जनरल मेडिसिन",
        "जनरल मेडिकल",
        "दवा विभाग",
        "दवाई",
        "बुखार का डॉक्टर",
        "दवा का डॉक्टर",
        "दवाई वाले",
    ]
    for alias in aliases:
        assert _normalize_department_name(alias) == "general medicine", f"Failed for alias: {alias}"


def test_general_medicine_doctors_in_kb():
    """Verify that General Medicine doctors exist in KB and have bookable OPD schedules."""
    kb = get_kb_loader()
    docs = kb.get_doctors()
    gm_docs = [d for d in docs if _normalize_department_name(d.get("department", "")) == "general medicine"]
    assert len(gm_docs) >= 2, f"Expected at least 2 General Medicine doctors, found {len(gm_docs)}"
    
    gm_names = {d["name"] for d in gm_docs}
    assert "Dr. Arjun Mehta" in gm_names
    assert "Dr. Priya Nair" in gm_names

    # Verify department availability tool succeeds for General Medicine
    res = get_department_actual_availability("General Medicine", date_iso="2026-08-21")
    assert res["success"] is True
    assert len(res["matching_doctors"]) > 0


@pytest.mark.asyncio
async def test_general_medicine_booking_flow():
    """Verify booking with General Medicine doctor returns confirmed status and fee."""
    from src.integrations.local_sink import booking_store
    booking_store.clear()
    raw = await tool_processor(
        "appointmentbookingtool",
        '{"patient_name": "Rohan Gupta", "doctor_name": "Dr. Arjun Mehta", "date": "tomorrow", "time": "10:00", "phone": "9876543210"}'
    )
    import json
    res = json.loads(raw) if isinstance(raw, str) else raw
    assert res.get("status") == "CONFIRMED"
    assert "Arjun Mehta" in res.get("doctor_name", "")
    assert res.get("fee") == 700


def test_concise_slot_range_formatting():
    """Verify time slot lists format to natural concise spoken ranges."""
    assert _format_slots_concise([]) == "no available slots"
    assert _format_slots_concise(["09:00"]) == "9 AM"
    assert _format_slots_concise(["09:00", "09:30"]) == "9 AM and 9:30 AM"
    assert _format_slots_concise(["09:00", "09:30", "10:00", "10:30"]) == "between 9 AM and 10:30 AM"
    assert _format_slots_concise(["15:00", "15:30", "16:00", "16:30", "17:00"]) == "between 3 PM and 5 PM"


def test_proactive_next_slot_scanner():
    """Verify scanner finds the earliest bookable date/slot for a department."""
    next_slot = _find_next_available_slot_in_range("Cardiology", "2026-08-21", search_days=7)
    assert next_slot is not None
    assert "date_iso" in next_slot
    assert "doctor_name" in next_slot
    assert "available_slots" in next_slot
    assert "slots_spoken" in next_slot
    assert len(next_slot["available_slots"]) > 0


def test_hindi_gender_guard_and_bot_word_stripping():
    """Verify gender guard converts female forms AND strips robotic enthusiasm words."""
    _GENDER_REPLACEMENTS = [
        ("चाहती हैं", "चाहते हैं"),
        ("सकती हैं", "सकते हैं"),
        ("बताती हैं", "बताते हैं"),
        ("करती हैं", "करते हैं"),
        ("आना चाहती", "आना चाहते"),
        ("दिखाना चाहती", "दिखाना चाहते"),
        ("पूछना चाहती", "पूछना चाहते"),
    ]
    _BOT_WORDS_PATTERN = re.compile(
        r'^\s*(Sure thing!?|Great news!?|Good news!?|Perfect!?|Sure!?|Certainly!?|Wonderful!?|Excellent!?|बेहतरीन!?|बिल्कुल सही!?|बिल्कुल!?|ज़रूर!?|अवश्य!?|शानदार!?|बढ़िया!?|वाह!?|ज़बरदस्त!?)\s*[,।!-]?\s*',
        re.IGNORECASE | re.UNICODE
    )

    def apply_guard(text: str) -> str:
        text = _BOT_WORDS_PATTERN.sub('', text)
        text = re.sub(r'\bDr\.?\s+Dr\.?\b', 'Dr.', text, flags=re.IGNORECASE)
        text = re.sub(r'डॉ\.?\s*Dr\.?\b', 'डॉ.', text, flags=re.IGNORECASE)
        text = re.sub(r'डॉ\.?\s*डॉ\.?', 'डॉ.', text)
        text = re.sub(r'\bDoctor\s+Dr\.?\b', 'Dr.', text, flags=re.IGNORECASE)
        text = re.sub(r'\bDoctor\s+Doctor\b', 'Doctor', text, flags=re.IGNORECASE)
        text = re.sub(r'डॉक्टर\s+डॉ\.?', 'डॉक्टर', text)
        text = re.sub(r'डॉक्टर\s+Dr\.?\b', 'डॉक्टर', text, flags=re.IGNORECASE)
        text = re.sub(r'डॉ\.\.+', 'डॉ.', text)
        text = re.sub(r'Dr\.\.+', 'Dr.', text)
        for female, neutral in _GENDER_REPLACEMENTS:
            text = text.replace(female, neutral)
        return text

    # Bot words stripped
    assert apply_guard("बेहतरीन! आपका अपॉइंटमेंट कन्फर्म हो गया है।") == "आपका अपॉइंटमेंट कन्फर्म हो गया है।"
    assert apply_guard("बिल्कुल! न्यूरोलॉजी विभाग में कल डॉ. मेघा राव उपलब्ध हैं।") == "न्यूरोलॉजी विभाग में कल डॉ. मेघा राव उपलब्ध हैं।"
    assert apply_guard("Great news! Your appointment is confirmed.") == "Your appointment is confirmed."
    assert apply_guard("Sure thing! Let me check that.") == "Let me check that."

    # Double doctor prefixes collapsed
    assert apply_guard("ENT डॉ. Dr. अनिल शर्मा 2:30 PM") == "ENT डॉ. अनिल शर्मा 2:30 PM"
    assert apply_guard("appointment with Dr. Dr. Anil Sharma") == "appointment with Dr. Anil Sharma"
    assert apply_guard("डॉक्टर डॉ. सेमिर") == "डॉक्टर सेमिर"

    # Gender neutrality enforced
    sample_bad = "आप डॉक्टर से कब मिलना चाहती हैं? क्या आप आज आ सकती हैं?"
    sample_fixed = apply_guard(sample_bad)
    assert "चाहती हैं" not in sample_fixed
    assert "सकती हैं" not in sample_fixed
    assert "चाहते हैं" in sample_fixed
    assert "सकते हैं" in sample_fixed


def test_language_detection_precision():
    """Verify that common English words do not falsely trigger Hindi/Hinglish mode."""
    # Pure English with colloquial words
    assert detect_language("ya sure, that works") == "english"
    assert detect_language("hi, I need to book a consultation") == "english"
    assert detect_language("can you call me later") == "english"
    assert detect_language("how are you doing") == "english"
    assert detect_language("hello, is doctor available tomorrow") == "english"

    # Unambiguous Hindi / Hinglish
    assert detect_language("मुझे कल डॉक्टर से मिलना है") == "hindi"
    assert detect_language("doctor se appointment chahiye") == "hinglish"
    assert detect_language("kripya fee bataiye") == "hinglish"
    assert detect_language("aapka hospital kahan par hai") == "hinglish"


def test_liveness_check_detection():
    """Verify connection check phrases are distinguished from real user queries."""
    _LIVENESS_EN = [
        "am i audible", "are you there", "hello asha", "hello", "hi",
        "can you hear me", "are you listening", "still there", "you there",
        "anyone there", "asha", "hello hello", "hello asha hello"
    ]
    _LIVENESS_HI = [
        "सुन रहे हो", "क्या आप सुन रहे", "हेलो", "हाँ", "हां",
        "सुनाई दे रहा", "हो वहाँ", "आशा", "हेलो आशा", "सुन पा रहे", "आवाज़ आ रही"
    ]
    all_liveness = _LIVENESS_EN + _LIVENESS_HI

    def is_liveness(text: str) -> bool:
        normalized = text.lower().strip().rstrip("?!.,")
        if len(normalized.split()) > 6:
            return False
        return any(phrase in normalized for phrase in all_liveness)

    # Valid check-ins
    assert is_liveness("Am I audible?") is True
    assert is_liveness("Hello?") is True
    assert is_liveness("Hello Asha, are you there?") is True
    assert is_liveness("क्या आप सुन रहे हैं?") is True
    assert is_liveness("सुन रहे हो?") is True

    # Real inquiries must NOT be classified as check-ins
    assert is_liveness("I want to book an appointment with Dr. Sameer Kulkarni tomorrow at 5 PM") is False
    assert is_liveness("What is the consultation fee for cardiology?") is False
    assert is_liveness("Where is the hospital located?") is False


def test_radiology_department_and_dr_shalini_verma():
    """Verify Radiology department, Dr. Shalini Verma, and X-ray / Ultrasound services exist in KB."""
    from src.kb_loader import get_kb_loader
    kb = get_kb_loader()
    
    # 1. Department
    depts = kb.get_departments()
    dept_names = [d["name"] for d in depts]
    assert any("Radiology" in n for n in dept_names)
    
    # 2. Doctor
    docs = kb.get_doctors()
    shalini = next((d for d in docs if "Shalini Verma" in d.get("name", "")), None)
    assert shalini is not None
    assert shalini["fee"] == 900
    assert "Radiology" in shalini["department"]
    
    # 3. Services (X-Ray and Ultrasound)
    services = kb.get_services()
    svc_names = [s["name"] for s in services]
    assert any("Chest X-Ray" in s for s in svc_names)
    assert any("Ultrasound Abdomen" in s for s in svc_names)
    assert any("Obstetric Ultrasound" in s for s in svc_names)

    # 4. Tools query resolution
    from src.tools import hospital_info, doctor_availability
    xray_res = hospital_info({"query": "chest xray fee"})
    assert "350" in str(xray_res)
    
    usg_res = hospital_info({"query": "ultrasound abdomen price"})
    assert "800" in str(usg_res)
    
    doc_res = doctor_availability({"doctor_name": "Dr. Shalini Verma", "date": "2026-08-25"})
    assert doc_res.get("status") == "DOCTOR_SLOTS_AVAILABLE"
    assert doc_res.get("fee") == 900


def test_returning_caller_context_injection():
    """Verify candidate patient details are retrieved for returning phone numbers."""
    from src.memory_manager import LocalFileMemoryManager, build_system_prompt_with_memory
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    
    mem = LocalFileMemoryManager(storage_path=tmp_path)
    # Create profile
    mem.create_or_update_patient_profile(
        name="Sunil Sharma",
        phone="9876543210",
        doctor_name="Dr. Shalini Verma",
        dept="Radiology & Diagnostic Imaging",
        appointment_ref="IS-APP-998877"
    )
    
    # Register session on repeat call
    session_id = "test_repeat_session_1"
    mem.register_session(session_id, "9876543210")
    ret_ctx = mem.get_returning_caller_context(session_id)
    assert "Sunil Sharma" in ret_ctx
    assert "Dr. Shalini Verma" in ret_ctx
    assert "IS-APP-998877" in ret_ctx
    
    prompt = build_system_prompt_with_memory("Base prompt", ret_ctx)
    assert "RETURNING CALLER CONTEXT" in prompt
    assert "Sunil Sharma" in prompt


def test_expanded_bot_words_suppression():
    """Verify newly banned English/Hindi enthusiasm words are stripped."""
    from src.server import _apply_gender_guard
    
    assert _apply_gender_guard("Perfect! I can help you with that.") == "I can help you with that."
    assert _apply_gender_guard("Good news! Dr. Shalini is available.") == "Dr. Shalini is available."
    assert _apply_gender_guard("Certainly! Let me check the schedule.") == "Let me check the schedule."
    assert _apply_gender_guard("Absolutely! Booking now.") == "Booking now."
    assert _apply_gender_guard("Of course! May I have your name?") == "May I have your name?"
    assert _apply_gender_guard("शानदार! आपका अपॉइंटमेंट दर्ज हो गया है।") == "आपका अपॉइंटमेंट दर्ज हो गया है।"

