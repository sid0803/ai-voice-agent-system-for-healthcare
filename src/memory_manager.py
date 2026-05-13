"""AgentCore Memory integration for voice conversations.

Single actor ID per phone: caller-<last 10 digits>.
One retrieve call per connect, one save call per turn. No legacy lookups.
"""

import asyncio
import logging
import os
import re
from datetime import datetime

import boto3

logger = logging.getLogger(__name__)


def _actor_id(phone: str) -> str:
    """caller-<last 10 digits> from any phone format."""
    digits = re.sub(r"[^0-9]", "", phone)
    if len(digits) >= 10:
        digits = digits[-10:]
    return f"caller-{digits}" if digits else "caller-unknown"


class AgentCoreMemoryManager:

    def __init__(self, memory_id: str, region: str = None):
        if region is None:
            region = os.getenv("MEMORY_REGION") or os.getenv("AWS_REGION") or "ap-south-1"
        self.memory_id = memory_id

        from src.nova_client import get_ec2_iam_role_credentials
        
        # AgentCore needs EC2 IAM role, not the .env Bedrock creds.
        # Fetch EC2 creds explicitly instead of mutating os.environ.
        ec2_creds = get_ec2_iam_role_credentials(timeout=2)
        
        try:
            if ec2_creds.get("aws_access_key_id"):
                session = boto3.Session(
                    region_name=region,
                    aws_access_key_id=ec2_creds.get("aws_access_key_id"),
                    aws_secret_access_key=ec2_creds.get("aws_secret_access_key"),
                    aws_session_token=ec2_creds.get("aws_session_token")
                )
                logger.info("[MEMORY] Using explicitly fetched EC2 IAM role credentials")
            else:
                session = boto3.Session(region_name=region)
                
            self.data_client = session.client("bedrock-agentcore")
            control = session.client("bedrock-agentcore-control")
        except Exception as e:
            logger.warning("[MEMORY] Failed to initialize Boto3 session explicitly: %s", e)
            raise

        self._sessions: dict[str, str] = {}  # session_id -> actor_id
        self._strategy_ids: dict[str, str] = {}

        try:
            mem = control.get_memory(memoryId=memory_id)
            for s in mem.get("memory", {}).get("strategies", []):
                self._strategy_ids[s["type"]] = s["strategyId"]
            logger.info("[MEMORY] Strategies: %s", self._strategy_ids)
        except Exception as e:
            logger.warning("[MEMORY] Could not fetch strategies: %s", e)

        logger.info("[MEMORY] Initialized with ID: %s (using IAM role)", memory_id[:40])

    def register_session(self, session_id: str, caller_phone: str) -> str:
        aid = _actor_id(caller_phone)
        self._sessions[session_id] = aid
        logger.info("[MEMORY] Registered session %s for %s (phone: %s)",
                     session_id[:8], aid, caller_phone)
        return aid

    async def retrieve_context(self, session_id: str) -> str:
        """Single retrieve call using caller-<last10> actor ID."""
        aid = self._sessions.get(session_id)
        if not aid:
            return ""

        context_parts = []
        for sid in self._strategy_ids.values():
            ns = f"/strategies/{sid}/actors/{aid}/"
            try:
                logger.info("[MEMORY] Querying: %s", ns)
                resp = self.data_client.retrieve_memory_records(
                    memoryId=self.memory_id,
                    namespace=ns,
                    searchCriteria={
                        "searchQuery": "patient name identity medical history symptoms last appointment",
                        "topK": 5,
                    },
                    maxResults=5,
                )
                for rec in resp.get("memoryRecordSummaries", []):
                    text = rec.get("content", {}).get("text", "")
                    if text:
                        context_parts.append(text)
                        logger.info("[MEMORY] Record: %s", text[:120])
            except Exception as e:
                logger.warning("[MEMORY] Retrieve failed: %s", e)

        if context_parts:
            logger.info("[MEMORY] Got %d records for %s", len(context_parts), aid)
            return "\n\n".join(context_parts)

        logger.info("[MEMORY] No context for %s (new caller)", aid)
        return ""

    async def save_interaction(self, session_id: str,
                               user_text: str, assistant_text: str) -> bool:
        aid = self._sessions.get(session_id)
        if not aid:
            return False
        try:
            # [D-10] create_event is a synchronous boto3 call — must use asyncio.to_thread
            # to avoid blocking the event loop and causing audio stuttering.
            await asyncio.to_thread(
                self.data_client.create_event,
                memoryId=self.memory_id,
                actorId=aid,
                sessionId=session_id,
                eventTimestamp=datetime.now(),
                payload=[
                    {"conversational": {"content": {"text": user_text}, "role": "USER"}},
                    {"conversational": {"content": {"text": assistant_text}, "role": "ASSISTANT"}},
                ],
            )
            logger.info("[MEMORY] Saved for %s", aid)
            return True
        except Exception as e:
            logger.warning("[MEMORY] Save failed for %s: %s", aid, e)
            return False

    def cleanup_session(self, session_id: str) -> None:
        aid = self._sessions.pop(session_id, None)
        if aid:
            logger.info("[MEMORY] Cleaned up session %s for %s", session_id[:8], aid)


def build_system_prompt_with_memory(base_prompt: str, memory_context: str = "") -> str:
    if not memory_context:
        return base_prompt
    return (
        f"{base_prompt}\n\n---\n\n"
        "## PREVIOUS CONVERSATION CONTEXT (CRITICAL - USE THIS)\n"
        "The following is context from previous calls with this same caller. "
        "You MUST use this information:\n"
        "- Greet the caller by name if their name is in the context below\n"
        "- Do NOT ask for information you already have from this context\n"
        "- If the caller asks if you remember them, confirm what you know\n\n"
        f"{memory_context}\n"
    )
