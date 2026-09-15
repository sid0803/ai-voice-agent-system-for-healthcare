import boto3
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger(__name__)

class TriageStore:
    """Thread-safe persistent store for clinical triage records in DynamoDB."""
    def __init__(self):
        self.table_name = os.getenv("TRIAGE_TABLE_NAME", "InDiiServe_Triage_Events")
        self.region = os.getenv("AWS_REGION", "ap-south-1")
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        self._client = boto3.client("dynamodb", region_name=self.region)
                    except Exception as e:
                        logger.warning(f"[TRIAGE-STORE] Could not initialize DynamoDB client: {e}")
        return self._client

    def write(self, hospital_id: str, symptoms: str, pain: any, priority: str, dept: str, is_emergency: bool = False):
        client = self._get_client()
        if not client:
            logger.info(f"[TRIAGE-STORE] (Fallback) Logged triage event for {hospital_id}: {priority} - {dept}")
            return

        def _do_put():
            try:
                client.put_item(
                    TableName=self.table_name,
                    Item={
                        "event_id": {"S": str(uuid.uuid4())},
                        "timestamp": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ")},
                        "hospital_id": {"S": str(hospital_id or "default")},
                        "symptoms": {"S": str(symptoms)},
                        "pain": {"S": str(pain if pain is not None else "N/A")},
                        "priority": {"S": str(priority)},
                        "department": {"S": str(dept)},
                        "emergency": {"BOOL": bool(is_emergency)},
                    }
                )
                logger.info("[TRIAGE-STORE] Recorded triage event to DynamoDB.")
            except Exception as e:
                logger.warning(f"[TRIAGE-STORE] Failed to put item to DynamoDB: {e}")

        # Fire and forget in a daemon thread so it doesn't block clinical tool response
        threading.Thread(target=_do_put, daemon=True).start()

triage_store = TriageStore()
