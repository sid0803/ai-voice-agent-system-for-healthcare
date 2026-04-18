import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class UniversalDataAdapter:
    """The Normalization Layer: Converts hospital raw data to Project Asha standards."""

    @staticmethod
    def normalize(raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Maps disparate fields (HIS specific) to Asha's expected JSON schema."""
        if not raw_data:
            return {}
            
        try:
            # 1. Identity
            normalized = {
                "id": raw_data.get("hospital_id") or raw_data.get("id"),
                "name": raw_data.get("hospital_name") or raw_data.get("name", "Unnamed Facility"),
                "address": raw_data.get("location") or raw_data.get("address", ""),
            }

            # 2. Departments
            # Input might be a list of strings or list of objects
            raw_depts = raw_data.get("departments", [])
            if raw_depts and isinstance(raw_depts[0], dict):
                normalized["departments"] = [d.get("name") for d in raw_depts if d.get("name")]
            else:
                normalized["departments"] = raw_depts

            # 3. Doctors (Roster)
            # Input might use 'specialty' instead of 'dept', 'fees' instead of 'fee'
            normalized["doctors"] = []
            raw_docs = raw_data.get("doctors", raw_data.get("staff", []))
            for doc in raw_docs:
                normalized["doctors"].append({
                    "name": doc.get("name"),
                    "dept": doc.get("dept") or doc.get("specialty") or doc.get("department", "General"),
                    "schedule": doc.get("schedule") or doc.get("timings") or "By Appointment",
                    "fee": doc.get("fee") or doc.get("fees") or doc.get("consultation_fee", 0),
                    "room": doc.get("room") or doc.get("cabin", "")
                })

            # 4. Pricing
            normalized["prices"] = raw_data.get("prices") or raw_data.get("rate_card") or {}

            # 5. FAQ & Policy
            normalized["faq"] = raw_data.get("faq") or raw_data.get("knowledge_base") or {}
            
            # 6. Sheets Metadata
            normalized["spreadsheet_id"] = raw_data.get("spreadsheet_id")

            return normalized

        except Exception as e:
            logger.error(f"Normalization failed: {str(e)}")
            # Return raw if normalization fails, allowing model to try its best
            return raw_data

# Global instance
data_adapter = UniversalDataAdapter()
