"""Unified Knowledge Base Loader.

The project now uses data/unified_hospital_kb.json as the local single source
of truth. Legacy mode is retained only as an explicit error path so stale
hospital_data/distilled_facts files cannot silently re-enter production.
"""

import json
import logging
import pathlib
import time
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ============================================================================
# Dual-Mode KB Loader
# ============================================================================

class HospitalKBLoader:
    """Loads the unified hospital knowledge base."""
    
    def __init__(self, kb_system: str = "legacy"):
        self.kb_system = kb_system
        self.kb_data: Dict[str, Any] = {}
        self.load_start_time = time.time()
        self._load()
        self.load_duration_ms = (time.time() - self.load_start_time) * 1000
        
    def _load(self):
        """Load KB based on configured system"""
        if self.kb_system != "unified":
            raise ValueError("Legacy KB files were removed. Set KB_SYSTEM=unified.")
        self._load_unified()
    
    def _load_unified(self):
        """Load new unified KB system"""
        logger.info("[KB LOADER] Loading UNIFIED knowledge base...")
        kb_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "unified_hospital_kb.json"
        
        try:
            with open(kb_path, "r", encoding="utf-8") as f:
                self.kb_data = json.load(f)
            elapsed_ms = (time.time() - self.load_start_time) * 1000
            logger.info(f"[KB LOADER] Unified KB loaded successfully ({elapsed_ms:.1f}ms)")
            logger.info(f"[KB LOADER]   - Metadata version: {self.kb_data.get('metadata', {}).get('version')}")
            logger.info(f"[KB LOADER]   - Departments: {len(self.kb_data.get('departments', []))}")
            logger.info(f"[KB LOADER]   - Doctors: {len(self.kb_data.get('doctors', []))}")
            logger.info(f"[KB LOADER]   - Services: {len(self.kb_data.get('services', []))}")
            logger.info(f"[KB LOADER]   - FAQ entries: {len(self.kb_data.get('faq', []))}")
        except Exception as e:
            logger.error(f"[KB LOADER] ❌ Failed to load unified KB: {e}")
            raise
    
    def _load_legacy(self):
        """Load legacy KB system (apollo_metro.json + distilled_facts.json)"""
        logger.info("[KB LOADER] Loading LEGACY knowledge base...")
        
        try:
            # Load apollo_metro.json
            apollo_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "hospital_data" / "apollo_metro.json"
            with open(apollo_path, "r", encoding="utf-8") as f:
                apollo_data = json.load(f)
            
            # Load distilled_facts.json
            facts_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "knowledge" / "distilled_facts.json"
            distilled_facts = []
            if facts_path.exists():
                with open(facts_path, "r", encoding="utf-8") as f:
                    distilled_facts = json.load(f)
            
            self.kb_data = {
                "metadata": {
                    "system": "legacy",
                    "hospital_id": apollo_data.get("id"),
                    "hospital_name": apollo_data.get("name"),
                    "loaded_at": time.time()
                },
                "core_info": apollo_data,
                "distilled_facts": distilled_facts
            }
            
            elapsed_ms = (time.time() - self.load_start_time) * 1000
            logger.info(f"[KB LOADER] Legacy KB loaded successfully ({elapsed_ms:.1f}ms)")
            logger.info(f"[KB LOADER]   - Hospital: {apollo_data.get('name')}")
            logger.info(f"[KB LOADER]   - Departments: {len(apollo_data.get('departments', []))}")
            logger.info(f"[KB LOADER]   - Doctors: {len(apollo_data.get('doctors', []))}")
            logger.info(f"[KB LOADER]   - Distilled facts: {len(distilled_facts)}")
        except Exception as e:
            logger.error(f"[KB LOADER] ❌ Failed to load legacy KB: {e}")
            raise
    
    def get_system_type(self) -> str:
        """Return current KB system type"""
        return self.kb_system
    
    def get_core_info(self) -> Dict[str, Any]:
        """Get hospital core information"""
        if self.kb_system == "unified":
            return self.kb_data.get("core_info", {})
        else:
            return self.kb_data.get("core_info", {})
    
    def get_departments(self) -> List[Dict[str, Any]]:
        """Get list of departments"""
        if self.kb_system == "unified":
            return self.kb_data.get("departments", [])
        else:
            return self.kb_data.get("core_info", {}).get("departments", [])
    
    def get_doctors(self) -> List[Dict[str, Any]]:
        """Get list of doctors"""
        if self.kb_system == "unified":
            return self.kb_data.get("doctors", [])
        else:
            return self.kb_data.get("core_info", {}).get("doctors", [])
    
    def get_services(self) -> List[Dict[str, Any]]:
        """Get list of services"""
        if self.kb_system == "unified":
            return self.kb_data.get("services", [])
        else:
            return self.kb_data.get("core_info", {}).get("services", [])
    
    def get_health_packages(self) -> List[Dict[str, Any]]:
        """Get list of health packages"""
        if self.kb_system == "unified":
            return self.kb_data.get("health_packages", [])
        else:
            return self.kb_data.get("core_info", {}).get("health_packages", [])
    
    def get_room_types(self) -> List[Dict[str, Any]]:
        """Get list of room types"""
        if self.kb_system == "unified":
            return self.kb_data.get("room_types", [])
        else:
            return self.kb_data.get("core_info", {}).get("room_types", [])
    
    def get_faq(self) -> List[Dict[str, Any]]:
        """Get FAQ entries"""
        if self.kb_system == "unified":
            return self.kb_data.get("faq", [])
        else:
            # Convert distilled facts to FAQ-like format for compatibility
            faq = []
            for fact in self.kb_data.get("distilled_facts", []):
                faq.append({
                    "question": fact.get("question"),
                    "answer": fact.get("answer")
                })
            return faq
    
    def get_amenities(self) -> Dict[str, Any]:
        """Get amenities"""
        if self.kb_system == "unified":
            return self.kb_data.get("amenities", {})
        else:
            return self.kb_data.get("core_info", {}).get("amenities", {})
    
    def get_operating_hours(self) -> Dict[str, str]:
        """Get operating hours"""
        if self.kb_system == "unified":
            return self.kb_data.get("operating_hours", {})
        else:
            return self.kb_data.get("core_info", {}).get("operating_hours", {})
    
    def get_insurance_providers(self) -> List[Dict[str, Any]]:
        """Get insurance TPA providers"""
        if self.kb_system == "unified":
            return self.kb_data.get("insurance_tpa", [])
        else:
            # Extract from FAQ if available
            faq = self.get_faq()
            for entry in faq:
                if "insurance" in entry.get("question", "").lower():
                    return entry
            return []
    
    def get_status(self) -> Dict[str, Any]:
        """Get loader status and metrics"""
        return {
            "kb_system": self.kb_system,
            "load_duration_ms": round(self.load_duration_ms, 2),
            "loaded": bool(self.kb_data),
            "data_summary": {
                "departments": len(self.get_departments()),
                "doctors": len(self.get_doctors()),
                "services": len(self.get_services()),
                "packages": len(self.get_health_packages()),
                "faq_entries": len(self.get_faq())
            }
        }

# ============================================================================
# Global KB Instance (Lazy Loaded)
# ============================================================================

_kb_loader: Optional[HospitalKBLoader] = None

def get_kb_loader(kb_system: str = "unified") -> HospitalKBLoader:
    """Get or create global KB loader instance (singleton pattern)"""
    global _kb_loader
    
    if _kb_loader is None:
        _kb_loader = HospitalKBLoader(kb_system=kb_system)
    
    return _kb_loader

def reload_kb_loader(kb_system: str = "unified"):
    """Force reload KB loader (for testing or system updates)"""
    global _kb_loader
    _kb_loader = HospitalKBLoader(kb_system=kb_system)
    return _kb_loader
