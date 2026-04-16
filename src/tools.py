"""src/tools.py — Production tool implementations for the Asha voice assistant.

This is the canonical module. src/mock_tools.py is kept as a backward-compat
redirect to this file.
"""
# Re-export everything from this canonical location
from src.mock_tools import (  # noqa: F401
    available_tools,
    tool_processor,
    hospital_info,
    doctor_availability,
    appointment_booking,
    report_status,
    emergency_handoff,
)
