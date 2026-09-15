import csv
import os
import time
import json
import logging
from datetime import datetime
from pathlib import Path
import threading
from typing import Optional

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
                        booking_data.get("status", booking_data.get("action_status", "CONFIRMED")),
                        booking_data.get("assigned_to", "Unassigned"),
                        "AI_CALL",
                        booking_data.get("decision_reason", "N/A")
                    ])
            logger.info(f"[SINK] Booking ({urgency}) noted down to local CSV: {booking_data.get('ref_id')}")
            return True
        except Exception:
            logger.exception("Failed to save booking to local sink")
            return False


class AuthoritativeBookingStore:
    """Authoritative slot and appointment store with persistent file backing, atomic conditional reservations, TTL cleanup, idempotency keys, and zero-loss lifecycle state transitions."""
    
    def __init__(self, local_sink_instance: LocalBookingSink, storage_file: str = "data/bookings/authoritative_appointments.json"):
        self.sink = local_sink_instance
        self.storage_file = storage_file
        self._lock = threading.Lock()
        # Active confirmed appointments: ref_id -> dict
        self._appointments: dict[str, dict] = {}
        # In-flight holds: slot_id -> {"reservation_id": ..., "session_id": ..., "expires_at": float, ...}
        self._reservations: dict[str, dict] = {}
        # Index: (doctor_id, date_iso, time_24h) -> ref_id / reservation_id
        self._slot_index: dict[tuple[str, str, str], str] = {}
        # Idempotency cache: idempotency_key -> booking_result_dict
        self._idempotency_cache: dict[str, dict] = {}
        
        # Load persisted appointments on startup
        self._load_persisted_state()

    def _load_persisted_state(self):
        """Reload active confirmed appointments from persistent storage on startup/restart."""
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    appts = data.get("appointments", {})
                    for ref_id, appt in appts.items():
                        if appt.get("status") == "CONFIRMED":
                            self._appointments[ref_id] = appt
                            tup = (appt.get("doctor_id"), appt.get("date_iso"), appt.get("time_24h"))
                            self._slot_index[tup] = ref_id
                    logger.info("[APPOINTMENT-STORE] Reloaded %d confirmed appointments from persistent store (%s)", len(self._appointments), self.storage_file)
        except Exception as e:
            logger.error("[APPOINTMENT-STORE] Failed to load persisted state: %s", e)

    def _save_persisted_state(self):
        """Atomically persist active appointments to disk."""
        try:
            os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)
            tmp_file = f"{self.storage_file}.tmp"
            payload = {
                "appointments": self._appointments,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, self.storage_file)
        except Exception as e:
            logger.error("[APPOINTMENT-STORE] Failed to persist state to disk: %s", e)

    def clear(self):
        """Clear all active appointments, reservations, and indexes (used for test isolation)."""
        with self._lock:
            self._appointments.clear()
            self._reservations.clear()
            self._slot_index.clear()
            self._idempotency_cache.clear()
            if os.path.exists(self.storage_file):
                try:
                    os.remove(self.storage_file)
                except Exception:
                    pass

    def _cleanup_expired_holds(self):
        """Purge expired in-flight holds."""
        now = time.time()
        expired_slots = [
            slot_key for slot_key, hold in self._reservations.items()
            if hold.get("expires_at", 0) <= now
        ]
        for slot_key in expired_slots:
            hold = self._reservations.pop(slot_key, None)
            if hold:
                tup = (hold["doctor_id"], hold["date_iso"], hold["time_24h"])
                if self._slot_index.get(tup) == hold["reservation_id"]:
                    del self._slot_index[tup]
                logger.info("[APPOINTMENT-STORE] Expired in-flight hold for slot %s (Reservation: %s)", slot_key, hold["reservation_id"])

    def get_occupied_slots(self, doctor_id: str, date_iso: str) -> set[str]:
        """Return set of 24h times that are currently occupied (CONFIRMED or unexpired RESERVED)."""
        with self._lock:
            self._cleanup_expired_holds()
            occupied = set()
            for appt in self._appointments.values():
                if appt.get("status") == "CONFIRMED":
                    if appt.get("doctor_id") == doctor_id and appt.get("date_iso") == date_iso:
                        occupied.add(appt.get("time_24h"))
            for hold in self._reservations.values():
                if hold.get("doctor_id") == doctor_id and hold.get("date_iso") == date_iso:
                    occupied.add(hold.get("time_24h"))
            return occupied

    def reserve_slot(self, doctor_id: str, date_iso: str, time_24h: str, session_id: str, ttl_seconds: int = 300) -> tuple[bool, str, str]:
        """Atomically reserve an in-flight hold on a slot. Returns (success, reservation_id, message)."""
        import uuid
        with self._lock:
            self._cleanup_expired_holds()
            tup = (doctor_id, date_iso, time_24h)
            slot_id = f"SLOT#{doctor_id}#{date_iso}#{time_24h}"
            
            if tup in self._slot_index:
                holder = self._slot_index[tup]
                return False, "", f"Slot {time_24h} on {date_iso} is already booked or reserved by another caller."
            
            res_id = f"RES-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
            expires_at = time.time() + ttl_seconds
            
            self._reservations[slot_id] = {
                "slot_id": slot_id,
                "doctor_id": doctor_id,
                "date_iso": date_iso,
                "time_24h": time_24h,
                "reservation_id": res_id,
                "session_id": session_id,
                "expires_at": expires_at,
                "status": "RESERVED"
            }
            self._slot_index[tup] = res_id
            logger.info("[APPOINTMENT-STORE] Reserved slot %s for session %s (ResID: %s, TTL: %ds)", slot_id, session_id[:8], res_id, ttl_seconds)
            return True, res_id, "Slot reserved successfully."

    def release_reservation(self, reservation_id: str, session_id: str) -> bool:
        """Release an in-flight hold owned by session_id."""
        with self._lock:
            found_slot = None
            for slot_key, hold in self._reservations.items():
                if hold.get("reservation_id") == reservation_id and hold.get("session_id") == session_id:
                    found_slot = slot_key
                    break
            if found_slot:
                hold = self._reservations.pop(found_slot)
                tup = (hold["doctor_id"], hold["date_iso"], hold["time_24h"])
                if self._slot_index.get(tup) == reservation_id:
                    del self._slot_index[tup]
                logger.info("[APPOINTMENT-STORE] Released reservation %s for slot %s", reservation_id, found_slot)
                return True
            return False

    def commit_booking(self, booking_payload: dict, reservation_id: str = None, session_id: str = None, transaction_id: str = None) -> tuple[bool, str, dict]:
        """Atomically commit a confirmed booking with idempotency key guarantee. Converts reservation to CONFIRMED."""
        with self._lock:
            self._cleanup_expired_holds()
            
            # Idempotency check: session_id + transaction_id
            idempotency_key = f"{session_id or 'default'}#{transaction_id or booking_payload.get('ref_id', '')}"
            if idempotency_key in self._idempotency_cache:
                logger.info("[APPOINTMENT-STORE] Idempotent hit for key %s. Returning existing booking confirmation.", idempotency_key)
                return True, "Booking retrieved via idempotency cache.", self._idempotency_cache[idempotency_key]

            doc_id = booking_payload.get("doctor_id")
            date_iso = booking_payload.get("date_iso")
            time_24h = booking_payload.get("time_24h")
            ref_id = booking_payload.get("ref_id")
            tup = (doc_id, date_iso, time_24h)
            slot_id = f"SLOT#{doc_id}#{date_iso}#{time_24h}"
            
            if slot_id in self._reservations:
                hold = self._reservations[slot_id]
                if reservation_id and hold.get("reservation_id") != reservation_id:
                    return False, "Slot is reserved by another session.", {}
                del self._reservations[slot_id]
            elif tup in self._slot_index and self._slot_index[tup] != ref_id:
                return False, f"Slot {time_24h} on {date_iso} is already confirmed by another appointment.", {}
            
            booking_payload["status"] = "CONFIRMED"
            booking_payload["committed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._appointments[ref_id] = booking_payload
            self._slot_index[tup] = ref_id
            self._idempotency_cache[idempotency_key] = booking_payload
            
            # Persist to disk and CSV sink
            self._save_persisted_state()
            self.sink.save_booking(booking_payload)
            logger.info("[APPOINTMENT-STORE] COMMITTED booking %s for Dr. %s at %s %s", ref_id, doc_id, date_iso, time_24h)
            return True, "Booking committed successfully.", booking_payload

    def atomic_reschedule(self, old_ref_id: str, new_booking_payload: dict, session_id: str = None) -> tuple[bool, str, dict]:
        """Atomically reschedule an appointment: reserves new slot, marks old RESCHEDULED, commits new. If new fails, old remains untouched."""
        with self._lock:
            self._cleanup_expired_holds()
            old_appt = self._appointments.get(old_ref_id)
            if not old_appt or old_appt.get("status") != "CONFIRMED":
                return False, f"Original appointment {old_ref_id} not found or not in active confirmed state.", {}
            
            new_doc_id = new_booking_payload.get("doctor_id")
            new_date_iso = new_booking_payload.get("date_iso")
            new_time_24h = new_booking_payload.get("time_24h")
            new_ref_id = new_booking_payload.get("ref_id")
            new_tup = (new_doc_id, new_date_iso, new_time_24h)
            new_slot_id = f"SLOT#{new_doc_id}#{new_date_iso}#{new_time_24h}"
            
            if new_tup in self._slot_index and self._slot_index[new_tup] != old_ref_id:
                return False, f"New requested slot {new_time_24h} on {new_date_iso} is occupied. Original appointment {old_ref_id} remains active.", old_appt
            
            old_tup = (old_appt.get("doctor_id"), old_appt.get("date_iso"), old_appt.get("time_24h"))
            if old_tup in self._slot_index:
                del self._slot_index[old_tup]
            old_appt["status"] = "RESCHEDULED"
            old_appt["rescheduled_to"] = new_ref_id
            old_appt["rescheduled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            
            new_booking_payload["status"] = "CONFIRMED"
            new_booking_payload["committed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            new_booking_payload["rescheduled_from"] = old_ref_id
            self._appointments[new_ref_id] = new_booking_payload
            self._slot_index[new_tup] = new_ref_id
            
            self._save_persisted_state()
            self.sink.save_booking(new_booking_payload)
            logger.info("[APPOINTMENT-STORE] ATOMIC RESCHEDULE SUCCESS: %s -> %s (Slot: %s)", old_ref_id, new_ref_id, new_slot_id)
            return True, "Appointment rescheduled successfully.", new_booking_payload

    def cancel_appointment(self, ref_id: str, authorized_patient_id: str = None) -> tuple[bool, str, dict]:
        """Cancel an appointment and release its slot lock."""
        with self._lock:
            self._cleanup_expired_holds()
            appt = self._appointments.get(ref_id)
            if not appt or appt.get("status") != "CONFIRMED":
                return False, f"Appointment {ref_id} not found or not in active confirmed state.", {}
            
            if authorized_patient_id and appt.get("patient_id") and appt.get("patient_id") != authorized_patient_id:
                return False, f"Unauthorized: Patient identity does not match appointment record.", {}
                
            tup = (appt.get("doctor_id"), appt.get("date_iso"), appt.get("time_24h"))
            if tup in self._slot_index and self._slot_index[tup] == ref_id:
                del self._slot_index[tup]
            appt["status"] = "CANCELLED"
            appt["cancelled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            
            self._save_persisted_state()
            logger.info("[APPOINTMENT-STORE] CANCELLED appointment %s and released slot %s", ref_id, tup)
            return True, "Appointment cancelled successfully.", appt

    def get_appointment(self, ref_id: str) -> Optional[dict]:
        """Retrieve single appointment by reference ID."""
        with self._lock:
            return self._appointments.get(ref_id)

    def lookup_appointments(self, patient_id: str = None, phone: str = None, appointment_ref: str = None) -> list[dict]:
        """Retrieve active upcoming appointments matching patient_id, phone, or appointment_ref."""
        with self._lock:
            self._cleanup_expired_holds()
            results = []
            for appt in self._appointments.values():
                if appt.get("status") == "CONFIRMED":
                    if appointment_ref and appt.get("ref_id") == appointment_ref:
                        results.append(appt)
                    elif patient_id and appt.get("patient_id") == patient_id:
                        results.append(appt)
                    elif phone and (appt.get("phone_number") == phone or appt.get("phone") == phone):
                        results.append(appt)
            return results

    def get_appointment_history(self, patient_id: str = None, phone: str = None) -> list[dict]:
        """Retrieve all appointments (CONFIRMED, COMPLETED, RESCHEDULED, CANCELLED) for patient."""
        with self._lock:
            results = []
            for appt in self._appointments.values():
                if patient_id and appt.get("patient_id") == patient_id:
                    results.append(appt)
                elif phone and (appt.get("phone_number") == phone or appt.get("phone") == phone):
                    results.append(appt)
            return results


# Singleton instances for process-wide authoritative booking management
local_sink = LocalBookingSink()
booking_store = AuthoritativeBookingStore(local_sink)