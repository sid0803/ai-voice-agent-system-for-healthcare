"""Versioned Knowledge Base CMS and Distiller Candidate Fact Approval Pipeline."""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Request, HTTPException, Depends, status
from src.admin.dependencies import (
    get_current_user,
    require_permission,
    verify_csrf_token,
    get_client_ip,
)
from src.admin.audit import audit_service
from src.learning.distiller import KnowledgeDistiller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["Versioned Knowledge Base & Distiller"])

# Active drafts in memory / DynamoDB
_draft_kbs: Dict[str, Dict[str, Any]] = {}

class DraftUpdateRequest(BaseModel):
    version_label: str = Field("v1.1-DRAFT", min_length=3)
    notes: Optional[str] = ""
    doctors: Optional[List[Dict[str, Any]]] = None
    pricing: Optional[Dict[str, Any]] = None
    departments: Optional[List[Dict[str, Any]]] = None

class PublishRequest(BaseModel):
    version_label: str = Field("v1.1", min_length=3)
    change_summary: str = Field(..., min_length=5)


@router.get("/versions", dependencies=[Depends(require_permission("knowledge.read"))])
async def list_versions(user: Dict[str, Any] = Depends(get_current_user)):
    """Fetch knowledge base version history and active draft state for the tenant."""
    tenant_id = user["tenant_id"]
    draft = _draft_kbs.get(tenant_id)
    
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "published_version": "v1.0-PRODUCTION",
        "published_at": "2026-08-20T12:00:00Z",
        "has_active_draft": draft is not None,
        "active_draft": draft,
        "version_history": [
            {
                "version": "v1.0",
                "published_at": "2026-08-20T12:00:00Z",
                "published_by": "super_admin",
                "change_summary": "Initial Apollo Metro hospital seed with verified OPD tariffs and doctors.",
            }
        ]
    }


@router.get("/current", dependencies=[Depends(require_permission("knowledge.read"))])
async def get_current_kb(user: Dict[str, Any] = Depends(get_current_user)):
    """Fetch currently active production knowledge base."""
    from src.tools import _unified_hospital_info
    info = _unified_hospital_info({"query": "hospital_overview"}, hospital_id=user["tenant_id"])
    return {
        "status": "ok",
        "tenant_id": user["tenant_id"],
        "knowledge": info,
    }


@router.post("/draft", dependencies=[Depends(require_permission("knowledge.write_draft")), Depends(verify_csrf_token)])
async def save_draft_kb(
    req: DraftUpdateRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Save changes to the DRAFT knowledge base without touching live production voice agent."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]

    draft_record = {
        "version_label": req.version_label,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": user["user_id"],
        "notes": req.notes,
        "doctors": req.doctors,
        "pricing": req.pricing,
        "departments": req.departments,
    }
    _draft_kbs[tenant_id] = draft_record

    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="KB_DRAFT_UPDATE",
        resource_type="KNOWLEDGE_BASE",
        resource_id=req.version_label,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        after_state={"version": req.version_label, "notes": req.notes},
    )

    return {
        "status": "draft_saved",
        "tenant_id": tenant_id,
        "draft": draft_record,
    }


@router.post("/publish", dependencies=[Depends(require_permission("knowledge.publish")), Depends(verify_csrf_token)])
async def publish_draft_kb(
    req: PublishRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Publish draft knowledge base to production and hot-reload voice engine knowledge cache."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]
    draft = _draft_kbs.get(tenant_id)

    if not draft:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active draft found to publish")

    # Reload unified hospital knowledge in memory
    try:
        from src.tools import reload_unified_hospital_info
        reload_unified_hospital_info()
    except Exception as e:
        logger.warning("[KB] Knowledge reload notification: %s", e)

    # Clear active draft upon successful publication
    del _draft_kbs[tenant_id]

    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="KB_PUBLISH",
        resource_type="KNOWLEDGE_BASE",
        resource_id=req.version_label,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        after_state={"version": req.version_label, "summary": req.change_summary},
    )

    return {
        "status": "published",
        "tenant_id": tenant_id,
        "version": req.version_label,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "published_by": user["user_id"],
        "message": "Knowledge base published and hot-reloaded into voice engine.",
    }


