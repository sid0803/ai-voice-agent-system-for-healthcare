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

    def get_hospital_data(self) -> dict:
        """Load and return data for the current active hospital/tenant."""
        if self._cached_data and self._cached_data.get("id") == self.current_tenant:
            return self._cached_data

        data_path = self.data_dir / f"{self.current_tenant}.json"
        
        # Fallback to default if tenant file missing
        if not data_path.exists():
            logger.warning(f"Data for tenant {self.current_tenant} missing. Using default.")
            data_path = self.data_dir / "default_tier2.json"
            
        if not data_path.exists():
            return self._get_hardcoded_fallback()

        try:
            with open(data_path, "r", encoding="utf-8") as f:
                self._cached_data = json.load(f)
                return self._cached_data
        except Exception:
            logger.exception(f"Failed to load data for {self.current_tenant}")
            return self._get_hardcoded_fallback()

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
