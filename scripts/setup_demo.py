import json
import logging
import random
import os
import bcrypt
from datetime import datetime, timedelta
from src.analytics.rds_client import rds_analytics

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("setup_demo")

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()

def setup_demo():
    """Requirement: Bulletproof Demo. Sets up local SQLite with Apollo Metro data."""
    logger.info("🚀 [DEMO SETUP] Initializing Bulletproof Demo Environment...")
    
    # 1. Initialize Schema
    # This will create indiiserve_demo.db and all tables
    rds_analytics.init_schema()
    
    conn = rds_analytics.get_connection()
    if not conn:
        logger.error("❌ [DEMO SETUP] Failed to connect to database.")
        return

    try:
        cur = conn.cursor()
        try:
            # 2. Load the AI-Ready Apollo Metro JSON
            json_path = os.path.join("data", "hospital_data", "apollo_metro.json")
            with open(json_path, "r", encoding="utf-8") as f:
                apollo_data = json.load(f)
            
            hospital_id = apollo_data["id"]
            hospital_name = apollo_data["name"]
            
            # 3. Clear existing demo data
            logger.info(f"🧹 [DEMO SETUP] Cleaning old records for '{hospital_id}'...")
            cur.execute("DELETE FROM users WHERE hospital_id = ?", (hospital_id,))
            cur.execute("DELETE FROM hospital_analytics WHERE hospital_id = ?", (hospital_id,))
            cur.execute("DELETE FROM tenants WHERE hospital_id = ?", (hospital_id,))
            
            # 4. Insert Tenant (Live Status)
            logger.info("🏥 [DEMO SETUP] Injecting Apollo Metro Tenant...")
            cur.execute("""
                INSERT INTO tenants (
                    hospital_id, hospital_name, status, ingestion_strategy, 
                    spreadsheet_id, created_at, hospital_data_normalized
                )
                VALUES (?, ?, 'live', 'hybrid', 'APOLLO_METRO_LIVE_SINK', ?, ?)
            """, (
                hospital_id, hospital_name, datetime.now().isoformat(), 
                json.dumps(apollo_data)
            ))
            
            # 5. Insert Admin User (admin_metro / metro123)
            logger.info("👤 [DEMO SETUP] Creating Admin: admin_metro / metro123")
            hashed_pass = hash_password("metro123")
            cur.execute("""
                INSERT INTO users (username, password_hash, hospital_id, role)
                VALUES (?, ?, ?, 'admin')
            """, ("admin_metro", hashed_pass, hospital_id))
            
            # 6. Generate 25 Dynamic Mock Calls for the Dashboard
            logger.info("📊 [DEMO SETUP] Generating 25 mock analytics entries...")
            
            sentiments = ["Positive", "Positive", "Positive", "Neutral", "Mixed", "Positive"]
            intents = [
                "Cardiology Appointment", "Diabetes Query", "Lab Report Status", 
                "Neurology Consultation", "General Inquiry", "Symptom Check",
                "Pricing/Fees", "Pharmacy Hours", "Emergency Transfer"
            ]
            outcomes = [
                "Appointment Booked", "Information Provided", "Patient Reassured", 
                "Transferred to Staff", "Appointment Booked", "Outcome Pending"
            ]
            departments = ["Cardiology", "Neurology", "Diabetes Clinic", "Physiotherapy", "Emergency"]
            
            for i in range(25):
                # Spread calls over the last 10 days
                timestamp = datetime.now() - timedelta(days=random.randint(0, 10), hours=random.randint(0, 23), minutes=random.randint(0, 59))
                duration = random.randint(30, 210)
                sentiment = random.choice(sentiments)
                intent = random.choice(intents)
                outcome = random.choice(outcomes)
                dept = random.choice(departments)
                is_booking = 1 if "Booked" in outcome else 0
                is_emergency = 1 if "Emergency" in intent else 0
                urgency = random.randint(3, 5) if is_emergency else random.randint(1, 2)
                
                # Mock phone (encrypted)
                phone = f"+9198{random.randint(10000000, 99999999)}"
                encrypted_phone = rds_analytics.encrypt_data(phone)
                
                cur.execute("""
                    INSERT INTO hospital_analytics (
                        session_id, phone_number, hospital_id, timestamp, 
                        duration_seconds, sentiment, intent, department, 
                        outcome, is_successful_booking, is_emergency, urgency_score, transcript_summary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"demo-sess-{i:03d}", encrypted_phone, hospital_id, timestamp.isoformat(),
                    duration, sentiment, intent, dept, outcome, is_booking, is_emergency, urgency,
                    f"Demo transcript for {intent}. Outcome: {outcome}."
                ))
                
            conn.commit()
        finally:
            cur.close()
            
        logger.info("✅ [DEMO SETUP] Setup Successful!")
        logger.info("--------------------------------------------------")
        logger.info(f"Hospital: {apollo_data['name']}")
        logger.info("Dashboard URL: Run 'streamlit run src/dashboard/app.py'")
        logger.info("Credentials:  admin_metro / metro123")
        logger.info("--------------------------------------------------")
    except Exception as e:
        logger.error(f"❌ [DEMO SETUP] Setup failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    setup_demo()
