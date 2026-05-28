"""DynamoDB Analytics Client for InDiiServe."""

import os
import logging
import decimal
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet
import boto3

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

def _convert_decimals(item):
    """Recursively convert DynamoDB Decimal types to standard Python int or float."""
    if isinstance(item, list):
        return [_convert_decimals(v) for v in item]
    elif isinstance(item, dict):
        return {k: _convert_decimals(v) for k, v in item.items()}
    elif isinstance(item, decimal.Decimal):
        if item % 1 == 0:
            return int(item)
        else:
            return float(item)
    return item
def _convert_floats_to_decimals(item):
    """Recursively convert Python float types to Decimal for DynamoDB writing."""
    if isinstance(item, list):
        return [_convert_floats_to_decimals(v) for v in item]
    elif isinstance(item, dict):
        return {k: _convert_floats_to_decimals(v) for k, v in item.items()}
    elif isinstance(item, float):
        return decimal.Decimal(str(item))
    return item

class DynamoDBAnalyticsClient:
    """Serverless AWS DynamoDB Analytics and Multi-Tenant Config Pipeline."""
    
    def __init__(self):
        self.region = os.environ.get("AWS_REGION", "ap-south-1")
        self.table_name = os.environ.get("DYNAMODB_ANALYTICS_TABLE", "InDiiServe_Asha_Analytics")
        self.tenants_table_name = os.environ.get("DYNAMODB_TENANTS_TABLE", "InDiiServe_Tenants")
        self.users_table_name = os.environ.get("DYNAMODB_USERS_TABLE", "InDiiServe_Users")
        
        # Lazy initialization
        self._table = None
        self._tenants_table = None
        self._users_table = None
        
        # PII Encryption Key
        self.encryption_key = os.environ.get("ENCRYPTION_KEY")
        if self.encryption_key:
            try:
                self._cipher = Fernet(self.encryption_key.encode())
            except Exception as e:
                logger.error("[SECURITY] Invalid ENCRYPTION_KEY. PII encryption DISABLED.", exc_info=True)
                self._cipher = None
        else:
            logger.warning("[SECURITY] ENCRYPTION_KEY not set. PII encryption DISABLED.")
            self._cipher = None

    def _get_table(self):
        """Lazily create the DynamoDB Table resource for analytics on first use."""
        if self._table is None:
            self._table = boto3.Session(region_name=self.region).resource("dynamodb").Table(self.table_name)
        return self._table

    def _get_tenants_table(self):
        """Lazily create the DynamoDB Table resource for tenants on first use."""
        if self._tenants_table is None:
            self._tenants_table = boto3.Session(region_name=self.region).resource("dynamodb").Table(self.tenants_table_name)
        return self._tenants_table

    def _get_users_table(self):
        """Lazily create the DynamoDB Table resource for users on first use."""
        if self._users_table is None:
            self._users_table = boto3.Session(region_name=self.region).resource("dynamodb").Table(self.users_table_name)
        return self._users_table

    def encrypt_data(self, data: str) -> str:
        """Encrypt sensitive PII data."""
        if not self._cipher or not data:
            return data
        return self._cipher.encrypt(data.encode()).decode()

    def decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive PII data."""
        if not self._cipher or not encrypted_data:
            return encrypted_data
        try:
            return self._cipher.decrypt(encrypted_data.encode()).decode()
        except Exception:
            return encrypted_data

    def save_analytics(self, session_id: str, phone: str, hospital_id: str, analytics: dict, duration: int):
        """Insert processed metrics into DynamoDB."""
        try:
            encrypted_phone = self.encrypt_data(phone)
            timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

            item = {
                "session_id": session_id,
                "phone_number": encrypted_phone,
                "hospital_id": hospital_id or "unknown",
                "timestamp": timestamp,
                "sentiment": analytics.get("sentiment", "Neutral"),
                "intent": analytics.get("intent", "General"),
                "department": analytics.get("department", "General"),
                "outcome": analytics.get("outcome", "inquiry"),
                "duration_seconds": duration,
                "transcript_summary": analytics.get("summary", ""),
                "is_successful_booking": analytics.get("successful_booking", False),
                "urgency_score": analytics.get("urgency_score", 1),
                "is_emergency": analytics.get("is_emergency", False),
                "symptoms_list": analytics.get("symptoms_list", ""),
                "follow_up_priority": analytics.get("follow_up_priority", "Low")
            }
            
            self._get_table().put_item(Item=item)
        except Exception:
            logger.exception("Failed to insert analytics row into DynamoDB")

    def load_analytics(self, hospital_id: str, days: int = 30) -> list[dict]:
        """Query historical analytics records from DynamoDB with pagination support."""
        try:
            table = self._get_table()
            cutoff = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S IST")
            
            items = []
            last_evaluated_key = None
            try:
                # Attempt to query on global secondary index
                while True:
                    kwargs = {
                        "IndexName": "HospitalTimestampIndex",
                        "KeyConditionExpression": "hospital_id = :h_id AND #ts >= :cutoff",
                        "ExpressionAttributeNames": {"#ts": "timestamp"},
                        "ExpressionAttributeValues": {":h_id": hospital_id, ":cutoff": cutoff}
                    }
                    if last_evaluated_key:
                        kwargs["ExclusiveStartKey"] = last_evaluated_key
                    
                    response = table.query(**kwargs)
                    items.extend(response.get("Items", []))
                    last_evaluated_key = response.get("LastEvaluatedKey")
                    if not last_evaluated_key:
                        break
            except Exception as e:
                logger.warning(f"[DYNAMODB] HospitalTimestampIndex query failed, falling back to Scan: {e}")
                items = []
                last_evaluated_key = None
                while True:
                    kwargs = {
                        "FilterExpression": "hospital_id = :h_id AND #ts >= :cutoff",
                        "ExpressionAttributeNames": {"#ts": "timestamp"},
                        "ExpressionAttributeValues": {":h_id": hospital_id, ":cutoff": cutoff}
                    }
                    if last_evaluated_key:
                        kwargs["ExclusiveStartKey"] = last_evaluated_key
                    
                    response = table.scan(**kwargs)
                    items.extend(response.get("Items", []))
                    last_evaluated_key = response.get("LastEvaluatedKey")
                    if not last_evaluated_key:
                        break
            
            # Convert decimal type issues and decrypt phone number
            converted_items = []
            for item in items:
                conv = _convert_decimals(item)
                # Decrypt phone number
                if "phone_number" in conv:
                    conv["phone_number"] = self.decrypt_data(conv["phone_number"])
                converted_items.append(conv)
                
            return converted_items
        except Exception:
            logger.exception(f"Failed to load analytics from DynamoDB for hospital {hospital_id}")
            return []

    # --- Tenants Table CRUD ---
    def get_tenant(self, hospital_id: str) -> dict | None:
        """Fetch tenant config and normalized data from DynamoDB."""
        try:
            response = self._get_tenants_table().get_item(Key={"hospital_id": hospital_id})
            item = response.get("Item")
            return _convert_decimals(item) if item else None
        except Exception:
            logger.exception(f"Failed to fetch tenant {hospital_id} from DynamoDB")
            return None

    def save_tenant(self, tenant_data: dict) -> bool:
        """Insert or update a hospital tenant config in DynamoDB."""
        try:
            # Ensure no nested floats exist to avoid Boto3 exceptions
            clean_data = _convert_floats_to_decimals(tenant_data)
            self._get_tenants_table().put_item(Item=clean_data)
            return True
        except Exception:
            logger.exception(f"Failed to save tenant {tenant_data.get('hospital_id')} in DynamoDB")
            return False

    def list_tenants(self) -> list[dict]:
        """Scan and return all tenant configurations."""
        try:
            response = self._get_tenants_table().scan()
            items = response.get("Items", [])
            return [_convert_decimals(i) for i in items]
        except Exception:
            logger.exception("Failed to scan tenants from DynamoDB")
            return []

    # --- Users Table CRUD ---
    def get_user(self, username: str) -> dict | None:
        """Fetch user by username from DynamoDB."""
        try:
            response = self._get_users_table().get_item(Key={"username": username})
            item = response.get("Item")
            return _convert_decimals(item) if item else None
        except Exception:
            logger.exception(f"Failed to fetch user {username} from DynamoDB")
            return None

    def save_user(self, username: str, password_hash: str, hospital_id: str, role: str) -> bool:
        """Save a new user to DynamoDB."""
        try:
            item = {
                "username": username,
                "password_hash": password_hash,
                "hospital_id": hospital_id,
                "role": role,
                "created_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            }
            self._get_users_table().put_item(Item=item)
            return True
        except Exception:
            logger.exception(f"Failed to save user {username} in DynamoDB")
            return False

# Global instance
dynamodb_analytics = DynamoDBAnalyticsClient()
