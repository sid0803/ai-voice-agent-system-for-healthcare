"""
Clinical safety idle & silence monitor for voice agent sessions.

Tracks user inactivity during telephony and WebSocket calls, triggers soft follow-up
prompts, and escalates to emergency desk / disconnects if persistent silence is detected.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

SOFT_FOLLOW_UP_DEFAULT_SEC = 30.0
ESCALATION_DEFAULT_SEC = 50.0

EMERGENCY_ESCALATION_PROMPT = (
    "[The caller has been silent for too long during a clinical inquiry. "
    "They may be unable to speak. Say a reassuring message and connect them to the emergency desk immediately.]"
)

SOFT_FOLLOWUP_PROMPT = (
    "[The caller has been silent for a few seconds. "
    "Gently check if they are still there or if they need a moment.]"
)


class IdleMonitorSession:
    """Monitors silence during an active telephony/websocket voice call."""

    def __init__(
        self,
        call_sid: str,
        session_id: str,
        hospital_id: str,
        caller_phone: str,
        send_message_fn: Callable[[str], Awaitable[Any]],
        hangup_fn: Callable[[], Awaitable[Any]],
        audit_log_fn: Optional[Callable[[str, str, str, dict], Any]] = None,
        soft_follow_up_sec: float = SOFT_FOLLOW_UP_DEFAULT_SEC,
        escalation_sec: float = ESCALATION_DEFAULT_SEC,
        poll_interval_sec: float = 2.0,
        is_tool_in_progress_fn: Optional[Callable[[], bool]] = None,
        mask_phone_fn: Optional[Callable[[str], str]] = None,
    ):
        self.call_sid = call_sid
        self.session_id = session_id
        self.hospital_id = hospital_id
        self.caller_phone = caller_phone
        self.send_message_fn = send_message_fn
        self.hangup_fn = hangup_fn
        self.audit_log_fn = audit_log_fn
        self.soft_follow_up_sec = soft_follow_up_sec
        self.escalation_sec = escalation_sec
        self.poll_interval_sec = poll_interval_sec
        self.is_tool_in_progress_fn = is_tool_in_progress_fn or (lambda: False)
        self.mask_phone_fn = mask_phone_fn or (lambda p: p)

        self.last_activity_time = time.time()
        self.idle_prompt_sent = False
        self.escalation_triggered = False

    def record_activity(self) -> None:
        """Reset idle timer when caller or agent produces activity."""
        self.last_activity_time = time.time()
        self.idle_prompt_sent = False

    async def trigger_followup(self, is_escalation: bool = False) -> None:
        """Send soft prompt or trigger emergency escalation on silence."""
        if not self.call_sid:
            return

        if is_escalation:
            if self.escalation_triggered:
                return
            self.escalation_triggered = True
            logger.warning("[SAFETY] Silence escalation triggered for call %s", self.call_sid)
            if self.audit_log_fn:
                try:
                    self.audit_log_fn(
                        self.session_id,
                        "SILENCE_ESCALATION",
                        self.hospital_id,
                        {"caller": self.mask_phone_fn(self.caller_phone)},
                    )
                except Exception:
                    logger.debug("Failed to record silence escalation audit event", exc_info=True)
            await self.send_message_fn(EMERGENCY_ESCALATION_PROMPT)
            return

        if self.idle_prompt_sent:
            return

        self.idle_prompt_sent = True
        logger.info("Soft silence follow-up for call %s", self.call_sid)
        try:
            await self.send_message_fn(SOFT_FOLLOWUP_PROMPT)
        except Exception:
            logger.exception("Error sending soft idle follow-up for call %s", self.call_sid)

    async def run(self) -> None:
        """Background monitoring loop."""
        try:
            while True:
                await asyncio.sleep(self.poll_interval_sec)
                if self.is_tool_in_progress_fn():
                    self.last_activity_time = time.time()
                    continue

                elapsed = time.time() - self.last_activity_time

                if not self.idle_prompt_sent and elapsed >= self.soft_follow_up_sec:
                    await self.trigger_followup(is_escalation=False)
                elif elapsed >= self.escalation_sec:
                    await self.trigger_followup(is_escalation=True)
                    # Give model time to speak the safety message before ending the call
                    await asyncio.sleep(5)
                    await self.hangup_fn()
                    return
        except asyncio.CancelledError:
            pass
