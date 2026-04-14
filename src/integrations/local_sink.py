import csv
import os
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

class LocalBookingSink:
    """Requirement No 2: 'Notedown' booking data to a local CSV/Excel-compatible file."""
    
    def __init__(self, output_dir: str = "data/bookings"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.output_dir / "hospital_bookings.csv"
        self._ensure_header()

    def _ensure_header(self):
        """Standard columns: Timestamp, Patient Name, Phone, Doctor, Department, Visit Date/Time, Reference ID, Patient Intent."""
        if not self.file_path.exists():
            with open(self.file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Timestamp", 
                    "Patient Name", 
                    "Phone", 
                    "Doctor", 
                    "Department", 
                    "Visit Date/Time", 
                    "Reference ID", 
                    "Patient Intent/Needs"
                ])

    def save_booking(self, booking_data: dict):
        """Append a new booking entry to the CSV file."""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp,
                    booking_data.get("patient_name", "Unknown"),
                    booking_data.get("phone", "N/A"),
                    booking_data.get("doctor", "Unspecified"),
                    booking_data.get("dept", "General"),
                    booking_data.get("visit_time", "N/A"),
                    booking_data.get("ref_id", "N/A"),
                    booking_data.get("intent", "Checkup/Inquiry")
                ])
            logger.info(f"[SINK] Booking noted down to local CSV: {booking_data.get('ref_id')}")
            return True
        except Exception:
            logger.exception("Failed to save booking to local sink")
            return False

# Global instance
local_sink = LocalBookingSink()
