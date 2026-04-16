import hashlib
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

load_dotenv()

from src.analytics.rds_client import rds_analytics

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def seed_data():
    print("Initializing RDS Schema...")
    rds_analytics.init_schema()
    
    conn = rds_analytics.get_connection()
    if not conn:
        print("Failed to connect to RDS for seeding.")
        return

    try:
        with conn.cursor() as cur:
            # 1. Seed Tenants
            print("Seeding tenants...")
            tenants = [
                ("default_tier2", "Tier-2 Clinic", "REPLACE_WITH_TIER2_SHEET_ID"),
                ("premium_metro", "Metro Premium Hospital", "REPLACE_WITH_METRO_SHEET_ID")
            ]
            for tid, name, sid in tenants:
                cur.execute("""
                    INSERT INTO tenants (hospital_id, hospital_name, spreadsheet_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (hospital_id) DO UPDATE SET 
                        hospital_name = EXCLUDED.hospital_name,
                        spreadsheet_id = EXCLUDED.spreadsheet_id;
                """, (tid, name, sid))

            # 2. Seed Users
            print("Seeding users...")
            users = [
                ("admin_tier2", "tier2pass", "default_tier2"),
                ("admin_metro", "metropass", "premium_metro")
            ]
            for user, pw, tid in users:
                cur.execute("""
                    INSERT INTO users (username, password_hash, hospital_id, is_admin)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (username) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        hospital_id = EXCLUDED.hospital_id;
                """, (user, hash_password(pw), tid, True))

            conn.commit()
            print("Seeding complete! Logins ready:")
            print(" - admin_tier2 / tier2pass")
            print(" - admin_metro / metropass")
    except Exception as e:
        print(f"Error during seeding: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    seed_data()
