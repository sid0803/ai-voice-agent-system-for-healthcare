"""
Unit tests for src/guards.py (Phase 4.2).

Validates:
1. Spoken Fact Gate fee correction (correcting hallucinated fee to authoritative fee).
2. Spoken Fact Gate reference ID correction (replaces hallucinated ID with authoritative ref_id).
3. Availability gate (blocks positive availability claims when backend returned UNAVAILABLE_EXACT).
4. Speech text sanitization (strips line breaks, list numbers, stray braces).
5. Known consultation fees loader and backward compatibility re-exports.
"""

from unittest.mock import MagicMock
import pytest
from src.guards import (
    _KNOWN_CONSULTATION_FEES,
    _load_known_fees,
    sanitize_spoken_text,
    apply_spoken_fact_gate,
)


def test_known_consultation_fees_populated():
    """_KNOWN_CONSULTATION_FEES must contain standard OPD fees."""
    assert isinstance(_KNOWN_CONSULTATION_FEES, set)
    assert len(_KNOWN_CONSULTATION_FEES) >= 5
    for fee in [800, 1000, 1200]:
        assert fee in _KNOWN_CONSULTATION_FEES


def test_sanitize_spoken_text():
    """sanitize_spoken_text cleans markdown lists, line breaks, and bracket artifacts."""
    raw = "1. Your appointment is confirmed.\n2. Please reach [Gate 3] at {10:00 AM}."
    sanitized = sanitize_spoken_text(raw)
    assert "\n" not in sanitized
    assert "1. " not in sanitized
    assert "2. " not in sanitized
    assert "[" not in sanitized
    assert "]" not in sanitized
    assert "{" not in sanitized
    assert "}" not in sanitized
    assert "Gate 3" in sanitized
    assert "10:00 AM" in sanitized


def test_spoken_fact_gate_fee_correction():
    """Spoken Fact Gate must correct a hallucinated fee to the authoritative fee."""
    mock_session = MagicMock()
    mock_session.state = MagicMock()
    mock_session.state.last_authoritative_facts = {
        "fee": {"authoritative": True, "value": 1000}
    }

    # Model hallucinates fee of 1500
    hallucinated_text = "The consultation fee for Dr. Sharma is 1500 rupees."
    guarded_text = apply_spoken_fact_gate(hallucinated_text, mock_session, role="ASSISTANT")

    assert "1000" in guarded_text
    assert "1500" not in guarded_text


def test_spoken_fact_gate_ref_id_correction():
    """Spoken Fact Gate must correct a mismatched appointment reference ID."""
    mock_session = MagicMock()
    mock_session.state = MagicMock()
    mock_session.state.last_authoritative_facts = {}
    mock_session.state.current_appointment = {
        "ref_id": "IS-APP-260916-9999"
    }

    hallucinated_text = "Your booking reference is IS-APP-260916-1111."
    guarded_text = apply_spoken_fact_gate(hallucinated_text, mock_session, role="ASSISTANT")

    assert "IS-APP-260916-9999" in guarded_text
    assert "IS-APP-260916-1111" not in guarded_text


def test_spoken_fact_gate_unavailable_slot_enforcement():
    """Spoken Fact Gate must block false availability claims when slot is UNAVAILABLE_EXACT."""
    mock_session = MagicMock()
    mock_session.state = MagicMock()
    mock_session.state.last_authoritative_facts = {
        "status": {"value": "UNAVAILABLE_EXACT"},
        "requested_time": {"value": "11:00 AM"},
        "department": {"value": "cardiology"},
    }

    hallucinated_text = "Dr. Sharma is available at 11:00 AM tomorrow."
    guarded_text = apply_spoken_fact_gate(hallucinated_text, mock_session, role="ASSISTANT")

    assert "isn't available" in guarded_text or "not available" in guarded_text
    assert "is available" not in guarded_text


def test_spoken_fact_gate_user_role_noop():
    """Spoken Fact Gate should only inspect ASSISTANT utterances, ignoring USER utterances."""
    mock_session = MagicMock()
    mock_session.state = MagicMock()
    mock_session.state.last_authoritative_facts = {
        "fee": {"authoritative": True, "value": 1000}
    }

    user_text = "I heard the fee was 1500 rupees?"
    result = apply_spoken_fact_gate(user_text, mock_session, role="USER")
    assert result == user_text


def test_server_reexports_guards():
    """src.server must re-export guard symbols for backward compatibility."""
    import src.server as s
    assert hasattr(s, "_KNOWN_CONSULTATION_FEES")
    assert hasattr(s, "sanitize_spoken_text")
    assert hasattr(s, "apply_spoken_fact_gate")
