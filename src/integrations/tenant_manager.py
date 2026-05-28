import re
import time
import random
import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class TenantManager:
    """Manages hospital-specific data and configuration for multi-tenancy."""
    
    def __init__(self, data_dir: str = "data/hospital_data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.current_tenant = os.environ.get("HOSPITAL_ID", "default_tier2")
        self._cached_data = None
        
        # In-memory cache for high-performance lookups during calls (Requirement: Low Latency)
        self._db_cache: dict[str, dict] = {}
        self._last_refresh = 0
        # Refresh cache every 60s + random jitter (Requirement: Reliability P1)
        self._refresh_interval = 60 + random.randint(-15, 15) 

    def get_hospital_data(self, hospital_id: str = None) -> dict:
        """Load data with Local File Priority -> Database Lookup -> Mock Default."""
        tid = hospital_id or self.current_tenant
        tid = re.sub(r'[^a-zA-Z0-9_-]', '', tid)

        # Unified KB is now the local single source of truth. Keep this path
        # before DynamoDB so local/server deployments do not depend on deleted
        # legacy hospital_data JSON files for status or emergency metadata.
        unified_data = self._get_unified_local_data(tid)
        if unified_data:
            self._db_cache[tid] = unified_data
            return unified_data

        # 1. Check Memory Cache (Fastest)
        if tid in self._db_cache and (time.time() - self._last_refresh < self._refresh_interval):
            return self._db_cache[tid]

        # 2. Prefer Local JSON (Developer Tier / Local override)
        data_path = self.data_dir / f"{tid}.json"
        if data_path.exists():
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Sync to DynamoDB in the background so SaaS analytics are correct
                    self._sync_local_to_db_async(tid, data)
                    self._db_cache[tid] = data
                    return data
            except Exception:
                logger.error(f"Failed to load file for {tid}")

        # 3. Try Database Lookup (SaaS Tier)
        db_data = self._get_from_db(tid)
        if db_data:
            self._db_cache[tid] = db_data
            return db_data

        # 4. Final Mock Fallback
        return self._get_hardcoded_fallback()

    def _sync_local_to_db_async(self, hospital_id: str, data: dict):
        """Update DynamoDB in a background thread to sync local JSON data."""
        import threading
        def run_sync():
            try:
                from src.analytics.dynamodb_client import dynamodb_analytics
                from datetime import datetime
                # Get existing tenant to preserve spreadsheet_id, status etc if already present
                existing = dynamodb_analytics.get_tenant(hospital_id) or {}
                
                tenant_record = {
                    "hospital_id": hospital_id,
                    "hospital_name": data.get("name", "Unknown Hospital"),
                    "status": existing.get("status") or data.get("status") or "live",
                    "ingestion_strategy": existing.get("ingestion_strategy") or "hybrid",
                    "sync_interval_mins": existing.get("sync_interval_mins") or 10,
                    "spreadsheet_id": existing.get("spreadsheet_id") or data.get("spreadsheet_id") or "",
                    "created_at": existing.get("created_at") or datetime.now().isoformat(),
                    "hospital_data_normalized": data
                }
                dynamodb_analytics.save_tenant(tenant_record)
                logger.info(f"[TENANT] Successfully synced local JSON for {hospital_id} to DynamoDB.")
            except Exception as e:
                logger.error(f"[TENANT] Failed to sync local JSON for {hospital_id} to DynamoDB: {e}")
                
        threading.Thread(target=run_sync, daemon=True).start()


    def _get_from_db(self, hospital_id: str) -> dict | None:
        """Fetch tenant config and normalized data from DynamoDB."""
        from src.analytics.dynamodb_client import dynamodb_analytics
        try:
            res = dynamodb_analytics.get_tenant(hospital_id)
            if res:
                status = res.get("status", "pending")
                normalized_data = res.get("hospital_data_normalized", {})
                sheet_id = res.get("spreadsheet_id", "")
                name = res.get("hospital_name", "")
                
                if isinstance(normalized_data, str):
                    try:
                        data = json.loads(normalized_data)
                    except json.JSONDecodeError:
                        data = {}
                else:
                    data = normalized_data or {}
                    
                data["status"] = status
                data["spreadsheet_id"] = sheet_id
                data["name"] = name
                return data
            return None
        except Exception:
            logger.exception(f"DynamoDB Tenant lookup failed for {hospital_id}")
            return None

    def _get_unified_local_data(self, hospital_id: str) -> dict | None:
        """Return unified KB data in the shape older callers expect."""
        kb_path = Path("data") / "unified_hospital_kb.json"
        if not kb_path.exists():
            return None
        try:
            with open(kb_path, "r", encoding="utf-8") as f:
                kb = json.load(f)
        except Exception:
            logger.exception("Failed to load unified hospital KB")
            return None

        metadata = kb.get("metadata", {})
        if hospital_id and metadata.get("hospital_id") and hospital_id != metadata.get("hospital_id"):
            return None

        core = dict(kb.get("core_info", {}))
        core["id"] = metadata.get("hospital_id") or core.get("id") or hospital_id
        core["name"] = metadata.get("hospital_name") or core.get("name", "")
        core["departments"] = kb.get("departments", [])
        core["doctors"] = kb.get("doctors", [])
        core["services"] = kb.get("services", [])
        core["health_packages"] = kb.get("health_packages", [])
        core["room_types"] = kb.get("room_types", [])
        core["operating_hours"] = kb.get("operating_hours", {})
        core["amenities"] = kb.get("amenities", {})
        core["faq"] = kb.get("faq", [])
        core["emergency"] = {
            "contact": core.get("emergency_contact", "1066"),
            "instruction": "I'm connecting you to our emergency desk immediately. Please stay on the line.",
        }
        return core

    def get_status(self, hospital_id: str) -> str:
        """Helper to quickly check if a tenant is live, sandbox, or pending."""
        data = self.get_hospital_data(hospital_id)
        return data.get("status", "pending")

    def get_sheets_id(self, hospital_id: str) -> str:
        """Requirement: Resolve the specific Google Sheets ID for a given hospital."""
        data = self.get_hospital_data(hospital_id)
        # Return the specific sheet ID or fallback to the global environment variable
        return data.get("spreadsheet_id") or os.environ.get("GOOGLE_SHEET_ID")

    def _get_hardcoded_fallback(self) -> dict:
        """Emergency fallback data for Tier 2/3 city hospital."""
        return {
            "id": "default_tier2",
            "name": "Standard Tier-2 Healthcare Center",
            # [D-08] CRITICAL: status MUST be 'live' or 'sandbox' or server.py rejects the call.
            # This fallback activates when DB/JSON lookup fails (SQLite/offline mode).
            "status": "live",
            "departments": ["Cardiology", "Pediatrics", "Orthopedics", "General Medicine"],
            "doctors": [
                {"name": "Dr. Sen", "dept": "Cardiology", "schedule": "Mon-Fri 10AM-2PM"},
                {"name": "Dr. Gupta", "dept": "General Medicine", "schedule": "Daily 9AM-8PM"}
            ],
            "prices": {
                "OPD Consultation": 300,
                "MRI Scan": 6000,
                "Blood Test": 450,
                "ICU Bed (Per Day)": 8000
            }
        }

# Global instance
tenant_manager = TenantManager()