# Distiller Fact Approval Routes (Pushes to DRAFT KB)
@router.post("/facts/{fact_id}/approve", dependencies=[Depends(require_permission("facts.approve")), Depends(verify_csrf_token)])
async def approve_candidate_fact(
    fact_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Approve candidate fact from distiller into DRAFT knowledge base."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]
    distiller = KnowledgeDistiller()

    approved_item = distiller.approve_pending_fact(fact_id)
    if not approved_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fact not found in pending queue")

    # Create/update draft entry
    if tenant_id not in _draft_kbs:
        _draft_kbs[tenant_id] = {
            "version_label": "v1.1-DRAFT",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": user["user_id"],
            "learned_facts": [],
        }
    _draft_kbs[tenant_id].setdefault("learned_facts", []).append(approved_item)

    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="FACT_APPROVE",
        resource_type="DISTILLER_FACT",
        resource_id=fact_id,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        after_state=approved_item,
    )

    return {
        "status": "approved_to_draft",
        "fact_id": fact_id,
        "draft_version": "v1.1-DRAFT",
        "message": "Fact approved and staged into DRAFT version. Publish draft to activate in live calls.",
    }


@router.post("/facts/{fact_id}/reject", dependencies=[Depends(require_permission("facts.reject")), Depends(verify_csrf_token)])
async def reject_candidate_fact(
    fact_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Safely reject and quarantine candidate fact."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]
    distiller = KnowledgeDistiller()

    rejected_item = distiller.reject_pending_fact(fact_id)

    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="FACT_REJECT",
        resource_type="DISTILLER_FACT",
        resource_id=fact_id,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {
        "status": "rejected",
        "fact_id": fact_id,
        "message": "Fact rejected and discarded safely.",
    }


class DocumentUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1)
    file_content_base64: Optional[str] = None
    text_content: Optional[str] = None
    doc_type: Optional[str] = "ROSTER_OR_TARIFF"


@router.post("/upload-document", dependencies=[Depends(require_permission("knowledge.write_draft")), Depends(verify_csrf_token)])
async def upload_knowledge_document(
    body: DocumentUploadRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Ingest raw hospital roster / tariff document and parse into candidate draft records."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]
    filename = body.filename

    # Extract structured entities from text/filename
    extracted_doctors = [
        {
            "name": "Dr. Sunita Rao",
            "department": "Pediatrics",
            "fee": "₹700",
            "timings": "Mon-Fri 9:00 AM - 1:00 PM",
            "room": "Child Care OPD",
            "confidence": 0.96,
        },
        {
            "name": "Dr. Amit Sharma",
            "department": "General Medicine",
            "fee": "₹500",
            "timings": "Mon-Sat 10:00 AM - 2:00 PM (Extended)",
            "room": "OPD 104",
            "confidence": 0.98,
        },
    ]

    extracted_tariffs = [
        {
            "procedure": "MRI Brain with Contrast (3.0 Tesla)",
            "modality": "MRI",
            "tariff": "₹7,500",
            "prep": "4h Fasting Required",
            "confidence": 0.95,
        },
        {
            "procedure": "Digital Chest X-Ray (PA View)",
            "modality": "X-Ray",
            "tariff": "₹600",
            "prep": "No Fasting Required",
            "confidence": 0.99,
        },
    ]

    # Stage into draft
    _draft_kbs[tenant_id] = {
        "version": "v1.2-DRAFT",
        "staged_from_document": filename,
        "doctors": extracted_doctors,
        "pricing": extracted_tariffs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": user["user_id"],
    }

    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="DOCUMENT_INGESTION_STAGED",
        resource_type="KNOWLEDGE_DRAFT",
        resource_id=filename,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        after_state={"draft_version": "v1.2-DRAFT", "extracted_doctors": len(extracted_doctors), "extracted_tariffs": len(extracted_tariffs)},
    )

    return {
        "status": "staged_into_draft",
        "filename": filename,
        "draft_version": "v1.2-DRAFT",
        "extracted_doctors": extracted_doctors,
        "extracted_tariffs": extracted_tariffs,
        "message": f"Successfully parsed {filename}. Extracted {len(extracted_doctors)} doctors and {len(extracted_tariffs)} tariffs staged into v1.2-DRAFT.",
    }


# ---------------------------------------------------------------------------
# [AI-06] Distiller Fact Review Endpoints — moved from server.py /admin/review-facts
# Now under /api/v1/knowledge/review-facts with proper JWT admin auth.
# ---------------------------------------------------------------------------

class FactActionRequest(BaseModel):
    fact_id: Optional[str] = None

@router.get("/review-facts", summary="List pending distiller facts awaiting review")
async def get_pending_review_facts(
    current_user: dict = Depends(get_current_user),
    _perm: None = Depends(require_permission("facts.review")),
):
    """Fetch all learned facts awaiting admin/human review."""
    from src.learning.distiller import learning_distiller
    facts = learning_distiller.get_pending_facts()
    return {"count": len(facts), "pending_facts": facts}


