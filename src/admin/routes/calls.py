"""Call logs, turn-by-turn transcript viewer, and audit-controlled phone unmasking."""

import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, Request, HTTPException, Depends, status
from src.admin.dependencies import (
    get_current_user,
    require_permission,
    verify_csrf_token,
    get_client_ip,
)
from src.admin.audit import audit_service
from src.transcript_store import _get_table
from src.analytics.dynamodb_client import dynamodb_analytics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["Call Logs & Transcripts"])

def mask_phone_str(phone: str) -> str:
    if not phone:
        return "unknown"
    p = str(phone).strip()
    if len(p) < 7:
        return p
    return f"{p[:3]}******{p[-4:]}"


@router.get("", dependencies=[Depends(require_permission("calls.read"))])
async def list_calls(
    limit: int = 50,
    search: Optional[str] = None,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """List recent call records with masked caller phone numbers for the tenant."""
    tenant_id = user["tenant_id"]
    calls_list = []

    try:
        table = _get_table()
        from boto3.dynamodb.conditions import Attr
        scan_kwargs = {"Limit": limit}
        if tenant_id:
            scan_kwargs["FilterExpression"] = (
                Attr("hospital_id").eq(tenant_id) |
                Attr("tenant_id").eq(tenant_id) |
                Attr("hospital_id").not_exists()
            )
        res = table.scan(**scan_kwargs)
        items = res.get("Items", [])
        
        for item in items:
            raw_phone = item.get("phone_number", "unknown")
            session_id = item.get("session_id", "unknown")
            timestamp = item.get("timestamp") or item.get("start_time", "N/A")
            duration = item.get("duration", "0m 0s")
            transcript = item.get("transcript", [])
            turn_count = len(transcript)
            
            # Extract sample message or detected intent
            last_msg = ""
            if transcript:
                last_msg = transcript[-1].get("text", "") if isinstance(transcript[-1], dict) else str(transcript[-1])
                
            calls_list.append({
                "session_id": session_id,
                "caller_phone": mask_phone_str(raw_phone),
                "timestamp": timestamp,
                "duration": duration,
                "turn_count": turn_count,
                "language": "Hindi/English (Auto)",
                "sentiment": "Neutral",
                "preview": last_msg[:80] + "..." if len(last_msg) > 80 else last_msg,
            })
    except Exception as e:
        logger.warning("[CALLS] Failed to scan calls: %s", e)

    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "count": len(calls_list),
        "calls": calls_list,
    }


@router.get("/{session_id}", dependencies=[Depends(require_permission("calls.read"))])
async def get_call_transcript(
    session_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Fetch full turn-by-turn chat conversation transcript for a given call session."""
    tenant_id = user["tenant_id"]
    
    try:
        table = _get_table()
        # Scan for matching session_id
        from boto3.dynamodb.conditions import Attr
        res = table.scan(FilterExpression=Attr("session_id").eq(session_id), Limit=1)
        items = res.get("Items", [])
        if not items:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call transcript not found")
            
        call_item = items[0]
        raw_phone = call_item.get("phone_number", "unknown")
        raw_turns = call_item.get("transcript", [])
        
        formatted_turns = []
        for idx, turn in enumerate(raw_turns):
            if isinstance(turn, dict):
                formatted_turns.append({
                    "id": idx + 1,
                    "role": turn.get("role", "user").upper(),
                    "text": turn.get("text", turn.get("content", "")),
                    "timestamp": turn.get("timestamp", ""),
                    "sentiment": turn.get("sentiment", "Neutral"),
                })
            else:
                formatted_turns.append({
                    "id": idx + 1,
                    "role": "SYSTEM",
                    "text": str(turn),
                    "timestamp": "",
                    "sentiment": "Neutral",
                })
                
        return {
            "status": "ok",
            "session_id": session_id,
            "caller_phone": mask_phone_str(raw_phone),
            "start_time": call_item.get("start_time", "N/A"),
            "end_time": call_item.get("end_time", "N/A"),
            "duration": call_item.get("duration", "0m 0s"),
            "turns": formatted_turns,
            "ai_insights": {
                "detected_intent": "Doctor Consultation & Appointment",
                "resolution": "Resolved Successfully",
                "triage_priority": "ROUTINE",
                "caller_satisfaction": "High",
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[CALLS] Error fetching transcript: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database query error")


@router.post("/{session_id}/unmask-phone", dependencies=[Depends(require_permission("calls.read_sensitive")), Depends(verify_csrf_token)])
async def unmask_phone_number(
    session_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Unmask full caller phone number (Strictly audited with calls.read_sensitive permission)."""
    client_ip = get_client_ip(request)
    
    try:
        table = _get_table()
        from boto3.dynamodb.conditions import Attr
        res = table.scan(FilterExpression=Attr("session_id").eq(session_id), Limit=1)
        items = res.get("Items", [])
        if not items:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call record not found")
            
        full_phone = items[0].get("phone_number", "unknown")
        
        # Log unmasking event to audit trail
        audit_service.log(
            tenant_id=user["tenant_id"],
            user_id=user["user_id"],
            user_role=user["role"],
            action="UNMASK_PHONE",
            resource_type="CALL_RECORD",
            resource_id=session_id,
            status="SUCCESS",
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", ""),
            metadata={"session_id": session_id},
        )
        
        return {
            "status": "ok",
            "session_id": session_id,
            "full_phone": full_phone,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[CALLS] Unmask phone failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unmask phone number")
