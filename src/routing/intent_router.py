"""Semantic Intent Router for cost and latency optimization.

Detects high-frequency intents (Greeting, Emergency, Handoff) using 
lightweight keyword matching and simple string heuristics to bypass 
expensive Bedrock Nova Sonic streams.
"""

import logging
from typing import Dict, Optional, Literal

logger = logging.getLogger(__name__)

# Intent Types
IntentType = Literal["GREETING", "EMERGENCY", "HANDOFF", "HOSPITAL_INFO", "UNKNOWN"]

# Keyword mappings for Indian Healthcare Context (English + Hindi/Hinglish)
KEYWORDS: Dict[IntentType, list[str]] = {
    "GREETING": [
        "hello", "hi", "namaste", "namaskar", "good morning", "asalaam", 
        "hey", "kaise ho", "sunye"
    ],
    "EMERGENCY": [
        "emergency", "bachao", "accident", "blood", "severe pain", "chest pain",
        "saans", "breathing", "stroke", "dil ka daura", "unconscious"
    ],
    "HANDOFF": [
        "receptionist", "staff", "human", "insan", "baat karado", "junior",
        "senior", "manager", "admin", "connect me"
    ],
    "HOSPITAL_INFO": [
        "hospital kahan hai", "location", "address", "timing", "open", "fee",
        "doctor kaun hai", "list of doctors", "fees"
    ]
}

class IntentRouter:
    """Predicts user intent from transcription or raw heuristics."""

    def __init__(self):
        # We can expand this with a small local embedding check if needed
        pass

    def route(self, text: str) -> IntentType:
        """Route text to an intent type."""
        if not text:
            return "UNKNOWN"
        
        normalized = text.lower().strip()
        
        # Priority 1: Emergency check
        for kw in KEYWORDS["EMERGENCY"]:
            if kw in normalized:
                return "EMERGENCY"
        
        # Priority 2: Handoff check
        for kw in KEYWORDS["HANDOFF"]:
            if kw in normalized:
                return "HANDOFF"
        
        # Priority 3: Simple Greeting
        for kw in KEYWORDS["GREETING"]:
            if normalized == kw or normalized.startswith(kw + " "):
                return "GREETING"
        
        # Priority 4: Hospital Basic Info
        for kw in KEYWORDS["HOSPITAL_INFO"]:
            if kw in normalized:
                return "HOSPITAL_INFO"
        
        return "UNKNOWN"

    def get_static_response_id(self, intent: IntentType) -> Optional[str]:
        """Maps an intent back to a static PCM asset ID if available."""
        mapping = {
            "GREETING": "hello",
            "EMERGENCY": "emergency",
            "HANDOFF": "transfer"
        }
        return mapping.get(intent)

# Singleton instance
intent_router = IntentRouter()
