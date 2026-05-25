import logging
import os
import pathlib
from datetime import datetime, timezone, timedelta

# Metadata for Audit Trail
IST = timezone(timedelta(hours=5, minutes=30))

class SecurityAuditLogger:
    """Enterprise-grade audit logger for healthcare data access and system events."""
    
    def __init__(self):
        log_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        self.audit_file = log_dir / "security_audit.log"
        
        # Dedicated logger that doesn't propagate to console by default (privacy)
        self.logger = logging.getLogger("AshaAudit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        
        if not self.logger.handlers:
            from logging.handlers import RotatingFileHandler
            handler = RotatingFileHandler(self.audit_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            
    def _get_timestamp(self):
        return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    def log_event(self, session_id: str, action: str, hospital_id: str, context: dict = None):
        """Log a security event to the audit trail."""
        timestamp = self._get_timestamp()
        ctx_str = f" | Context: {context}" if context else ""
        log_msg = f"Session: {session_id} | Hospital: {hospital_id} | Action: {action}{ctx_str}"
        self.logger.info(log_msg)
        
    def log_access(self, session_id: str, hospital_id: str, data_type: str, patient_id: str = "Unknown"):
        """Log access to sensitive patient data."""
        self.log_event(
            session_id, 
            "DATA_ACCESS", 
            hospital_id, 
            {"data_type": data_type, "patient_id": patient_id}
        )

    def log_tool_use(self, session_id: str, hospital_id: str, tool_name: str, success: bool = True):
        """Log execution of clinical/operational tools."""
        self.log_event(
            session_id, 
            "TOOL_EXECUTION", 
            hospital_id, 
            {"tool": tool_name, "status": "SUCCESS" if success else "FAILED"}
        )

# Global singleton
audit_logger = SecurityAuditLogger()
