"""DynamoDB transcript storage for voice calls."""

import logging
import os
from datetime import datetime, timezone, timedelta

import boto3

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_table_name = os.environ.get("DYNAMODB_TABLE_NAME", "InDiiServe_Call_Transcript_1")
_region = os.environ.get("AWS_REGION") or "ap-south-1"
_table = boto3.Session(region_name=_region).resource("dynamodb").Table(_table_name)


def save_transcript(phone_number: str, session_id: str,
                    transcripts: list[dict], call_start_time: datetime = None):
    """Save call transcript to DynamoDB with call timing info."""
    try:
        end_time = datetime.now(IST)

        if call_start_time:
            start_ist = call_start_time.astimezone(IST)
            duration_secs = int((end_time - start_ist).total_seconds())
        else:
            start_ist = end_time
            duration_secs = 0

        mins, secs = divmod(duration_secs, 60)

        _table.put_item(
            Item={
                "phone_number": phone_number or "unknown",
                "timestamp": start_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "session_id": session_id,
                "start_time": start_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S IST"),
                "duration": f"{mins}m {secs}s",
                "duration_seconds": duration_secs,
                "transcript": transcripts,
            }
        )
        logger.info(
            "Transcript saved for phone=%s session=%s duration=%dm%ds (%d messages)",
            phone_number or "unknown",
            session_id[:8],
            mins, secs,
            len(transcripts),
        )
    except Exception:
        logger.exception("Failed to save transcript to DynamoDB")
