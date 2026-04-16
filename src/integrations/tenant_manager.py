import json
import os
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

    def get_hospital_data(self, hospital_id: str = None) -> dict:
        """Load and return data for a specific hospital or the current active tenant."""
        tid = hospital_id or self.current_tenant
        
        if not hospital_id and self._cached_data and self._cached_data.get("id") == self.current_tenant:
            return self._cached_data

        data_path = self.data_dir / f"{tid}.json"
        
        # Fallback to default if tenant file missing
        if not data_path.exists():
            logger.warning(f"Data for tenant {tid} missing. Using default.")
            data_path = self.data_dir / "default_tier2.json"
            
        if not data_path.exists():
            return self._get_hardcoded_fallback()

        try:
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not hospital_id:
                    self._cached_data = data
                return data
        except Exception:
            logger.exception(f"Failed to load data for {tid}")
            return self._get_hardcoded_fallback()

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
