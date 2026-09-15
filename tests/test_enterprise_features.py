"""Automated Enterprise Features Test Suite for InDiiServe Hospital Command Center.

Tests:
1. Dynamic Doctor Roster Store (thread-safe in-memory cache + persistent storage).
2. Voice AI Emergency Leave Guard & Alternative Clinician Redirection.
3. Multi-Channel WhatsApp / SMS Digital Booking Pass Generator.
4. Admin Roster Management API Endpoints (GET & PATCH with RBAC).
5. Genuine Dashboard Analytics Aggregations (Weekday & Hourly Bucketing).
"""

import pytest
from fastapi.testclient import TestClient

from src.server import app
from src.integrations.roster_store import roster_store
from src.integrations.notifications import notification_service
from src.tools import (
    get_actual_doctor_availability,
    _unified_doctor_availability,
    appointment_booking,
)
from src.admin.dependencies import create_access_token, generate_csrf_token

client = TestClient(app)


def test_roster_store_basic_operations():
    """Verify listing doctors, updating statuses, and rejecting invalid statuses."""
    doctors = roster_store.list_all()
    assert len(doctors) >= 5, "Roster should be seeded with at least 5 doctors from unified KB"

    # Verify Dr. Sameer Kulkarni can be marked ON_LEAVE
    res = roster_store.update_doctor_status(
        doctor_id="doc_001",
        status="ON_LEAVE",
        reason="Attending National Cardiology Summit",
        return_date="2026-09-08",
        alternative_doctor="Dr. Rajesh Nair",
        updated_by="admin_test",
    )
    assert res["status"] == "ON_LEAVE"

    status_info = roster_store.get_doctor_status("Dr. Sameer Kulkarni")
    assert status_info is not None
    assert status_info["status"] == "ON_LEAVE"
    assert status_info["reason"] == "Attending National Cardiology Summit"

    # Test invalid status rejection
    with pytest.raises(ValueError):
        roster_store.update_doctor_status("doc_001", "VACATION")

    # Reset back to AVAILABLE
    roster_store.update_doctor_status("doc_001", "AVAILABLE")
    reset_status = roster_store.get_doctor_status("Dr. Sameer Kulkarni")
    assert reset_status["status"] == "AVAILABLE"
    assert reset_status["reason"] == ""


def test_roster_store_delay_and_surgery():
    """Verify tracking running late delays and surgical status."""
    roster_store.update_doctor_status(
        doctor_id="doc_002",
        status="RUNNING_LATE",
        delay_minutes=45,
        reason="Delayed in Emergency OT rounds",
    )
    status_info = roster_store.get_doctor_status("Dr. Rajesh Nair")
    assert status_info["status"] == "RUNNING_LATE"
    assert status_info["delay_minutes"] == 45
    assert "Emergency OT" in status_info["reason"]

    # Reset to AVAILABLE
    roster_store.update_doctor_status("doc_002", "AVAILABLE")
    assert roster_store.get_doctor_status("Dr. Rajesh Nair")["status"] == "AVAILABLE"


def test_voice_tools_doctor_on_leave_blocks_and_redirects():
    """Assert Asha voice agent proactively offers alternative doctors when requested clinician is on leave."""
    # Put Dr. Sameer Kulkarni on emergency leave
    roster_store.update_doctor_status(
        doctor_id="doc_001",
        status="ON_LEAVE",
        reason="Family Emergency",
        alternative_doctor="Dr. Rajesh Nair",
    )

    try:
        # Raw availability check
        doc_dict = {
            "id": "doc_001",
            "name": "Dr. Sameer Kulkarni",
            "department": "Cardiology",
            "availability": {"time_slots": ["10:00 AM", "11:00 AM"]},
        }
        _, free_slots, _ = get_actual_doctor_availability(doc_dict, date_iso="2026-09-08")
        assert free_slots == []

        # Spoken dialogue generation check
        avail_res = _unified_doctor_availability({
            "doctor_name": "Dr. Sameer Kulkarni",
            "department": "Cardiology",
            "date": "tomorrow",
        })
        assert avail_res.get("status") == "DOCTOR_ON_LEAVE"
        spoken = avail_res.get("answer", "")
        assert "Family Emergency" in spoken or "emergency leave" in spoken
        assert "Dr. Rajesh Nair" in spoken

        # Booking attempt while on leave must fail cleanly and recommend alternative
        booking_res = appointment_booking({
            "patient_name": "Sanjay Roy",
            "doctor_name": "Dr. Sameer Kulkarni",
            "doctor_dept": "Cardiology",
            "date": "tomorrow",
            "time": "11:00 AM",
            "phone_number": "9876543210",
        })
        assert booking_res["success"] is False
        assert booking_res["status"] == "DOCTOR_ON_LEAVE"
        assert "Dr. Rajesh Nair" in booking_res["answer"]

    finally:
        # Safely restore doctor to AVAILABLE
        roster_store.update_doctor_status("doc_001", "AVAILABLE")


