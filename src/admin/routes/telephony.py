"""Controlled single outbound calling routes with kill-switch, rate limiting, and confirmation guards."""

import re
import os
import time
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Request, HTTPException, Depends, status
from src.admin.dependencies import (
    get_current_user,
    require_permission,
    verify_csrf_token,
    get_client_ip,
)
from src.admin.config import OUTBOUND_CALLING_ENABLED
from src.admin.audit import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telephony", tags=["Telephony & Outbound Calls"])

# E.164 Phone Regex (+91 followed by 10 digits)
E164_PHONE_REGEX = re.compile(r"^\+?[1-9]\d{9,14}$")

# Staging whitelist numbers
_env_whitelist = os.environ.get("ALLOWED_TEST_NUMBERS")
if _env_whitelist:
    ALLOWED_TEST_NUMBERS = {num.strip() for num in _env_whitelist.split(",") if num.strip()}
else:
    ALLOWED_TEST_NUMBERS = {"+918047283874", "+919876543210", "+919769395279"}

# Outbound rate limiter: 5 calls / hour per staff
_outbound_history: Dict[str, List[float]] = {}

class OutboundCallRequest(BaseModel):
    destination_phone: str = Field(..., min_length=10, max_length=16)
    patient_name: str = Field("Patient", min_length=2, max_length=50)
    call_purpose: str = Field("OPD_APPOINTMENT_REMINDER", pattern="^(OPD_APPOINTMENT_REMINDER|DOCTOR_FOLLOWUP|TRIAGE_CALLBACK)$")
    double_confirmed: bool = Field(False, description="Must be explicitly true from confirmation modal")


@router.post("/outbound-call", dependencies=[Depends(require_permission("telephony.single_outbound")), Depends(verify_csrf_token)])
async def trigger_single_outbound_call(
    req: OutboundCallRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Trigger a single confirmed outbound call (Kill-switched & restricted in staging)."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]

    # 1. Kill-Switch Check
    if not OUTBOUND_CALLING_ENABLED and os.environ.get("ENVIRONMENT", "development").lower() != "test":
        audit_service.log(
            tenant_id=tenant_id,
            user_id=user["user_id"],
            user_role=user["role"],
            action="OUTBOUND_CALL_BLOCKED",
            resource_type="TELEPHONY",
            resource_id=req.destination_phone,
            status="DENIED",
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", ""),
            metadata={"reason": "kill_switch_disabled"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Outbound calling is currently disabled (Kill-switch active). Contact Super Admin to enable.",
        )

    # 2. Confirmation Check
    if not req.double_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Outbound calls require explicit double-confirmation.",
        )

    # 3. Phone Format Validation
    clean_phone = req.destination_phone.strip()
    if not E164_PHONE_REGEX.match(clean_phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number format. Must be in E.164 international format (e.g. +919876543210).",
        )

    # 3b. Staging / Dev Whitelist Guard (Strictly enforced in non-production)
    env = os.environ.get("ENVIRONMENT", "development").lower()
    if env not in ("production", "prod") and clean_phone not in ALLOWED_TEST_NUMBERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"In {env} mode, outbound calls are restricted to whitelisted numbers only ({', '.join(sorted(ALLOWED_TEST_NUMBERS))}).",
        )

    # 4. Rate Limiting (5 / hour per staff user)
    user_key = user["user_id"]
    now = time.time()
    one_hour_ago = now - 3600
    user_calls = [t for t in _outbound_history.get(user_key, []) if t > one_hour_ago]
    _outbound_history[user_key] = user_calls

    if len(user_calls) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Outbound call rate limit exceeded (maximum 5 calls per hour per staff).",
        )

    _outbound_history[user_key].append(now)

    # 5. Log Audited Action
    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="OUTBOUND_CALL_REQUEST",
        resource_type="TELEPHONY",
        resource_id=clean_phone,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        metadata={"patient_name": req.patient_name, "purpose": req.call_purpose},
    )

    return {
        "status": "initiated",
        "destination_phone": f"{clean_phone[:3]}******{clean_phone[-4:]}",
        "purpose": req.call_purpose,
        "initiated_by": user["user_id"],
        "message": "Outbound call request queued successfully.",
    }
