import json
import os
import logging
from pathlib import Path
from src.analytics.rds_client import rds_analytics

logger = logging.getLogger(__name__)

def export_bedrock_finetuning_data(output_path: str = "data/finetuning/nova_finetune_v1.jsonl"):
    """
    Requirement: Hybrid Fine-Tuning Data Exporter.
    Prioritizes 'Successful Bookings' but includes general flow for natural conversation.
    """
    conn = rds_analytics.get_connection()
    if not conn:
        print("Error: Could not connect to RDS Analytics for data export.")
        return

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with conn.cursor() as cur:
            # Query for successful bookings first (priority), then some general calls
            # We join with the transcript table or retrieve from the analytics record
            cur.execute("""
                SELECT session_id, transcript_summary, is_successful_booking 
                FROM hospital_analytics 
                ORDER BY is_successful_booking DESC, timestamp DESC
                LIMIT 5000;
            """)
            rows = cur.fetchall()

        with open(output_file, 'w', encoding='utf-8') as f:
            for session_id, summary, is_booked in rows:
                # In a real scenario, we would fetch the full conversation turns from DynamoDB
                # For this exporter, we create a high-quality summary record
                bedrock_record = {
                    "prompt": f"Patient session {session_id}. History: {summary}",
                    "completion": f"Outcome: {'SUCCESSFUL_BOOKING' if is_booked else 'INQUIRY_HANDLED'}. Persona: Asha."
                }
                f.write(json.dumps(bedrock_record) + "\n")
        
        print(f"Successfully exported {len(rows)} records for Bedrock Fine-Tuning to {output_path}")

    except Exception as e:
        print(f"Export failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    export_bedrock_finetuning_data()
