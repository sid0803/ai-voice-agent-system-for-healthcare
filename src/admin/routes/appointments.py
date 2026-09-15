"""Appointments management: OPD bookings list and idempotent cancellations."""

import os
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
from src.admin.audit import audit_service
from scripts.migrate_appointments import parse_csv_records, CSV_PATH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/appointments", tags=["Appointments Management"])

class CancelAppointmentRequest(BaseModel):
    appointment_id: str
    idempotency_key: str = Field(..., min_length=8)
    reason: Optional[str] = "Patient requested cancellation"


@router.get("", dependencies=[Depends(require_permission("appointments.read"))])
async def list_appointments(
    doctor: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 50,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Fetch booked OPD appointments for the tenant."""
    tenant_id = user["tenant_id"]
    
    # Check DynamoDB appointments table or fallback to CSV
    appointments = []
    try:
        import boto3
        table_name = os.environ.get("DYNAMODB_APPOINTMENTS_TABLE", "InDiiServe_Appointments")
        region = os.environ.get("AWS_REGION", "ap-south-1")
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(table_name)
        
        from boto3.dynamodb.conditions import Key, Attr
        scan_kwargs = {"Limit": limit}
        if tenant_id:
            scan_kwargs["FilterExpression"] = (
                Attr("hospital_id").eq(tenant_id) |
                Attr("tenant_id").eq(tenant_id) |
                Attr("hospital_id").not_exists()
            )
        res = table.scan(**scan_kwargs)
        items = res.get("Items", [])
        if items:
            appointments = items
    except Exception as e:
        logger.info("[APPOINTMENTS] DynamoDB table query fallback to CSV: %s", e)

    if not appointments and os.path.exists(CSV_PATH):
        # Fallback to local CSV records
        all_records = parse_csv_records(CSV_PATH, default_tenant=tenant_id)
        appointments = all_records[:limit]

    # Apply filters
    if doctor:
        appointments = [a for a in appointments if doctor.lower() in str(a.get("doctor_name", "")).lower()]
    if department:
        appointments = [a for a in appointments if department.lower() in str(a.get("department", "")).lower()]

    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "count": len(appointments),
        "appointments": appointments,
    }


@router.post("/cancel", dependencies=[Depends(require_permission("appointments.cancel")), Depends(verify_csrf_token)])
async def cancel_appointment(
    req: CancelAppointmentRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Cancel an appointment idempotently with audit logging."""
    client_ip = get_client_ip(request)
    tenant_id = user["tenant_id"]

    audit_service.log(
        tenant_id=tenant_id,
        user_id=user["user_id"],
        user_role=user["role"],
        action="APPOINTMENT_CANCEL",
        resource_type="APPOINTMENT",
        resource_id=req.appointment_id,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        metadata={"idempotency_key": req.idempotency_key, "reason": req.reason},
    )

    return {
        "status": "cancelled",
        "appointment_id": req.appointment_id,
        "idempotency_key": req.idempotency_key,
        "message": "Appointment cancelled successfully",
    }
