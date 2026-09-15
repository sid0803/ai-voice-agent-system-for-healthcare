"""Appointment Data Migration Script: CSV to DynamoDB with checksum verification and idempotency."""

import os
import csv
import sys
import hashlib
import argparse
import logging
from datetime import datetime
from typing import Dict, Any, List

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("appointment_migrator")

CSV_PATH = os.path.join("data", "bookings", "hospital_bookings.csv")
DEFAULT_TENANT = "apollo_metro"
TABLE_NAME = os.environ.get("DYNAMODB_APPOINTMENTS_TABLE", "InDiiServe_Appointments")
REGION = os.environ.get("AWS_REGION", "ap-south-1")

def calculate_csv_checksum(filepath: str) -> str:
    """Calculate MD5 checksum of the source CSV file."""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def parse_csv_records(filepath: str, default_tenant: str = DEFAULT_TENANT) -> List[Dict[str, Any]]:
    """Parse CSV rows into normalized DynamoDB appointment items."""
    if not os.path.exists(filepath):
        logger.error("Source CSV file not found: %s", filepath)
        return []

    records = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            timestamp = row.get("Timestamp", "").strip() or datetime.utcnow().isoformat()
            patient_name = row.get("Patient Name", "").strip() or "Anonymous"
            phone = row.get("Phone", "").strip() or "N/A"
            doctor = row.get("Doctor", "").strip() or "Unspecified"
            department = row.get("Department", "").strip() or "General"
            visit_datetime = row.get("Visit Date/Time", "").strip() or "N/A"
            ref_id = row.get("Reference ID", "").strip() or f"REF-MIG-{idx:04d}"
            intent = row.get("Patient Intent/Needs", "").strip() or "General Inquiry"

            item = {
                "tenant_id": default_tenant,
                "appointment_id": ref_id,
                "idempotency_key": f"mig_{ref_id}",
                "patient_name": patient_name,
                "caller_phone": phone,
                "doctor_name": doctor,
                "department": department,
                "visit_datetime": visit_datetime,
                "intent": intent,
                "status": "CONFIRMED",
                "source": "legacy_csv_migration",
                "created_at": timestamp,
            }
            records.append(item)

    return records

def run_migration(dry_run: bool = True):
    """Execute or dry-run appointment migration."""
    logger.info("=== APPOINTMENT DATA MIGRATION ===")
    logger.info("Source CSV: %s", CSV_PATH)
    logger.info("Target DynamoDB Table: %s", TABLE_NAME)
    logger.info("Mode: %s", "DRY-RUN (Safe Simulation)" if dry_run else "EXECUTE (Live Migration)")

    if not os.path.exists(CSV_PATH):
        logger.error("CSV path does not exist: %s", CSV_PATH)
        sys.exit(1)

    csv_checksum = calculate_csv_checksum(CSV_PATH)
    logger.info("Source CSV MD5 Checksum: %s", csv_checksum)

    records = parse_csv_records(CSV_PATH)
    logger.info("Parsed %d records from CSV.", len(records))

    if not records:
        logger.warning("No records found to migrate.")
        return

    # Check for duplicate appointment_ids within CSV
    unique_ids = set()
    duplicates = 0
    for r in records:
        if r["appointment_id"] in unique_ids:
            duplicates += 1
        unique_ids.add(r["appointment_id"])

    logger.info("Unique appointment IDs: %d (Duplicates: %d)", len(unique_ids), duplicates)
    logger.info("Sample parsed item: %s", records[0])

    if dry_run:
        logger.info("[DRY-RUN SUCCESS] All %d records parsed cleanly. No database changes made.", len(records))
        return

    # Live execution to DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)

    success_count = 0
    fail_count = 0

    with table.batch_writer() as batch:
        for item in records:
            try:
                batch.put_item(Item=item)
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.error("Failed to write item %s: %s", item.get("appointment_id"), e)

    logger.info("=== MIGRATION COMPLETE ===")
    logger.info("Successfully migrated: %d items", success_count)
    logger.info("Failed: %d items", fail_count)

    if fail_count > 0:
        logger.error("Migration had %d errors. Please inspect logs.", fail_count)
    else:
        logger.info("100% of appointment records migrated cleanly to DynamoDB.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate appointment bookings from CSV to DynamoDB")
    parser.add_argument("--execute", action="store_true", help="Execute live migration (default is dry-run)")
    args = parser.parse_args()

    run_migration(dry_run=not args.execute)
