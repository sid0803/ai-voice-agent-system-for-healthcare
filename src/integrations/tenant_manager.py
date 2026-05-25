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
        """Load data with Database Priority -> Local File Fallback -> Mock Default."""
        tid = hospital_id or self.current_tenant
        tid = re.sub(r'[^a-zA-Z0-9_-]', '', tid)

        # 1. Check Memory Cache (Fastest)
        if tid in self._db_cache and (time.time() - self._last_refresh < self._refresh_interval):
            return self._db_cache[tid]

        # 2. Try Database Lookup (SaaS Tier)
        db_data = self._get_from_db(tid)
        if db_data:
            self._db_cache[tid] = db_data
            return db_data

        # 3. Fallback to Local JSON (Developer Tier)
        data_path = self.data_dir / f"{tid}.json"
        if data_path.exists():
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._db_cache[tid] = data
                    return data
            except Exception:
                logger.error(f"Failed to load file for {tid}")

        # 4. Final Mock Fallback
        return self._get_hardcoded_fallback()

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
