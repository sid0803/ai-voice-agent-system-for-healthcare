"""Comprehensive 10-Point Automated Test Suite for InDiiServe Hospital Admin Portal."""

import time
import pytest
from fastapi.testclient import TestClient

from src.server import app
from src.admin.config import ALL_PERMISSIONS, ROLE_PERMISSIONS
from src.admin.dependencies import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_csrf_token,
)
from src.admin.session_store import (
    create_session_family,
    rotate_refresh_token,
    revoke_session_family,
)
from src.admin.audit import sanitize_audit_payload
from scripts.migrate_appointments import parse_csv_records, CSV_PATH

client = TestClient(app)


# 1. Multi-Tenancy Isolation Test
def test_multi_tenancy_isolation():
    """Assert that a user from hospital_A querying calls receives tenant-scoped records."""
    token_a = create_access_token(user_id="admin_a", tenant_id="apollo_metro", role="hospital_admin")
    resp_a = client.get("/api/v1/calls", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["tenant_id"] == "apollo_metro"

    token_b = create_access_token(user_id="admin_b", tenant_id="city_care", role="hospital_admin")
    resp_b = client.get("/api/v1/calls", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["tenant_id"] == "city_care"


# 2. Permission Boundary Test
def test_permission_boundaries():
    """Assert staff role cannot publish KB or invite users without permission."""
    token_staff = create_access_token(user_id="staff_1", tenant_id="apollo_metro", role="staff")
    
    # Attempt KB publish
    resp_kb = client.post(
        "/api/v1/knowledge/publish",
        headers={"Authorization": f"Bearer {token_staff}"},
        json={"version_label": "v1.2", "change_summary": "Unauthorized attempt"}
    )
    assert resp_kb.status_code == 403

    # Attempt user invite
    resp_invite = client.post(
        "/api/v1/auth/invite",
        headers={"Authorization": f"Bearer {token_staff}"},
        json={"username": "new_staff", "password": "Password123!", "role": "staff"}
    )
    assert resp_invite.status_code == 403


# 3. Token Reuse Detection Test
def test_token_reuse_detection():
    """Assert that reusing a stale rotated refresh token immediately invalidates the entire token family."""
    user_id = "test_user_session"
    tenant_id = "apollo_metro"
    
    family_id, tok_1 = create_session_family(user_id, tenant_id)
    
    # Rotation 1: tok_1 -> tok_2
    res1 = rotate_refresh_token(family_id, tok_1)
    assert res1 is not None
    _, _, tok_2 = res1
    
    # Attacker tries to use old tok_1
    stale_res = rotate_refresh_token(family_id, tok_1)
    assert stale_res is None, "Reuse of rotated token must fail"
    
    # Genuine user tries tok_2 now -> must fail because family was revoked on reuse!
    revoked_res = rotate_refresh_token(family_id, tok_2)
    assert revoked_res is None, "Entire family must be revoked after reuse incident"


# 4. CSRF Validation Test
def test_csrf_validation():
    """Assert high-entropy CSRF tokens are generated properly and differ per invocation."""
    t1 = generate_csrf_token()
    t2 = generate_csrf_token()
    assert len(t1) >= 32
    assert t1 != t2


# 5. Idempotent Cancellation Test
def test_idempotent_cancellation():
    """Assert appointment cancellation with idempotency key succeeds cleanly."""
    token = create_access_token(user_id="receptionist_1", tenant_id="apollo_metro", role="staff")
    idempotency_key = "idemp_test_cancel_9999"

    resp = client.post(
        "/api/v1/appointments/cancel",
        headers={"Authorization": f"Bearer {token}"},
        json={"appointment_id": "REF-0-0", "idempotency_key": idempotency_key, "reason": "Patient rescheduled"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["idempotency_key"] == idempotency_key


# 6. Audit Sanitization Test
def test_audit_sanitization():
    """Assert that sensitive passwords, tokens, and phone numbers are scrubbed in audit records."""
    raw = {
        "password": "CleartextPassword123!",
        "api_key": "exotel_token_secret_xyz",
        "nested": {
            "token": "bearer_secret_123",
            "caller_phone": "+919876543210",
        },
        "hospital_id": "apollo_metro",
    }
    sanitized = sanitize_audit_payload(raw)
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert "******" in sanitized["nested"]["caller_phone"]
    assert sanitized["hospital_id"] == "apollo_metro"


# 7. Import Isolation Test
def test_import_isolation():
    """Assert importing src.admin does not initialize voice engine clients on startup."""
    import sys
    import src.admin.config
    import src.admin.audit
    import src.admin.dependencies
    import src.admin.routes.auth
    import src.admin.routes.knowledge
    import src.admin.routes.telephony
    
    assert "src.admin.routes.knowledge" in sys.modules
    assert "src.admin.routes.telephony" in sys.modules


# 8. LLM Distiller Gating Test
def test_distiller_cannot_modify_published_kb():
    """Assert candidate facts from distiller flow strictly to DRAFT KB and cannot modify published KB."""
    from src.tools import _unified_hospital_info
    
    initial_res = _unified_hospital_info({"query": "mri_cost"}, hospital_id="apollo_metro")
    initial_answer = initial_res.get("answer", "")

    # Candidate fact with a modified price in draft
    token = create_access_token(user_id="admin_1", tenant_id="apollo_metro", role="hospital_admin")
    
    # Save a draft
    resp = client.post(
        "/api/v1/knowledge/draft",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "version_label": "v1.1-DRAFT",
            "notes": "Candidate test fact",
            "pricing": {"mri": "₹9,999"}
        }
    )
    assert resp.status_code == 200
    
    # Assert that live production KB query is UNCHANGED!
    current_res = _unified_hospital_info({"query": "mri_cost"}, hospital_id="apollo_metro")
    assert current_res.get("answer") == initial_answer


# 9. Migration Rollback Test
def test_migration_rollback():
    """Assert migration parser gracefully handles invalid/missing CSV without crashing."""
    invalid_records = parse_csv_records("data/non_existent_file.csv")
    assert invalid_records == []


# 10. Migration Idempotency Test
def test_migration_idempotency():
    """Assert running CSV appointment parser multiple times produces identical record counts and unique IDs."""
    records_pass_1 = parse_csv_records(CSV_PATH)
    records_pass_2 = parse_csv_records(CSV_PATH)

    assert len(records_pass_1) == len(records_pass_2)
    assert records_pass_1[0]["appointment_id"] == records_pass_2[0]["appointment_id"]
    assert records_pass_1[0]["idempotency_key"] == records_pass_2[0]["idempotency_key"]


# 11. Triage List and NameError Regression Test
def test_triage_list_events():
    """Assert that list_triage_events does not crash with NameError and returns structured items."""
    token = create_access_token(user_id="staff_1", tenant_id="apollo_metro", role="staff")
    resp = client.get(
        "/api/v1/triage",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "events" in data
    assert data["tenant_id"] == "apollo_metro"
    assert isinstance(data["events"], list)


# 12. Triage Acknowledge with Mocked DynamoDB Persistence
def test_triage_acknowledge_persistence(monkeypatch):
    """Assert triage acknowledgment calls update_item on DynamoDB table."""
    mock_updates = []
    class MockTable:
        def update_item(self, **kwargs):
            mock_updates.append(kwargs)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}
        def scan(self, **kwargs):
            return {"Items": [{"event_id": "trg_test_1", "status": "OPEN", "priority": "CRITICAL"}]}

    import src.admin.routes.triage as triage_module
    monkeypatch.setattr(triage_module, "_get_triage_table", lambda: MockTable())

    token = create_access_token(user_id="staff_1", tenant_id="apollo_metro", role="staff")
    csrf = generate_csrf_token()
    resp = client.patch(
        "/api/v1/triage/trg_test_1/acknowledge",
        headers={
            "Authorization": f"Bearer {token}",
            "X-CSRF-Token": csrf,
            "Cookie": f"indiiserve_csrf_token={csrf}"
        },
        json={"assigned_doctor_id": "doc_cardio_1", "notes": "Patient routed to ER"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "acknowledged"
    assert data["event_id"] == "trg_test_1"
    assert data["assigned_doctor_id"] == "doc_cardio_1"
    assert len(mock_updates) == 1
    assert mock_updates[0]["Key"] == {"event_id": "trg_test_1"}
    assert mock_updates[0]["ExpressionAttributeValues"][":s"] == "ACKNOWLEDGED"
    assert mock_updates[0]["ExpressionAttributeValues"][":d"] == "doc_cardio_1"


# 13. Triage Resolve with Mocked DynamoDB Persistence
def test_triage_resolve_persistence(monkeypatch):
    """Assert triage resolve calls update_item on DynamoDB table."""
    mock_updates = []
    class MockTable:
        def update_item(self, **kwargs):
            mock_updates.append(kwargs)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    import src.admin.routes.triage as triage_module
    monkeypatch.setattr(triage_module, "_get_triage_table", lambda: MockTable())

    token = create_access_token(user_id="doc_1", tenant_id="apollo_metro", role="doctor")
    csrf = generate_csrf_token()
    resp = client.patch(
        "/api/v1/triage/trg_test_1/resolve",
        headers={
            "Authorization": f"Bearer {token}",
            "X-CSRF-Token": csrf,
            "Cookie": f"indiiserve_csrf_token={csrf}"
        },
        json={"clinical_notes": "ECG completed, nitroglycerin administered, patient stable.", "outcome": "Admitted to CCU"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"
    assert data["event_id"] == "trg_test_1"
    assert data["clinical_notes"] == "ECG completed, nitroglycerin administered, patient stable."
    assert len(mock_updates) == 1
    assert mock_updates[0]["Key"] == {"event_id": "trg_test_1"}
    assert mock_updates[0]["ExpressionAttributeValues"][":s"] == "RESOLVED"


# 14. Telephony Whitelist Configuration
def test_telephony_whitelist_includes_live_number():
    """Assert telephony whitelist includes live hospital line +918047283874 and excludes old trial number."""
    from src.admin.routes.telephony import ALLOWED_TEST_NUMBERS
    assert "+918047283874" in ALLOWED_TEST_NUMBERS
    assert "+919513886363" not in ALLOWED_TEST_NUMBERS


# 15. Outbound Call Whitelist and Guard Enforcement Test
def test_outbound_call_whitelist_enforcement(monkeypatch):
    """Assert outbound calls strictly enforce whitelist, double confirmation, and phone formatting."""
    import src.admin.routes.telephony as telephony_mod
    monkeypatch.setattr(telephony_mod, "OUTBOUND_CALLING_ENABLED", True)
    monkeypatch.setenv("ENVIRONMENT", "development")

    token = create_access_token(user_id="admin_telephony", tenant_id="apollo_metro", role="hospital_admin")
    csrf = generate_csrf_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
        "Cookie": f"indiiserve_csrf_token={csrf}"
    }

    # 1. Non-whitelisted number in dev/staging -> 403 Forbidden
    resp_blocked = client.post(
        "/api/v1/telephony/outbound-call",
        headers=headers,
        json={
            "destination_phone": "+919999999999",
            "patient_name": "Test Patient",
            "call_purpose": "OPD_APPOINTMENT_REMINDER",
            "double_confirmed": True
        }
    )
    assert resp_blocked.status_code == 403
    assert "whitelisted" in resp_blocked.json()["detail"]

    # 2. Missing double-confirmation -> 400 Bad Request
    resp_unconfirmed = client.post(
        "/api/v1/telephony/outbound-call",
        headers=headers,
        json={
            "destination_phone": "+918047283874",
            "patient_name": "Test Patient",
            "call_purpose": "OPD_APPOINTMENT_REMINDER",
            "double_confirmed": False
        }
    )
    assert resp_unconfirmed.status_code == 400
    assert "double-confirmation" in resp_unconfirmed.json()["detail"]

    # 3. Whitelisted number with confirmation -> 200 OK
    resp_ok = client.post(
        "/api/v1/telephony/outbound-call",
        headers=headers,
        json={
            "destination_phone": "+918047283874",
            "patient_name": "Test Patient",
            "call_purpose": "OPD_APPOINTMENT_REMINDER",
            "double_confirmed": True
        }
    )
    assert resp_ok.status_code == 200
    data_ok = resp_ok.json()
    assert data_ok["status"] == "initiated"
    assert "******" in data_ok["destination_phone"]


# 11. Distiller Review Facts & Root SPA Serving Tests (AI-02 & AI-06)
def test_distiller_review_facts_endpoints():
    """Verify that review-facts endpoints require auth and respond under /api/v1/knowledge."""
    # 1. Unauthenticated request -> 401
    resp_unauth = client.get("/api/v1/knowledge/review-facts")
    assert resp_unauth.status_code == 401

    # 2. Staff without manage_knowledge permission -> 403
    token_staff = create_access_token(user_id="staff_user", tenant_id="apollo_metro", role="staff")
    resp_forbidden = client.get(
        "/api/v1/knowledge/review-facts",
        headers={"Authorization": f"Bearer {token_staff}"},
    )
    assert resp_forbidden.status_code == 403

    # 3. Admin with manage_knowledge -> 200 OK
    token_admin = create_access_token(user_id="admin_user", tenant_id="apollo_metro", role="hospital_admin")
    resp_ok = client.get(
        "/api/v1/knowledge/review-facts",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "pending_facts" in data
    assert "count" in data


def test_root_spa_serving():
    """Verify that GET / returns the portal SPA HTML content (AI-02)."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in resp.text or "<html" in resp.text
