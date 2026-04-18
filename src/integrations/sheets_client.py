import os
import logging
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    """Requirement No 2: 'Notedown' booking data to Google Sheets."""
    
    def __init__(self):
        self.spreadsheet_id = os.environ.get("GOOGLE_SHEET_ID")
        self.creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        self.service = None
        self._init_service()

    def _init_service(self):
        """Initialize the Google Sheets API service."""
        if not self.spreadsheet_id:
            logger.warning("[SHEETS] GOOGLE_SHEET_ID not set. Sheets integration disabled.")
            return

        if not os.path.exists(self.creds_path):
            logger.warning(f"[SHEETS] Credentials file {self.creds_path} not found. Sheets integration disabled.")
            return

        try:
            creds = service_account.Credentials.from_service_account_file(
                self.creds_path, 
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            self.service = build('sheets', 'v4', credentials=creds)
            logger.info("[SHEETS] Google Sheets service initialized successfully.")
        except Exception:
            logger.exception("[SHEETS] Failed to initialize Google Sheets service")

    def append_booking(self, booking_data: dict, hospital_id: str = None):
        """Requirement No 2: Append booking data with Intent/Needs column to clinic-specific sheet."""
        if not self.service:
            logger.info("[SHEETS] Sheets service unavailable, skipping cloud upload.")
            return False

        # Resolve target spreadsheet for multi-tenancy
        from src.integrations.tenant_manager import tenant_manager
        target_sheet_id = tenant_manager.get_sheets_id(hospital_id) if hospital_id else self.spreadsheet_id
        
        if not target_sheet_id:
            logger.error("[SHEETS] No target spreadsheet ID found. Cannot append booking.")
            return False

        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            urgency = booking_data.get("priority", "NORMAL")
            
            values = [[
                timestamp,
                booking_data.get("patient_name", "Unknown"),
                booking_data.get("phone", "N/A"),
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
            ]]
            
            body = {'values': values}
            result = self.service.spreadsheets().values().append(
                spreadsheetId=target_sheet_id,
                range="Sheet1!A:M",
                valueInputOption="RAW",
                body=body
            ).execute()
            
            logger.info(f"[SHEETS] Successfully appended clinical booking ({urgency}) to {target_sheet_id}")
            return True
        except Exception:
            logger.exception(f"[SHEETS] Error appending to Google Sheets ({target_sheet_id})")
            return False

# Global instance
sheets_client = GoogleSheetsClient()
