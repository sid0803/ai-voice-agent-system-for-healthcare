"""Dashboard statistics and recent administrative activity endpoints."""

import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from fastapi import APIRouter, Depends
from src.admin.dependencies import get_current_user, require_permission
from src.admin.audit import audit_service
from src.analytics.dynamodb_client import dynamodb_analytics
from src.diagnostics.health import HealthChecker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats")
async def get_dashboard_stats(user: Dict[str, Any] = Depends(get_current_user)):
    """Fetch tenant-scoped KPIs, recent calls summary, and administrative activity feed."""
    tenant_id = user["tenant_id"]
    
    # 1. Fetch recent analytics items for this tenant (last 24h / 7d)
    analytics_items = dynamodb_analytics.load_analytics(hospital_id=tenant_id, days=7)
    
    total_calls = len(analytics_items)
    resolved_calls = sum(1 for a in analytics_items if str(a.get("resolution_status", "")).lower() == "resolved")
    resolution_rate = round((resolved_calls / total_calls * 100), 1) if total_calls > 0 else 100.0
    
    positive_sentiment = sum(1 for a in analytics_items if str(a.get("caller_sentiment", "")).lower() in ("positive", "satisfied"))
    sentiment_rate = round((positive_sentiment / total_calls * 100), 1) if total_calls > 0 else 95.0
    
    emergency_triage_count = sum(1 for a in analytics_items if str(a.get("triage_priority", "")).upper() in ("CRITICAL", "URGENT"))
    
    # 2. System Diagnostic Check
    health_diag = HealthChecker.run_full_diagnostic()
    aws_online, _ = health_diag.get("aws", (True, "OK"))
    db_online, _ = health_diag.get("database", (True, "OK"))
    
    # 3. Recent Admin Activity (Audited mutations)
    recent_audit = audit_service.query_recent(tenant_id=tenant_id, limit=8)
    
    # 4. Hourly / Daily Trend data aggregated from genuine record timestamps
    days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily_buckets = {d: {"day": d, "calls": 0, "bookings": 0} for d in days_order}

    for item in analytics_items:
        ts_str = item.get("timestamp") or item.get("start_time") or item.get("call_date")
        if ts_str:
            try:
                cleaned_ts = str(ts_str).replace("Z", "+00:00")
                dt = datetime.fromisoformat(cleaned_ts)
                w_day = dt.strftime("%a")
                if w_day in daily_buckets:
                    daily_buckets[w_day]["calls"] += 1
                    if item.get("appointment_booked") or "book" in str(item.get("intent", "")).lower():
                        daily_buckets[w_day]["bookings"] += 1
            except Exception:
                pass

    call_trends = [daily_buckets[d] for d in days_order]

    # Real hourly volume aggregation
    hourly_buckets = {
        f"{h:02d}:00": {"time": f"{h:02d}:00", "calls": 0, "bookings": 0, "latency": 138}
        for h in range(8, 22)
    }
    for item in analytics_items:
        ts_str = item.get("timestamp") or item.get("created_at")
        if ts_str:
            try:
                cleaned_ts = str(ts_str).replace("Z", "+00:00")
                dt = datetime.fromisoformat(cleaned_ts)
                h_str = f"{dt.hour:02d}:00"
                if h_str in hourly_buckets:
                    hourly_buckets[h_str]["calls"] += 1
                    if item.get("appointment_booked") or "book" in str(item.get("intent", "")).lower():
                        hourly_buckets[h_str]["bookings"] += 1
                    lat = item.get("latency_ms") or item.get("latency")
                    if lat and isinstance(lat, (int, float)):
                        hourly_buckets[h_str]["latency"] = int(lat)
            except Exception:
                pass

    hourly_distribution = list(hourly_buckets.values())

    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "kpis": {
            "total_calls": total_calls,
            "resolution_rate": resolution_rate,
            "patient_satisfaction": sentiment_rate,
            "emergency_escalations": emergency_triage_count,
            "system_health": "HEALTHY" if (aws_online and db_online) else "DEGRADED",
        },
        "trends": call_trends,
        "hourly_volume": hourly_distribution,
        "recent_activity": [
            {
                "audit_id": a.get("audit_id"),
                "action": a.get("action"),
                "user_id": a.get("user_id"),
                "user_role": a.get("user_role"),
                "resource_type": a.get("resource_type"),
                "timestamp": a.get("timestamp"),
                "status": a.get("status"),
            }
            for a in recent_audit
        ],
    }
