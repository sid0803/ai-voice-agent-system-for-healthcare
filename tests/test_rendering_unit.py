"""
Unit tests for src/rendering.py (Phase 4.4).

Validates:
1. Phonetic reference ID expansion for TTS (e.g. spelling out digits and capital letters with dashes).
2. Currency rendering with 'rupees' unit suffix.
3. 24-hour time conversion into spoken conversational AM/PM formats.
4. Backward compatibility re-exports from src.tools.
"""

import pytest
from src.rendering import (
    render_reference_id,
    render_currency,
    render_time,
)


def test_render_reference_id():
    """Reference IDs must be spelled out phonetically with digit words and character spacing."""
    ref = "IS-APP-120423-BCA6"
    rendered = render_reference_id(ref)
    assert "I S dash A P P" in rendered
    assert "one two zero four two three" in rendered
    assert "B C A six" in rendered

    # Empty string should handle safely
    assert render_reference_id("") == ""


def test_render_currency():
    """render_currency formats integers, floats, and numeric strings with 'rupees'."""
    assert render_currency(900) == "900 rupees"
    assert render_currency(1200) == "1200 rupees"
    assert render_currency("1500") == "1500 rupees"
    assert render_currency(None) == ""


def test_render_time():
    """render_time converts 24h military timestamps to conversational AM/PM expressions."""
    assert render_time("09:00") == "9 AM"
    assert render_time("09:30") == "9:30 AM"
    assert render_time("12:00") == "12 PM"
    assert render_time("13:00") == "1 PM"
    assert render_time("17:15") == "5:15 PM"
    assert render_time("00:00") == "12 AM"

    # Non-timestamp strings should pass through safely
    assert render_time("morning") == "morning"
    assert render_time("") == ""


def test_tools_reexports_rendering_functions():
    """src.tools must re-export rendering functions for backward compatibility."""
    import src.tools as t
    assert hasattr(t, "render_reference_id")
    assert hasattr(t, "render_currency")
    assert hasattr(t, "render_time")
    assert t.render_currency(500) == "500 rupees"
