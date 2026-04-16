import os
import logging
import boto3
import psycopg2
from datetime import datetime
import time

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
        self._cached_token = None
        self._token_expiry = 0

    def _get_auth_token(self):
        """Generate a short-lived IAM Auth Token for RDS access."""
        current_time = time.time()
        if self._cached_token and current_time < self._token_expiry:
            return self._cached_token

        try:
            token = self.rds_client.generate_db_auth_token(
                DBHostname=self.host,
                Port=self.port,
                DBUsername=self.user,
                Region=self.region
            )
            self._cached_token = token
            self._token_expiry = current_time + 720 # Cache for 12 minutes
            return token
        except Exception:
            logger.exception("Failed to generate RDS IAM Auth Token")
            return None

    def get_connection(self):
        """Establish a connection to RDS using the IAM token."""
        # 1. Check for missing or placeholder hostnames (Mock Mode)
        if not self.host or "your_aws_rds_endpoint" in self.host.lower() or "mock" in self.host.lower():
            logger.info("📡 [RDS] Running in Mock/Offline mode. Analytics will not be saved to Postgres.")
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
        """Create the historical analytics, tenants, and users tables for multi-tenancy."""
        conn = self.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cur:
                # 1. Hospital Analytics Table (Main Data)
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
                        outcome VARCHAR(20),
                        duration_seconds INT,
                        transcript_summary TEXT,
                        is_successful_booking BOOLEAN DEFAULT FALSE
                    );
                """)

                # 2. Tenants Table (Metadata per Clinic)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tenants (
                        hospital_id VARCHAR(50) PRIMARY KEY,
                        hospital_name VARCHAR(100) NOT NULL,
                        spreadsheet_id VARCHAR(100), -- Clinic specific Google Sheet
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # 3. Users Table (Dashboard Access)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(50) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        hospital_id VARCHAR(50) REFERENCES tenants(hospital_id),
                        is_admin BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                
                conn.commit()
            logger.info("[RDS] Multi-tenant analytics schema initialized.")
        except Exception:
            logger.exception("Failed to initialize RDS schema")
        finally:
            conn.close()

# Global instance
rds_analytics = RDSAnalyticsClient()
