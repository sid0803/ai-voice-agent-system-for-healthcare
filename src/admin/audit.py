"""Sanitized Put-Only Audit Logging Service for InDiiServe Admin Portal."""

import os
import re
import time
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Sanitization patterns
SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|api_key|token|auth|cookie|credentials|key|cert)",
    re.IGNORECASE
)
PHONE_KEY_PATTERN = re.compile(r"(phone|mobile|caller_phone|contact)", re.IGNORECASE)

def _mask_phone_val(val: str) -> str:
    if not val:
        return ""
    p = str(val).strip()
    if len(p) < 7:
        return p
    return f"{p[:3]}******{p[-4:]}"

def sanitize_audit_payload(obj: Any) -> Any:
    """Recursively scrub credentials, tokens, and mask phone numbers in audit data."""
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            k_str = str(k)
            if SENSITIVE_KEY_PATTERN.search(k_str):
                sanitized[k] = "[REDACTED]"
            elif PHONE_KEY_PATTERN.search(k_str) and isinstance(v, str):
                sanitized[k] = _mask_phone_val(v)
            else:
                sanitized[k] = sanitize_audit_payload(v)
        return sanitized
    elif isinstance(obj, list):
        return [sanitize_audit_payload(item) for item in obj]
    return obj


class AuditLogger:
    """Service to log security, operational, and clinical audit events to DynamoDB."""

    def __init__(self, table_name: str = "InDiiServe_Audit_Logs", region: str = "ap-south-1"):
        self.table_name = os.environ.get("DYNAMODB_AUDIT_TABLE", table_name)
        self.region = os.environ.get("AWS_REGION", region)
        self._dynamodb = None
        self._table = None
        self._init_client()

    def _init_client(self):
        try:
            self._dynamodb = boto3.resource("dynamodb", region_name=self.region)
            self._table = self._dynamodb.Table(self.table_name)
        except Exception as e:
            logger.warning("[AUDIT] Failed to initialize DynamoDB Audit table: %s", e)

    def log(
        self,
        tenant_id: str,
        user_id: str,
        user_role: str,
        action: str,
        resource_type: str = "SYSTEM",
        resource_id: str = "",
        status: str = "SUCCESS",
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ip_address: str = "",
        user_agent: str = "",
    ):
        """Asynchronously record a sanitized audit event."""
        threading.Thread(
            target=self._write_event,
            args=(
                tenant_id,
                user_id,
                user_role,
                action,
                resource_type,
                resource_id,
                status,
                before_state,
                after_state,
                metadata,
                ip_address,
                user_agent,
            ),
            daemon=True,
        ).start()

    def _write_event(
        self,
        tenant_id: str,
        user_id: str,
        user_role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        status: str,
        before_state: Optional[Dict[str, Any]],
        after_state: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
        ip_address: str,
        user_agent: str,
    ):
        if not self._table:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        audit_id = f"aud_{int(time.time())}_{uuid4().hex[:8]}"

        item = {
            "tenant_id": tenant_id or "default_tier2",
            "audit_id": audit_id,
            "timestamp": now_iso,
            "user_id": user_id or "anonymous",
            "user_role": user_role or "guest",
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id or "N/A",
            "status": status,
            "ip_address": ip_address or "127.0.0.1",
            "user_agent": user_agent or "unknown",
            "before_state": sanitize_audit_payload(before_state or {}),
            "after_state": sanitize_audit_payload(after_state or {}),
            "metadata": sanitize_audit_payload(metadata or {}),
        }

        try:
            self._table.put_item(Item=item)
            logger.info("[AUDIT] Logged %s for user %s on tenant %s (%s)", action, user_id, tenant_id, status)
        except Exception as e:
            logger.warning("[AUDIT] Failed to persist audit item: %s", e)

    def query_recent(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Query recent audit records for a tenant (requires audit.read permission)."""
        if not self._table:
            return []
        try:
            from boto3.dynamodb.conditions import Key
            res = self._table.query(
                KeyConditionExpression=Key("tenant_id").eq(tenant_id),
                ScanIndexForward=False,
                Limit=limit
            )
            return res.get("Items", [])
        except Exception as e:
            logger.warning("[AUDIT] Query failed: %s", e)
            return []


# Global audit logger singleton
audit_service = AuditLogger()
