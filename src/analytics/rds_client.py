import os
import logging
import boto3
import psycopg2
import sqlite3
import json
from datetime import datetime
import time
from cryptography.fernet import Fernet

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
        
        # PII Encryption Key (Loaded from environment)
        # To generate a key: Fernet.generate_key().decode()
        self.encryption_key = os.environ.get("ENCRYPTION_KEY")
        # [D-03] Guard against empty ENCRYPTION_KEY — Fernet raises ValueError on empty string
        if self.encryption_key:
            try:
                self._cipher = Fernet(self.encryption_key.encode())
            except Exception as e:
                logger.error(
                    "[SECURITY] Invalid ENCRYPTION_KEY: %s. PII encryption DISABLED. "
                    "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
                    e
                )
                self._cipher = None
        else:
            logger.warning(
                "[SECURITY] ENCRYPTION_KEY not set. PII encryption DISABLED. "
                "Set ENCRYPTION_KEY in .env for production."
            )
            self._cipher = None

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
        """Establish a connection to RDS using the IAM token or fallback to SQLite for Demo Mode."""
        # 1. Check for missing or placeholder hostnames -> Fallback to Local SQLite (Requirement: Showcase)
        if not self.host or "your_aws_rds_endpoint" in self.host.lower() or "mock" in self.host.lower():
            logger.info("📡 [DB] RDS not configured. Falling back to local SQLite (Demo Mode).")
            db_path = os.path.join(os.getcwd(), "indiiserve_demo.db")
            try:
                # sqlite3 uses the same PEP 249 interface as psycopg2
                return sqlite3.connect(db_path, check_same_thread=False)
            except Exception:
                logger.exception("Failed to connect to local SQLite")
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

    def is_sqlite(self, conn) -> bool:
        """Check if the connection is SQLite or Postgres."""
        return isinstance(conn, sqlite3.Connection)

    def format_query(self, query: str) -> str:
        """Requirement: Demo Resilience. Auto-translates %s (Postgres) to ? (SQLite) if needed."""
        if not query:
            return query
        
        # Check if we are currently in fallback/demo mode (SQLite)
        # Note: We check the host initially as get_connection might be called later
        is_demo = not self.host or "your_aws_rds_endpoint" in self.host.lower() or "mock" in self.host.lower()
        
        if is_demo:
            # Replace Postgres placeholders with SQLite placeholders
            return query.replace("%s", "?")
        return query

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
            # Fallback for old unencrypted data
            return encrypted_data

    def init_schema(self):
        """Create the historical analytics, tenants, and users tables for multi-tenancy."""
        conn = self.get_connection()
        if not conn:
            return
        
        is_sqlite = self.is_sqlite(conn)
        
        try:
            cur = conn.cursor()
            try:
                # 1. Hospital Analytics Table
                # Translation: Postgres SERIAL -> SQLite AUTOINCREMENT, JSONB -> TEXT
                serial_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "SERIAL PRIMARY KEY"
                json_type = "TEXT" if is_sqlite else "JSONB"
                
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS hospital_analytics (
                        id {serial_type},
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
                        is_successful_booking BOOLEAN DEFAULT FALSE,
                        urgency_score INT DEFAULT 1,
                        is_emergency BOOLEAN DEFAULT FALSE,
                        symptoms_list TEXT,
                        follow_up_priority VARCHAR(20) DEFAULT 'Low'
                    );
                """)
                
                # SQLite doesn't support ALTER TABLE ... ADD COLUMN IF NOT EXISTS in one go 
                # but we handle it gracefully via catching exceptions or pre-checks
                if not is_sqlite:
                    cur.execute("ALTER TABLE hospital_analytics ADD COLUMN IF NOT EXISTS urgency_score INT DEFAULT 1;")
                    cur.execute("ALTER TABLE hospital_analytics ADD COLUMN IF NOT EXISTS is_emergency BOOLEAN DEFAULT FALSE;")

                # 2. Tenants Table
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS tenants (
                        hospital_id VARCHAR(50) PRIMARY KEY,
                        hospital_name VARCHAR(100) NOT NULL,
                        status VARCHAR(20) DEFAULT 'pending', 
                        ingestion_strategy VARCHAR(20) DEFAULT 'hybrid', 
                        push_token VARCHAR(64),
                        sync_interval_mins INT DEFAULT 10,
                        last_sync_at TIMESTAMP,
                        hospital_data_normalized {json_type}, 
                        ingestion_config {json_type}, 
                        spreadsheet_id VARCHAR(100), 
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # 3. Users Table
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS users (
                        id {serial_type},
                        username VARCHAR(50) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        hospital_id VARCHAR(50),
                        role VARCHAR(20) DEFAULT 'staff',
                        is_admin BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                
                conn.commit()
            finally:
                cur.close()
            logger.info(f"[DB] Multi-tenant analytics schema initialized ({'SQLite' if is_sqlite else 'Postgres'}).")
        except Exception:
            logger.exception("Failed to initialize database schema")
        finally:
            conn.close()

# Global instance
rds_analytics = RDSAnalyticsClient()
