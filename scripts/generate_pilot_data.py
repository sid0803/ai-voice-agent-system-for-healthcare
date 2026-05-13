import sqlite3
import random
import uuid
import os
from datetime import datetime, timedelta

# Configuration
DB_PATH = os.path.join(os.getcwd(), "indiiserve_demo.db")
HOSPITAL_ID = "apollo_metro"

# Data Sets
SURNAMES = ["Sharma", "Verma", "Gupta", "Sen", "Reddy", "Patel", "Nair", "Das", "Khan", "Iyer"]
INTENTS = ["Appointment", "Billing", "Emergency", "General Inquiry", "Follow-up"]
SENTIMENTS = ["Positive", "Neutral", "Negative", "Anxious", "Urgent"]
OUTCOMES = ["Booked", "Resolved", "Handoff", "Abandoned"]

MULTILINGUAL_SAMPLES = [
    {"text": "Asha, Dr. Sen se milna hai kal subah 10 baje.", "intent": "Appointment", "lang": "Hinglish"},
    {"text": "I have a sharp pain in my chest, please help!", "intent": "Emergency", "lang": "English"},
    {"text": "MRI scan ka cost kitna hoga? Hospital main machines hain?", "intent": "Billing", "lang": "Hinglish"},
    {"text": "Mujhe bahut ghabrahat ho rahi hai, doctor se baat karao.", "intent": "Emergency", "lang": "Hinglish"},
    {"text": "Checking for doctor availability in the evening shift.", "intent": "General Inquiry", "lang": "English"},
    {"text": "Report kab tak milegi? Blood test kal hua tha.", "intent": "Follow-up", "lang": "Hinglish"},
    {"text": "I want to schedule a routine checkup for my father.", "intent": "Appointment", "lang": "English"},
    {"text": "Billing counter par bahut bheed hai, discount milega?", "intent": "Billing", "lang": "Hinglish"},
    {"text": "Mera blood pressure shoot kar gaya hai, kya karu?", "intent": "Emergency", "lang": "Hinglish"},
    {"text": "Is Dr. Gupta available for a video consultation?", "intent": "General Inquiry", "lang": "English"}
]

def generate_pilot():
    print(f"Generating 100 realistic pilot records for {HOSPITAL_ID}...")
    
    if os.path.exists(DB_PATH):
        # We don't wipe everything, just analytics for this hospital to keep the demo clean
        pass

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Ensure tables exist (Mirroring rds_client.py)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hospital_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    # Clear existing data for a fresh demo
    cur.execute("DELETE FROM hospital_analytics WHERE hospital_id = ?", (HOSPITAL_ID,))

    now = datetime.now()
    records = []

    for i in range(100):
        # Time distribution (mostly in last 7 days)
        if i < 80:
            days_ago = random.randint(0, 6)
        else:
            days_ago = random.randint(7, 30)
        
        timestamp = (now - timedelta(days=days_ago, hours=random.randint(0, 23))).strftime('%Y-%m-%d %H:%M:%S')
        
        sample = random.choice(MULTILINGUAL_SAMPLES)
        intent = sample["intent"]
        
        # Realistic outcome distribution
        outcome = random.choice(OUTCOMES)
        is_booking = (outcome == "Booked" or random.random() < 0.1)
        
        if intent == "Emergency":
            urgency_score = random.randint(4, 5)
            is_emergency = True
            outcome = "Handoff"
            sentiment = "Urgent"
        else:
            urgency_score = random.randint(1, 3)
            is_emergency = False
            sentiment = random.choice(["Positive", "Neutral", "Anxious"])

        # Qualify logic matching our funnel:
        # Inquiry = intent present
        # Qualified = urgency > 2 OR intent in ['Appointment', 'Emergency']
        
        duration = random.randint(15, 300) if i < 90 else random.randint(2, 8) # Most are engaged
        
        phone = f"+91 {random.randint(70000, 99999)} {random.randint(10000, 99999)}"
        session_id = f"demo_{uuid.uuid4().hex[:8]}"
        
        records.append((
            session_id, phone, HOSPITAL_ID, timestamp, sentiment, intent, 
            random.choice(["General", "Cardio", "Pediatrics", "Billing"]),
            outcome, duration, sample["text"], is_booking, urgency_score, 
            is_emergency, "Symptoms detected" if is_emergency else None,
            "High" if urgency_score > 3 else "Low"
        ))

    cur.executemany("""
        INSERT INTO hospital_analytics (
            session_id, phone_number, hospital_id, timestamp, sentiment, intent, department, 
            outcome, duration_seconds, transcript_summary, is_successful_booking, 
            urgency_score, is_emergency, symptoms_list, follow_up_priority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)

    conn.commit()
    conn.close()
    print(f"Successfully injected 100 records into {DB_PATH}")

if __name__ == "__main__":
    generate_pilot()
