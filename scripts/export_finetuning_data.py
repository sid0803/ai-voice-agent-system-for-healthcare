"""
Export multi-turn conversation data for Bedrock model fine-tuning.

Pulls SUCCESSFUL BOOKING sessions from RDS analytics, then fetches the full
conversation turns from DynamoDB, and exports in the Nova/Claude conversation
JSONL format required for Bedrock Custom Model fine-tuning.

Usage:
    python scripts/export_finetuning_data.py [output_path]

Output format (one JSON object per line):
    {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
"""
import json
import os
import sys
import logging
import pathlib

# Add project root to sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import boto3
from src.analytics.rds_client import rds_analytics

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# DynamoDB table for full transcripts
_dynamo_table_name = os.environ.get("DYNAMODB_TABLE_NAME", "InDiiServe_Call_Transcript_1")
_region = os.environ.get("AWS_REGION", "ap-south-1")
_dynamo = boto3.resource("dynamodb", region_name=_region).Table(_dynamo_table_name)


def fetch_successful_sessions(limit: int = 5000) -> list[dict]:
    """Pull session IDs of successful bookings from RDS analytics."""
    conn = rds_analytics.get_connection()
    if not conn:
        logger.error("Cannot connect to RDS. Set RDS_HOSTNAME and credentials.")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, hospital_id, intent, department
                FROM hospital_analytics
                WHERE is_successful_booking = TRUE
                ORDER BY timestamp DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
        conn.close()
        logger.info("Found %d successful booking sessions in RDS.", len(rows))
        return [
            {"session_id": r[0], "hospital_id": r[1], "intent": r[2], "department": r[3]}
            for r in rows
        ]
    except Exception:
        logger.exception("RDS query failed")
        conn.close()
        return []


def fetch_transcript_from_dynamo(session_id: str) -> list[dict]:
    """Fetch full conversation turns from DynamoDB by session_id."""
    try:
        result = _dynamo.get_item(Key={"session_id": session_id})
        item = result.get("Item")
        if not item:
            return []
        return item.get("transcript", [])
    except Exception:
        logger.warning("DynamoDB fetch failed for session %s", session_id[:8])
        return []


def build_conversation_record(turns: list[dict]) -> dict | None:
    """
    Convert raw transcript turns into the Bedrock fine-tuning JSONL format.

    Expected turn format: {"role": "user"|"assistant", "content": "<text>"}
    Output format:
        {"messages": [{"role": "user", "content": "..."}, ...]}
    """
    messages = []
    for turn in turns:
        role = turn.get("role", "").lower()
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # Need at least one full exchange
    if len(messages) >= 2:
        return {"messages": messages}
    return None


def export_finetuning_data(output_path: str = "data/finetuning/nova_finetune_v1.jsonl"):
    """Main export function: RDS → DynamoDB → JSONL conversation records."""
    output_file = pathlib.Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    sessions = fetch_successful_sessions(limit=5000)
    if not sessions:
        logger.error("No sessions to export. Exiting.")
        return

    exported = 0
    skipped = 0

    with open(output_file, "w", encoding="utf-8") as f:
        for meta in sessions:
            session_id = meta["session_id"]
            turns = fetch_transcript_from_dynamo(session_id)

            if not turns:
                skipped += 1
                continue

            record = build_conversation_record(turns)
            if record:
                # Add system metadata as first message for context
                record["messages"].insert(0, {
                    "role": "system",
                    "content": (
                        f"You are Asha, a professional hospital receptionist at {meta.get('hospital_id', 'InDiiServe Healthcare')}. "
                        f"The caller's intent was: {meta.get('intent', 'General')}. "
                        f"Department: {meta.get('department', 'General')}."
                    )
                })
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                exported += 1
            else:
                skipped += 1

    logger.info(
        "Export complete: %d records written to %s (%d skipped - no transcript in DynamoDB).",
        exported,
        output_file,
        skipped,
    )


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "data/finetuning/nova_finetune_v1.jsonl"
    export_finetuning_data(out)
