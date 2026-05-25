import csv
import os
import logging
from datetime import datetime
from pathlib import Path
import threading

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

class LocalBookingSink:
    """Requirement No 2: 'Notedown' booking data to a local CSV/Excel-compatible file."""
    
    def __init__(self, output_dir: Path = _PROJECT_ROOT / "data" / "bookings"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.output_dir / "hospital_bookings.csv"
        self._lock = threading.Lock()
        self._recent_criticals: dict = {}  # [LOW FIX] Track criticals for anti-spam
        self._ensure_header()

    def _ensure_header(self):
        """Standard columns + Clinical Actionable columns."""
        with self._lock:
            if not self.file_path.exists():
                with open(self.file_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "Timestamp", "Patient Name", "Phone", "Doctor", "Department", 
                        "Visit Date/Time", "Reference ID", "Intent/Needs",
                        "Urgency", "Action Status", "Assigned To", "Source", "Decision Reason"
                    ])

    def _rotate_files(self):
        """Rotate hospital_bookings.csv if it exceeds 10MB."""
        max_size = 10 * 1024 * 1024  # 10MB
        if self.file_path.exists() and self.file_path.stat().st_size > max_size:
            for i in range(4, 0, -1):
                src = self.file_path.with_name(f"hospital_bookings.csv.{i}")
                dst = self.file_path.with_name(f"hospital_bookings.csv.{i+1}")
                if src.exists():
                    try:
                        if dst.exists():
                            dst.unlink()
                        src.rename(dst)
                    except Exception:
                        pass
            dst = self.file_path.with_name("hospital_bookings.csv.1")
            try:
                if dst.exists():
                    dst.unlink()
                self.file_path.rename(dst)
            except Exception:
                pass
            with open(self.file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Timestamp", "Patient Name", "Phone", "Doctor", "Department", 
                    "Visit Date/Time", "Reference ID", "Intent/Needs",
                    "Urgency", "Action Status", "Assigned To", "Source", "Decision Reason"
                ])

    def save_booking(self, booking_data: dict):
        """Append booking with clinical metadata. Implements 10-min Anti-spam merge."""
        try:
            phone = booking_data.get("phone", "N/A")
            urgency = booking_data.get("priority", "NORMAL")
            
            # Anti-spam: Skip duplicate CRITICAL entries from same phone in last 10 mins
            if urgency == "CRITICAL" and phone != "N/A":
                import time
                now = time.time()
                last_time = self._recent_criticals.get(phone, 0)
                if now - last_time < 600:  # 10 minutes
                    logger.info(f"[SINK] Anti-spam blocked duplicate CRITICAL for {phone}")
                    return True
                self._recent_criticals[phone] = now

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._lock:
                self._rotate_files()
                with open(self.file_path, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp,
                        booking_data.get("patient_name", "Unknown"),
                        phone,
                        booking_data.get("doctor", "Unspecified"),
                        booking_data.get("dept", "General"),
                        booking_data.get("visit_time", "N/A"),
                        booking_data.get("ref_id", "N/A"),
                        booking_data.get("intent", "Checkup/Inquiry"),
                        urgency,
                        booking_data.get("action_status", "PENDING"),
                        booking_data.get("assigned_to", "Unassigned"),
                        "AI_CALL",
                        booking_data.get("decision_reason", "N/A")
                    ])
            logger.info(f"[SINK] Booking ({urgency}) noted down to local CSV: {booking_data.get('ref_id')}")
            return True
        except Exception:
            logger.exception("Failed to save booking to local sink")
            return False

# Global instance
local_sink = LocalBookingSink()
