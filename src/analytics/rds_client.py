import os
import logging
import boto3
import psycopg2
from datetime import datetime

logger = logging.getLogger(__name__)

class RDSAnalyticsClient:
    """Requirement: AWS RDS (Postgres) Analytics Pipeline with IAM Authentication."""
    
    def __init__(self):
        self.host = os.environ.get("RDS_HOSTNAME")
        self.port = int(os.environ.get("RDS_PORT", 5432))
        self.user = os.environ.get("RDS_USERNAME")
        self.dbname = os.environ.get("RDS_DB_NAME", "indiiserve_analytics")
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self.rds_client = boto3.client("rds", region_name=self.region)

    def _get_auth_token(self):
        """Generate a short-lived IAM Auth Token for RDS access."""
        try:
            return self.rds_client.generate_db_auth_token(
                DBHostname=self.host,
                Port=self.port,
                DBUsername=self.user,
                Region=self.region
            )
        except Exception:
            logger.exception("Failed to generate RDS IAM Auth Token")
            return None

    def get_connection(self):
        """Establish a connection to RDS using the IAM token."""
        if not self.host:
            logger.warning("RDS_HOSTNAME not set. RDS integration disabled.")
            return None

        token = self._get_auth_token()
        if not token:
            return None

        try:
            return psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.dbname,
                user=self.user,
                password=token,
                sslmode='require'
            )
        except Exception:
            logger.exception("Failed to connect to RDS Postgres")
            return None

    def init_schema(self):
        """Create the historical analytics table if it doesn't exist."""
        conn = self.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS hospital_analytics (
                        id SERIAL PRIMARY KEY,
                        session_id VARCHAR(50) UNIQUE,
                        phone_number VARCHAR(20),
                        hospital_id VARCHAR(50),
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        sentiment VARCHAR(20),
                        intent VARCHAR(100),
                        department VARCHAR(50),
                        outcome VARCHAR(20), -- 'booked', 'inquiry', 'abandoned'
                        duration_seconds INT,
                        transcript_summary TEXT,
                        is_successful_booking BOOLEAN DEFAULT FALSE
                    );
                """)
                conn.commit()
            logger.info("[RDS] Analytics schema initialized.")
        except Exception:
            logger.exception("Failed to initialize RDS schema")
        finally:
            conn.close()

# Global instance
rds_analytics = RDSAnalyticsClient()