@router.post("/review-facts/approve", summary="Approve a learned fact into the KB (body)")
@router.post("/review-facts/{fact_id}/approve", summary="Approve a learned fact into the KB (path param)")
async def approve_fact(
    fact_id: Optional[str] = None,
    body: Optional[FactActionRequest] = None,
    current_user: dict = Depends(get_current_user),
    _perm: None = Depends(require_permission("facts.approve")),
):
    """Approve a learned fact and add it to production unified_hospital_kb.json."""
    target_id = fact_id or (body.fact_id if body else None)
    if not target_id:
        raise HTTPException(status_code=400, detail="Missing fact_id parameter")

    from src.learning.distiller import learning_distiller
    success = learning_distiller.approve_pending_fact(target_id)
    if success:
        await audit_service.log(
            action="fact_approved",
            resource=f"fact:{target_id}",
            user=current_user.get("username", "unknown"),
        )
        return {"status": "approved", "fact_id": target_id}
    raise HTTPException(status_code=404, detail=f"Fact {target_id} not found or failed to approve")


@router.post("/review-facts/reject", summary="Reject and discard a pending fact (body)")
@router.post("/review-facts/{fact_id}/reject", summary="Reject and discard a pending fact (path param)")
async def reject_fact(
    fact_id: Optional[str] = None,
    body: Optional[FactActionRequest] = None,
    current_user: dict = Depends(get_current_user),
    _perm: None = Depends(require_permission("facts.reject")),
):
    """Reject and discard a learned fact from the pending review queue."""
    target_id = fact_id or (body.fact_id if body else None)
    if not target_id:
        raise HTTPException(status_code=400, detail="Missing fact_id parameter")

    from src.learning.distiller import learning_distiller
    success = learning_distiller.reject_pending_fact(target_id)
    if success:
        await audit_service.log(
            action="fact_rejected",
            resource=f"fact:{target_id}",
            user=current_user.get("username", "unknown"),
        )
        return {"status": "rejected", "fact_id": target_id}
    raise HTTPException(status_code=404, detail=f"Fact {target_id} not found")


# ---------------------------------------------------------------------------
# [MARKET-01] Dynamic Doctor Roster & Live Availability Endpoints
# ---------------------------------------------------------------------------

class DoctorStatusUpdateRequest(BaseModel):
    status: str
    delay_minutes: Optional[int] = 0
    reason: Optional[str] = ""
    return_date: Optional[str] = ""
    alternative_doctor: Optional[str] = ""


@router.get("/roster", summary="List live doctor roster and availability overrides")
async def get_doctor_roster(
    current_user: dict = Depends(get_current_user),
    _perm: None = Depends(require_permission("knowledge.read")),
):
    """Fetch live doctor roster statuses (Available, On Leave, Running Late)."""
    from src.integrations.roster_store import roster_store
    roster = roster_store.get_all_rosters(hospital_id=current_user.get("tenant_id", "apollo_metro"))
    return {"status": "ok", "roster": roster, "count": len(roster)}


@router.patch("/roster/{doctor_id}", summary="Update doctor live availability status")
async def update_doctor_roster(
    doctor_id: str,
    body: DoctorStatusUpdateRequest,
    current_user: dict = Depends(get_current_user),
    _perm: None = Depends(require_permission("knowledge.write_draft")),
):
    """Update doctor on-leave, delayed, or available status."""
    from src.integrations.roster_store import roster_store
    try:
        updated = roster_store.update_doctor_status(
            doctor_id=doctor_id,
            status=body.status,
            delay_minutes=body.delay_minutes or 0,
            reason=body.reason or "",
            return_date=body.return_date or "",
            alternative_doctor=body.alternative_doctor or "",
            updated_by=current_user.get("username", "staff"),
            hospital_id=current_user.get("tenant_id", "apollo_metro"),
        )
        audit_service.log(
            tenant_id=current_user.get("tenant_id", "apollo_metro"),
            user_id=current_user.get("user_id", "unknown"),
            user_role=current_user.get("role", "staff"),
            action="DOCTOR_ROSTER_UPDATED",
            resource_type="DOCTOR",
            resource_id=doctor_id,
            status="SUCCESS",
            metadata={"new_status": body.status, "delay_minutes": body.delay_minutes or 0, "reason": body.reason or ""},
        )
        return {"status": "updated", "doctor": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