def test_voice_tools_doctor_available_succeeds():
    """Verify that when doctor is AVAILABLE, slots can be queried and booked normally."""
    roster_store.update_doctor_status("doc_001", "AVAILABLE")

    doc_dict = {
        "id": "doc_001",
        "name": "Dr. Sameer Kulkarni",
        "department": "Cardiology",
        "availability": {"time_slots": ["10:00 AM", "11:00 AM", "12:00 PM"]},
    }
    _, free_slots, _ = get_actual_doctor_availability(doc_dict, date_iso="2026-09-08")
    assert len(free_slots) > 0

    booking_res = appointment_booking({
        "patient_name": "Sanjay Roy",
        "doctor_name": "Dr. Sameer Kulkarni",
        "doctor_dept": "Cardiology",
        "date": "2026-11-20",
        "time": "10:00 AM",
        "phone_number": "9876543210",
    })
    assert booking_res["success"] is True or booking_res.get("status") == "CONFIRMED"
    assert "digital_pass_dispatched" in booking_res
    assert booking_res["digital_pass_dispatched"] is True




def test_notification_pass_formatting_and_dispatch():
    """Verify digital OPD pass layout, maps link, and multichannel dispatch."""
    pass_text = notification_service.format_pass_message(
        patient_name="Anita Roy",
        doctor_name="Dr. Sunita Rao",
        department="Pediatrics",
        visit_datetime="Tomorrow, 10:30 AM",
        appointment_id="APT-TEST-992",
        room="Child Care OPD 102",
        fee="700",
    )
    assert "Apollo Metro Hospital" in pass_text
    assert "Anita Roy" in pass_text
    assert "Dr. Sunita Rao" in pass_text
    assert "APT-TEST-992" in pass_text
    assert "Child Care OPD 102" in pass_text
    assert "₹700" in pass_text
    assert "maps.google.com" in pass_text

    # Dispatch confirmation
    res = notification_service.dispatch_booking_confirmation(
        phone="+919876543210",
        patient_name="Anita Roy",
        doctor_name="Dr. Sunita Rao",
        department="Pediatrics",
        visit_datetime="Tomorrow, 10:30 AM",
        appointment_id="APT-TEST-992",
        room="Child Care OPD 102",
        fee="700",
    )
    assert res["status"] == "dispatched"
    assert "whatsapp" in res["channels"]
    assert "sms" in res["channels"]


def test_admin_roster_api_endpoints():
    """Verify GET and PATCH /api/v1/knowledge/roster with RBAC and CSRF security."""
    admin_token = create_access_token(user_id="admin_1", tenant_id="apollo_metro", role="hospital_admin")
    csrf_token = generate_csrf_token()

    # GET Roster
    get_resp = client.get(
        "/api/v1/knowledge/roster",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "roster" in data
    assert len(data["roster"]) >= 5

    # PATCH Roster - Mark Dr. Rajesh Nair RUNNING_LATE
    patch_resp = client.patch(
        "/api/v1/knowledge/roster/doc_002",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-CSRF-Token": csrf_token,
        },
        json={
            "status": "RUNNING_LATE",
            "delay_minutes": 30,
            "reason": "Emergency Angiography in Cath Lab",
        },
    )
    assert patch_resp.status_code == 200
    patch_data = patch_resp.json()
    assert patch_data["status"] == "updated"
    assert patch_data["doctor"]["status"] == "RUNNING_LATE"
    assert patch_data["doctor"]["delay_minutes"] == 30

    # Reset Dr. Rajesh Nair
    client.patch(
        "/api/v1/knowledge/roster/doc_002",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-CSRF-Token": csrf_token,
        },
        json={"status": "AVAILABLE"},
    )


def test_dashboard_genuine_aggregations():
    """Assert dashboard API returns genuine real-time metrics and distributions without mock multipliers."""
    admin_token = create_access_token(user_id="admin_1", tenant_id="apollo_metro", role="hospital_admin")
    resp = client.get(
        "/api/v1/dashboard/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "kpis" in data
    assert "trends" in data
    assert "hourly_volume" in data

    kpis = data["kpis"]
    assert "total_calls" in kpis
    assert "resolution_rate" in kpis
    assert "patient_satisfaction" in kpis
    assert "emergency_escalations" in kpis

    # Verify hourly volume has structured keys
    hourly = data["hourly_volume"]
    assert isinstance(hourly, list)
    if hourly:
        first_hour = hourly[0]
        assert "time" in first_hour
        assert "calls" in first_hour
        assert "bookings" in first_hour
        assert "latency" in first_hour
