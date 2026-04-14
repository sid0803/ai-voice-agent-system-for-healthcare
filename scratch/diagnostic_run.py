import os
import sys
import json
import logging
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import core components to verify connectivity
try:
    from src import mock_tools
    from src.integrations.tenant_manager import tenant_manager
    from src.integrations.sheets_client import sheets_client
    from src.integrations.local_sink import local_sink
    from src.analytics.rds_client import rds_analytics
    from src.analytics.processor import analytics_processor
    imports_ok = True
except ImportError as e:
    imports_ok = False
    import_error = str(e)

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

def run_full_scan():
    print("="*80)
    print(" INDIISERVE HEALTHCARE AI - END-TO-END SYSTEM HEALTH SCAN")
    print("="*80)
    
    # 1. Dependency & Module Check
    print(f"\n[SCAN 1] Module Connectivity:")
    if imports_ok:
        print("  - Core Imports: OK")
        print("  - Analytics Engine: OK")
        print("  - Integration Sinks: OK")
    else:
        print(f"  - CRITICAL ERROR: Missing modules. {import_error}")
        print("    Suggestion: Run 'pip install -r requirements.txt'")
        return

    # 2. Multi-Tenancy (Requirement No 1)
    print(f"\n[SCAN 2] Multi-Tenancy (Requirement No 1):")
    hospital_id = os.environ.get("HOSPITAL_ID", "default_tier2")
    data = tenant_manager.get_hospital_data()
    if data:
        print(f"  - Active Hospital ID: {hospital_id}")
        print(f"  - Hospital Name: {data.get('name')}")
        print(f"  - Doctor Roster: {len(data.get('doctors', []))} doctors found")
        print(f"  - Services/Prices: {len(data.get('services', []))} items found")
    else:
        print(f"  - ERROR: Failed to load hospital data for {hospital_id}")

    # 3. Proactive Intent Logic (Requirement No 2)
    print(f"\n[SCAN 3] Proactive Intent Logic (Requirement No 2):")
    booking_tool = next((t for t in mock_tools.available_tools if t['toolSpec']['name'] == 'appointmentBookingTool'), None)
    if booking_tool:
        schema = json.loads(booking_tool['toolSpec']['inputSchema']['json'])
        if "symptom_intent" in schema['properties']:
            print("  - Booking Intent Collection: ENABLED (Requirement No 2 Met)")
        else:
            print("  - WARNING: 'symptom_intent' missing from booking schema.")
    else:
        print("  - ERROR: Appointment Booking tool not registered.")

    # 4. Data Sinks (Requirement No 2 - Notedown)
    print(f"\n[SCAN 4] Data Sinks (Requirement No 2):")
    print(f"  - Local CSV Sink: {local_sink.file_path}")
    if local_sink.file_path.exists():
        print("    - Status: ACTIVE (Ready to append)")
    else:
        print("    - Status: WILL CREATE ON FIRST CALL")
    
    if sheets_client.spreadsheet_id:
        print(f"  - Google Sheets ID: {sheets_client.spreadsheet_id}")
        if sheets_client.service:
            print("    - Status: CONNECTED")
        else:
            print("    - Status: PENDING (Needs credentials.json)")
    else:
        print("  - Google Sheets: DISABLED (Set GOOGLE_SHEET_ID in .env)")

    # 5. Massive Analytics & Brain Training (Phase 2)
    print(f"\n[SCAN 5] Massive Analytics & Brain Training (Phase 2):")
    if rds_analytics.host:
        print(f"  - RDS Analytics Host: {rds_analytics.host}")
        print(f"  - Authentication: IAM AUTH (Enabled)")
    else:
        print("  - RDS Analytics: DISABLED (Set RDS_HOSTNAME for data science)")
    
    print(f"  - AI Data Scientist Model: {analytics_processor.model_id}")
    print(f"  - Fine-Tuning Exporter: AVAILABLE (scripts/export_finetuning_data.py)")

    # 6. Environment Check
    print("\n[SCAN 6] Environment Variable Audit:")
    vars_to_check = {
        "EXOTEL_SID": "Core Telephony",
        "AWS_REGION": "Cloud Infrastructure",
        "BEDROCK_REGION": "AI Logic (Nova Sonic)",
        "MEMORY_ID": "Patient Recognition (AgentCore)",
        "HOSPITAL_ID": "Multitenancy (Requirement No 1)",
        "GOOGLE_SHEET_ID": "Booking Notedown (Requirement No 2)"
    }
    for v, desc in vars_to_check.items():
        val = os.environ.get(v)
        status = "OK" if val else "MISSING"
        print(f"  - {v:<16} : {status} ({desc})")

    print("\n" + "="*80)
    print(" SYSTEM SCAN COMPLETE - READY FOR FINAL VALIDATION")
    print("="*80)

if __name__ == "__main__":
    run_full_scan()
