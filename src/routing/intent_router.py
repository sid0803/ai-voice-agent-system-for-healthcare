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
        "hello", "hi", "namaste", "namaskar", "good morning", "hey", "kaise ho", "sunye"
    ],
    "EMERGENCY_STRONG": [
        "chest pain", "seena dard", "saans nahi aa rahi", "ghutan", "saans ruk rahi",
        "accident", "blood", "stroke", "dil ka daura", "unconscious", "emergency", "bachao"
    ],
    "EMERGENCY_WEAK": [
        "pain", "dard", "jalan", "be-chaini", "chakkar", "dizziness", "uneasy", "heavy"
    ],
    "DISTRESS": [
        "scared", "darr lag raha hai", "help", "please", "unbearable", "severely", "bahut zyada"
    ],
    "HANDOFF": [
        "receptionist", "staff", "human", "insan", "baat karado", "admin", "connect me"
    ]
}

class IntentRouter:
    """Predicts user intent from transcription with Signal Fusion (Strong vs Weak)."""

    def route(self, text: str) -> IntentType:
        """Route text using Clinical Fusion Logic: Strong > (Weak + Weak) > Normal."""
        if not text:
            return "UNKNOWN"
        
        normalized = text.lower().strip()
        
        # 1. Check for STRONG Emergency Signals (Immediate Trigger)
        for kw in KEYWORDS["EMERGENCY_STRONG"]:
            if kw in normalized:
                logger.info(f"[ROUTING] Strong emergency signal detected: {kw}")
                return "EMERGENCY"

        # 2. Check for Signal Fusion (Weak + Weak OR Weak + Distress)
        weak_count = sum(1 for kw in KEYWORDS["EMERGENCY_WEAK"] if kw in normalized)
        distress_count = sum(1 for kw in KEYWORDS["DISTRESS"] if kw in normalized)
        
        if (weak_count >= 2) or (weak_count >= 1 and distress_count >= 1):
            logger.info(f"[ROUTING] Signal fusion triggered (Weak={weak_count}, Distress={distress_count})")
            return "EMERGENCY"
        
        # 3. Priority 2: Handoff check
        for kw in KEYWORDS["HANDOFF"]:
            if kw in normalized:
                return "HANDOFF"
        
        # 4. Priority 3: Simple Greeting
        for kw in KEYWORDS["GREETING"]:
            if normalized == kw or normalized.startswith(kw + " "):
                return "GREETING"
        
        return "UNKNOWN"

    def get_static_response_id(self, intent: IntentType) -> Optional[str]:
        """Maps an intent back to a static PCM asset ID."""
        mapping = {"GREETING": "hello", "EMERGENCY": "emergency", "HANDOFF": "transfer"}
        return mapping.get(intent)

# Singleton instance
intent_router = IntentRouter()
