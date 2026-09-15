"""Idle monitor / silence escalation unit tests — AI-23.

Tests the silence detection, follow-up prompt, escalation, and hangup
logic in isolation by mocking asyncio.sleep and the Exotel send methods.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers: mock session and send functions
# ---------------------------------------------------------------------------

def _make_mock_session(session_id: str = "test-idle-session"):
    session = MagicMock()
    session.session_id = session_id
    session.state = MagicMock()
    session.state.caller_phone = "919999999999"
    return session


def _make_send_fn():
    """Returns a recording async function used to verify messages were sent."""
    sent: list[str] = []

    async def _send(data: str):
        sent.append(data)

    return _send, sent


# ---------------------------------------------------------------------------
# Import idle constants from server
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _import_constants():
    """Verify the idle timeout constants are loaded from server.py."""
    from src.server import IDLE_TIMEOUT_SECONDS, HANGUP_GRACE_SECONDS
    assert IDLE_TIMEOUT_SECONDS > 0, "IDLE_TIMEOUT_SECONDS must be > 0"
    assert HANGUP_GRACE_SECONDS > 0, "HANGUP_GRACE_SECONDS must be > 0"


# ---------------------------------------------------------------------------
# Test 1: Idle timer constants are sane
# ---------------------------------------------------------------------------
def test_idle_01_timeout_constants_are_sane():
    """IDLE_TIMEOUT_SECONDS and HANGUP_GRACE_SECONDS must be positive integers."""
    from src.server import IDLE_TIMEOUT_SECONDS, HANGUP_GRACE_SECONDS
    assert isinstance(IDLE_TIMEOUT_SECONDS, int)
    assert isinstance(HANGUP_GRACE_SECONDS, int)
    assert 5 <= IDLE_TIMEOUT_SECONDS <= 120, "Idle timeout should be between 5s and 2min"
    assert 5 <= HANGUP_GRACE_SECONDS <= 60, "Grace period should be between 5s and 1min"


# ---------------------------------------------------------------------------
# Test 2: Follow-up liveness check strings are defined
# ---------------------------------------------------------------------------
def test_idle_02_liveness_phrases_defined():
    """_ALL_LIVENESS_PHRASES must be a non-empty list of strings."""
    from src.server import _ALL_LIVENESS_PHRASES
    assert isinstance(_ALL_LIVENESS_PHRASES, list)
    assert len(_ALL_LIVENESS_PHRASES) >= 5, "At least 5 liveness phrases should be defined"
    for phrase in _ALL_LIVENESS_PHRASES:
        assert isinstance(phrase, str)
        assert len(phrase) >= 2  # "hi" and other 2-char phrases are valid


# ---------------------------------------------------------------------------
# Test 3: Liveness check detection — true positives
# ---------------------------------------------------------------------------
def test_idle_03_liveness_check_true_positives():
    """Common liveness phrases must trigger _is_liveness_check."""
    from src.server import _is_liveness_check
    true_positives = [
        "hello", "hello?", "are you there?", "asha?", "hello asha",
        "hello are you there",
    ]
    for phrase in true_positives:
        assert _is_liveness_check(phrase), f"Expected liveness=True for: {phrase!r}"


# ---------------------------------------------------------------------------
# Test 4: Liveness check detection — false positives (normal sentences)
# ---------------------------------------------------------------------------
def test_idle_04_liveness_check_false_positives():
    """Normal appointment queries must NOT trigger _is_liveness_check."""
    from src.server import _is_liveness_check
    false_positives = [
        "I want to book an appointment",
        "What are OPD hours?",
        "Can I speak to Dr. Sharma?",
        "ami appointment nite chai",
        "doctor ki fee kitni hai",
    ]
    for phrase in false_positives:
        assert not _is_liveness_check(phrase), f"Expected liveness=False for: {phrase!r}"


# ---------------------------------------------------------------------------
# Test 5: Idle timeout constant matches expected default
# ---------------------------------------------------------------------------
def test_idle_05_default_timeout_is_25_seconds():
    """Default idle timeout should be 25 seconds (configurable via env)."""
    from src.server import IDLE_TIMEOUT_SECONDS
    # Only assert the default if env var is not overridden in CI
    import os
    if "IDLE_TIMEOUT_SECONDS" not in os.environ:
        assert IDLE_TIMEOUT_SECONDS == 25


# ---------------------------------------------------------------------------
# Test 6: Hangup grace default is 15 seconds
# ---------------------------------------------------------------------------
def test_idle_06_default_hangup_grace_is_15_seconds():
    """Default hangup grace should be 15 seconds."""
    from src.server import HANGUP_GRACE_SECONDS
    import os
    if "HANGUP_GRACE_SECONDS" not in os.environ:
        assert HANGUP_GRACE_SECONDS == 15


# ---------------------------------------------------------------------------
# Test 7: IdleMonitorSession records activity and resets flags
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idle_07_session_record_activity_resets():
    """record_activity must update last_activity_time and reset idle_prompt_sent."""
    import time
    from src.idle_monitor import IdleMonitorSession

    sent_messages = []
    async def fake_send(msg): sent_messages.append(msg)
    async def fake_hangup(): pass

    session = IdleMonitorSession(
        call_sid="test_call_sid",
        session_id="test_sess",
        hospital_id="test_hosp",
        caller_phone="9999999999",
        send_message_fn=fake_send,
        hangup_fn=fake_hangup,
    )
    session.idle_prompt_sent = True
    session.last_activity_time = time.time() - 100

    session.record_activity()
    assert not session.idle_prompt_sent
    assert time.time() - session.last_activity_time < 2.0


# ---------------------------------------------------------------------------
# Test 8: IdleMonitorSession triggers soft follow-up
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idle_08_session_soft_followup():
    """trigger_followup(False) sends reassuring soft prompt and prevents duplicates."""
    from src.idle_monitor import IdleMonitorSession

    sent_messages = []
    async def fake_send(msg): sent_messages.append(msg)
    async def fake_hangup(): pass

    session = IdleMonitorSession(
        call_sid="test_call_sid",
        session_id="test_sess",
        hospital_id="test_hosp",
        caller_phone="9999999999",
        send_message_fn=fake_send,
        hangup_fn=fake_hangup,
    )

    await session.trigger_followup(is_escalation=False)
    assert len(sent_messages) == 1
    assert "silent for a few seconds" in sent_messages[0]
    assert session.idle_prompt_sent

    # Duplicate call should be ignored
    await session.trigger_followup(is_escalation=False)
    assert len(sent_messages) == 1


# ---------------------------------------------------------------------------
# Test 9: IdleMonitorSession triggers escalation and audit event
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idle_09_session_escalation_triggers_audit():
    """trigger_followup(True) sends emergency escalation prompt and records audit log."""
    from src.idle_monitor import IdleMonitorSession

    sent_messages = []
    audit_events = []
    async def fake_send(msg): sent_messages.append(msg)
    async def fake_hangup(): pass
    def fake_audit(sess_id, event, hosp_id, meta):
        audit_events.append((sess_id, event, hosp_id, meta))

    session = IdleMonitorSession(
        call_sid="test_call_sid",
        session_id="test_sess_42",
        hospital_id="asha_hospital",
        caller_phone="9876543210",
        send_message_fn=fake_send,
        hangup_fn=fake_hangup,
        audit_log_fn=fake_audit,
        mask_phone_fn=lambda p: f"{p[:3]}***{p[-2:]}",
    )

    await session.trigger_followup(is_escalation=True)
    assert len(sent_messages) == 1
    assert "emergency desk" in sent_messages[0]
    assert session.escalation_triggered
    assert len(audit_events) == 1
    assert audit_events[0][1] == "SILENCE_ESCALATION"
    assert audit_events[0][3]["caller"] == "987***10"

    # Subsequent escalation calls are idempotent
    await session.trigger_followup(is_escalation=True)
    assert len(sent_messages) == 1
    assert len(audit_events) == 1


# ---------------------------------------------------------------------------
# Test 10: Empty call_sid results in no-op
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idle_10_empty_call_sid_noop():
    """If call_sid is empty, no follow-up should be triggered."""
    from src.idle_monitor import IdleMonitorSession

    sent_messages = []
    async def fake_send(msg): sent_messages.append(msg)
    async def fake_hangup(): pass

    session = IdleMonitorSession(
        call_sid="",
        session_id="test_sess",
        hospital_id="test_hosp",
        caller_phone="9999999999",
        send_message_fn=fake_send,
        hangup_fn=fake_hangup,
    )

    await session.trigger_followup(is_escalation=False)
    await session.trigger_followup(is_escalation=True)
    assert len(sent_messages) == 0



