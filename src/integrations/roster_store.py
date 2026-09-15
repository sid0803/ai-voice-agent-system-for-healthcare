"""Dynamic Doctor Roster & Live Availability Store.

Enables hospital front-desk staff to mark doctors as:
- AVAILABLE (Normal OPD schedule)
- ON_LEAVE (Doctor on emergency / planned leave — Asha routes callers to alternatives)
- RUNNING_LATE (Doctor delayed by X minutes due to emergency surgery / traffic)
- IN_SURGERY (Doctor temporarily in OT)

Provides sub-millisecond in-memory caching to avoid latency regressions during
live Exotel voice calls.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ROSTER_FILE = _PROJECT_ROOT / "data" / "doctor_roster.json"
_UNIFIED_KB_FILE = _PROJECT_ROOT / "data" / "unified_hospital_kb.json"

VALID_STATUSES = {"AVAILABLE", "ON_LEAVE", "RUNNING_LATE", "IN_SURGERY"}


class DoctorRosterStore:
    """Thread-safe, low-latency store for dynamic doctor availability overrides."""

    def __init__(self, storage_path: Optional[Path] = None):
        self._path = storage_path or _ROSTER_FILE
        self._lock = threading.Lock()
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._last_loaded: float = 0.0
        self._init_store()

    def _init_store(self) -> None:
        """Initialize roster store from file or seed from unified_hospital_kb.json."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._memory_cache = data
                    self._last_loaded = time.time()
                    return
            except Exception as e:
                logger.warning("[ROSTER] Failed to read %s, re-seeding: %s", self._path, e)

        # Seed from unified KB
        self._seed_from_kb()

    def _seed_from_kb(self) -> None:
        """Seed doctor roster using doctors listed in unified_hospital_kb.json."""
        roster: Dict[str, Dict[str, Any]] = {}
        try:
            if _UNIFIED_KB_FILE.exists():
                kb = json.loads(_UNIFIED_KB_FILE.read_text(encoding="utf-8"))
                for doc in kb.get("doctors", []):
                    doc_id = doc.get("id") or doc.get("name", "").lower().replace(" ", "_").replace(".", "")
                    doc_name = doc.get("name", "Unknown Doctor")
                    roster[doc_id] = {
                        "doctor_id": doc_id,
                        "doctor_name": doc_name,
                        "department": doc.get("department", "General"),
                        "status": "AVAILABLE",
                        "delay_minutes": 0,
                        "reason": "",
                        "return_date": "",
                        "alternative_doctor": "",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_by": "system_seed",
                    }
        except Exception as e:
            logger.error("[ROSTER] Error seeding from KB: %s", e)

        # Fallback default doctors if KB had none
        if not roster:
            for name, dept in [
                ("Dr. Amit Sharma", "Cardiology"),
                ("Dr. Priya Patel", "General Medicine"),
                ("Dr. Rajesh Kulkarni", "Orthopedics"),
                ("Dr. Sneha Roy", "Pediatrics"),
            ]:
                doc_id = name.lower().replace(" ", "_").replace(".", "")
                roster[doc_id] = {
                    "doctor_id": doc_id,
                    "doctor_name": name,
                    "department": dept,
                    "status": "AVAILABLE",
                    "delay_minutes": 0,
                    "reason": "",
                    "return_date": "",
                    "alternative_doctor": "",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "updated_by": "system_seed",
                }

        with self._lock:
            self._memory_cache = roster
            self._save_to_disk()

    def _save_to_disk(self) -> None:
        """Persist current memory cache to disk atomically."""
        try:
            tmp_file = self._path.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(self._memory_cache, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_file.replace(self._path)
        except Exception as e:
            logger.error("[ROSTER] Failed to persist roster to %s: %s", self._path, e)

    def get_all_rosters(self, hospital_id: str = "apollo_metro") -> List[Dict[str, Any]]:
        """Return a list of all doctor roster entries."""
        with self._lock:
            return list(self._memory_cache.values())

    def list_all(self, hospital_id: str = "apollo_metro") -> List[Dict[str, Any]]:
        """Alias for get_all_rosters."""
        return self.get_all_rosters(hospital_id)

    def get_doctor_status(self, doctor_query: str, hospital_id: str = "apollo_metro") -> Dict[str, Any]:
        """Look up live status for a doctor by ID or name (fuzzy match)."""
        if not doctor_query:
            return {"status": "AVAILABLE", "delay_minutes": 0}

        query_clean = doctor_query.lower().strip()
        with self._lock:
            # 1. Exact ID match
            if query_clean in self._memory_cache:
                return dict(self._memory_cache[query_clean])

            # 2. Match by full or partial name
            for doc_id, data in self._memory_cache.items():
                name_clean = data.get("doctor_name", "").lower()
                q_words = [w for w in query_clean.replace("dr.", "").replace("dr", "").split() if len(w) > 2]
                if name_clean == query_clean:
                    return dict(data)
                if q_words and any(w in name_clean for w in q_words):
                    return dict(data)

        return {"status": "AVAILABLE", "delay_minutes": 0}

    def update_doctor_status(
        self,
        doctor_id: str,
        status: str,
        delay_minutes: int = 0,
        reason: str = "",
        return_date: str = "",
        alternative_doctor: str = "",
        updated_by: str = "staff",
        hospital_id: str = "apollo_metro",
    ) -> Dict[str, Any]:
        """Update live status for a doctor."""
        status_norm = status.upper().strip()
        if status_norm not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")

        clean_id = doctor_id.lower().strip().replace(" ", "_").replace(".", "")
        with self._lock:
            entry = self._memory_cache.get(clean_id)
            if not entry:
                for k, v in self._memory_cache.items():
                    if v.get("doctor_name", "").lower() == doctor_id.lower():
                        clean_id = k
                        entry = v
                        break

            if not entry:
                entry = {
                    "doctor_id": clean_id,
                    "doctor_name": doctor_id,
                    "department": "General",
                }

            entry["status"] = status_norm
            entry["delay_minutes"] = max(0, int(delay_minutes)) if status_norm == "RUNNING_LATE" else 0
            entry["reason"] = reason.strip()
            entry["return_date"] = return_date.strip()
            entry["alternative_doctor"] = alternative_doctor.strip()
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            entry["updated_by"] = updated_by

            self._memory_cache[clean_id] = entry
            self._save_to_disk()
            logger.info(
                "[ROSTER] Doctor %s status updated to %s (delay=%d min, reason=%s) by %s",
                clean_id,
                status_norm,
                entry["delay_minutes"],
                entry["reason"],
                updated_by,
            )
            return dict(entry)


# Global singleton
roster_store = DoctorRosterStore()
