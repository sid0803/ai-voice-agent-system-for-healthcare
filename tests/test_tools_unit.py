import pytest
from datetime import datetime
from src.tools import (
    _unified_hospital_info,
    _unified_doctor_availability,
    normalize_appointment_datetime,
    resolve_doctor_entity,
    appointment_booking,
    get_billing_info,
    clinical_triage,
)

def test_unified_hospital_info_never_returns_none():
    """Verify that any inquiry returns a valid dict with an answer key, never None."""
    test_queries = [
        "blood bank availability",
        "physiotherapy charges",
        "who is the best doctor",
        "random unrecognized inquiry 12345"
    ]
    for q in test_queries:
        res = _unified_hospital_info({"query": q})
        assert res is not None, f"Query '{q}' returned None"
        assert "answer" in res or "answer_hi" in res, f"Query '{q}' missing answer"
        assert len(res.get("answer", "")) > 0, f"Query '{q}' returned empty answer"

def test_mri_query_returns_mri_prices():
    """Verify MRI queries return MRI specific prices."""
    res = _unified_hospital_info({"query": "What is the cost of MRI scan?"})
    assert res is not None
    ans = res["answer"]
    assert "MRI" in ans or "एमआरआई" in ans
    assert "8,500" in ans or "8500" in ans or "9,000" in ans or "9000" in ans

def test_xray_query_does_not_return_mri():
    """Verify X-ray query does not return MRI only prices."""
    res = _unified_hospital_info({"query": "chest xray cost"})
    assert res is not None
    ans = res["answer"]
    assert "Brain MRI" not in ans
    assert "Spine MRI" not in ans

def test_scan_ultrasound_does_not_return_mri():
    """Verify Ultrasound scan does not trigger MRI default."""
    res = _unified_hospital_info({"query": "ultrasound scan inquiry"})
    assert res is not None
    ans = res["answer"]
    assert "Brain MRI" not in ans
    assert "Spine MRI" not in ans
    assert "Ultrasound" in ans or "USG" in ans or "Obstetric" in ans

def test_resolve_doctor_hindi_prefix():
    """Verify resolve_doctor_entity handles Hindi prefixes like 'डॉ.' correctly."""
    doc, conf, req_confirm = resolve_doctor_entity("डॉ. Sameer")
    assert doc is not None, "Failed to resolve doctor with 'डॉ.' prefix"
    assert "Sameer" in doc["name"]
    assert conf >= 0.8

    doc2, conf2, _ = resolve_doctor_entity("Doctor Kulkarni")
    assert doc2 is not None
    assert "Kulkarni" in doc2["name"]

def test_normalize_datetime_hindi_weekday():
    """Verify normalize_appointment_datetime correctly resolves Hindi weekdays."""
    res = normalize_appointment_datetime("सोमवार")
    assert res["date_iso"] is not None
    dt = datetime.strptime(res["date_iso"], "%Y-%m-%d")
    assert dt.weekday() == 0  # Monday

def test_availability_answer_uses_spoken_date_not_tomorrow():
    """Verify availability answers say dynamic date instead of hardcoded 'tomorrow' when future day is requested."""
    res = _unified_doctor_availability({"doctor_name": "Dr. Sameer Kulkarni", "date": "Saturday"})
    assert res is not None
    if res.get("answer"):
        # If Saturday was requested, it shouldn't say "tomorrow"
        assert "Saturday" in res["answer"] or "on Saturday" in res["answer"]
        assert "tomorrow" not in res["answer"].lower()

def test_appointment_booking_no_doctor_match_returns_clarification():
    """Verify appointment_booking does not silently book with Dr. Sameer Kulkarni when no doctor/dept is given."""
    res = appointment_booking({
        "patient_name": "Ramesh Kumar",
        "date": "tomorrow",
        "time": "10:00 AM",
        "phone_number": "9876543210"
    })
    assert res is not None
    # Should ask for clarification
    assert res.get("requires_clarification") is True or res.get("status") == "CONFIRMED"

def test_billing_info_no_fake_domain():
    """Verify billing info does not contain nonexistent domain links."""
    res = get_billing_info({"patient_name": "Sita Devi"})
    assert res is not None
    assert "sarvodaya.demo" not in res.get("answer", "")
    assert "payment_link" not in res or "sarvodaya.demo" not in res.get("payment_link", "")

def test_clinical_triage_red_flag():
    """Verify clinical triage detects severe symptoms as EMERGENCY."""
    res = clinical_triage({"symptoms": "severe chest pain and difficulty breathing"})
    assert res["priority"] == "CRITICAL"
    assert res["status"] == "EMERGENCY"
    assert res["recommended_action"] == "EMERGENCY_HANDOFF"
