"""Tests for Advanced Clinical Voice AI Modules:
1. Live Voice AI Sandbox Simulation (_unified_hospital_info grounding + telemetry)
2. Clinical Analytics & Revenue Intelligence Aggregation
3. Document-to-Knowledge AI Ingestion Staging
"""

import pytest
from httpx import AsyncClient, ASGITransport
from src.server import app
from src.admin.dependencies import create_access_token


@pytest.fixture
def admin_headers():
    token = create_access_token(
        user_id="admin_test",
        tenant_id="apollo_metro",
        role="hospital_admin",
    )
    return {
        "Cookie": f"indiiserve_access_token={token}; indiiserve_csrf_token=valid_csrf_123",
        "X-CSRF-Token": "valid_csrf_123",
    }


@pytest.mark.asyncio
async def test_sandbox_simulate_doctor_inquiry(admin_headers):
    """Verify sandbox simulation responds with grounded Doctor information and tool trace."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sandbox/simulate",
            json={"query": "Dr. Amit Sharma OPD timings and fee", "persona": "STANDARD", "language": "hi-IN"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SUCCESS"
        assert "Amit Sharma" in data["response_text"]
        assert data["tool_call"]["name"] == "_unified_hospital_info"
        assert "telemetry" in data
        assert data["telemetry"]["speech_to_speech_latency_ms"] > 0
        assert data["telemetry"]["sentiment_label"] == "REASSURED"


@pytest.mark.asyncio
async def test_sandbox_simulate_emergency(admin_headers):
    """Verify sandbox simulation detects ESI-1 cardiac emergencies and sets critical flags."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/sandbox/simulate",
            json={"query": "Acute chest pain radiating to left arm", "persona": "CHEST_PAIN", "language": "hi-IN"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_emergency"] is True
        assert data["intent"] == "EMERGENCY_TRIAGE"
        assert "Emergency Department" in data["response_text"]


@pytest.mark.asyncio
async def test_analytics_overview(admin_headers):
    """Verify clinical analytics aggregates revenue, utilization heatmaps, and funnel data."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/analytics/overview", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "kpis" in data
        assert data["kpis"]["today_voice_revenue"] >= 34500
        assert len(data["department_revenue"]) >= 3
        assert len(data["hourly_heatmap"]) == 11
        assert len(data["conversion_funnel"]) == 4


@pytest.mark.asyncio
async def test_document_upload_staging(admin_headers):
    """Verify document upload parses doctor roster and tariffs into v1.2-DRAFT."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/knowledge/upload-document",
            json={"filename": "apollo_duty_roster.pdf", "doc_type": "ROSTER_OR_TARIFF"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "staged_into_draft"
        assert data["draft_version"] == "v1.2-DRAFT"
        assert len(data["extracted_doctors"]) >= 2
        assert len(data["extracted_tariffs"]) >= 2
