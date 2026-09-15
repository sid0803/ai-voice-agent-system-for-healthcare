"""Multilingual processing, language detection, gender guards, and conversational fillers.

Supports Hindi (Devanagari), Hinglish (Romanized Hindi), Bengali, and English
with real-time turn-level language detection and prompt injection instructions.
"""

import re
from typing import Dict, List, Tuple

# ── Language Detection ───────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """Returns 'bengali', 'hindi', 'hinglish', or 'english' based on the caller's text.
    
    Called on every user utterance for per-turn language mirroring.
    """
    if not text:
        return "english"

    # 1. Check for Bengali script characters (Unicode range U+0980–U+09FF)
    bengali_count = sum(1 for ch in text if '\u0980' <= ch <= '\u09FF')
    if bengali_count >= 2:
        return "bengali"

    # 2. Check for Devanagari script characters (Unicode range U+0900–U+097F)
    # Require >= 3 Devanagari characters to avoid single-char noise switching
    devanagari_count = sum(1 for ch in text if '\u0900' <= ch <= '\u097F')
    if devanagari_count >= 3:
        return "hindi"

    cleaned_text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = cleaned_text.split()

    # Explicit language switch intent check
    if any(p in cleaned_text for p in ["in bengali", "in bangla", "speak bengali", "speak in bengali", "with bengali", "bangla te"]):
        return "bengali"

    # Core Bengali words in Roman script
    core_bengali_roman_words = {
        "bhalo", "achen", "kemon", "aami", "ami", "apni", "apnar", "tumi", "tomar",
        "kintu", "korbo", "korben", "bolun", "bolte", "lagbe", "hobe", "dorkar",
        "dekhte", "shunchen", "shunte", "parchi", "ekhon", "kaalke", "aajke"
    }
    if any(w in core_bengali_roman_words for w in words):
        return "bengali"

    # Hinglish = Roman script but contains Hindi/Urdu words or ASR phonetic variations
    core_hindi_roman_words = {
        "hai", "hain", "hoon", "kya", "kab", "kaise", "kahaan", "kidhar", "kyun", "kaun", "kaunse",
        "kiska", "kiski", "kiske", "kitna", "kitne", "mujhe", "mera", "meri", "hum", 
        "humara", "humari", "humare", "aap", "aapka", "aapki", "aapke", "tum", "tumhara", 
        "tumhari", "tumhare", "apna", "apni", "apne", "liye", "saath", "paas", "karna", "karo", "karein", "karni", 
        "karta", "karti", "karte", "krna", "kro", "chahiye", "chahie", "chahye", 
        "batao", "bataiye", "batana", "btao", "btaiye", "nahi", "nahin", "theek", 
        "achha", "acha", "thik", "parso", "abhi", "pehle", "baad", 
        "bhi", "lekin", "toh", "suniye", "milna", "milenge", "milengi", "milte", "milega", "milegi", "dekhna", 
        "dikhana", "dikhao", "chalega", "bataye", "batayein", "bataoge", "karenge", "karegi", "karega", "dopahar", "dupahar", "dufair",
        "subah", "shaam", "raat", "bolo", "sakta", "sakti", "ki", "ka", "ke", "ko", "se", "mein", "me", "hoga", "hogi", "kitni"
    }

    # Require >= 2 Hinglish words or 1 unambiguous strong word to switch from English
    strong_hinglish_words = {"chahiye", "bataiye", "bataye", "karta", "karti", "humara", "tumhara", "kijiye", "aapka", "aapki", "aapke", "kaunse", "milenge", "karenge"}
    matched_words = [w for w in words if w in core_hindi_roman_words]
    
    if len(matched_words) >= 2 or any(w in strong_hinglish_words for w in words):
        return "hinglish"

    return "english"


# ── Hindi Gender-Neutral & Persona Output Guard ──────────────────────────────

_GENDER_REPLACEMENTS: List[Tuple[str, str]] = [
    ("चाहती हैं", "चाहते हैं"),
    ("सकती हैं", "सकते हैं"),
    ("बताती हैं", "बताते हैं"),
    ("करती हैं", "करते हैं"),
    ("आना चाहती", "आना चाहते"),
    ("दिखाना चाहती", "दिखाना चाहते"),
    ("पूछना चाहती", "पूछना चाहते"),
]

_BOT_WORDS_PATTERN = re.compile(
    r'^\s*(Sure thing!?|Great news!?|Good news!?|Perfect!?|Sure!?|Certainly!?|Wonderful!?|Excellent!?|Absolutely!?|Of course!?|'
    r'बेहतरीन!?|बिल्कुल सही!?|बिल्कुल!?|ज़रूर!?|अवश्य!?|शानदार!?|बढ़िया!?|वाह!?|ज़बरदस्त!?)\s*[,।!-]?\s*',
    re.IGNORECASE | re.UNICODE
)


def _apply_gender_guard(text: str) -> str:
    """Sanitizes generated speech for persona tone and gender neutrality."""
    if not text:
        return ""
    # 1. Strip leading robotic enthusiasm prefixes
    text = _BOT_WORDS_PATTERN.sub('', text)

    # 2. Collapse double doctor prefixes in all languages
    text = re.sub(r'\bDr\.?\s+Dr\.?\b', 'Dr.', text, flags=re.IGNORECASE)
    text = re.sub(r'डॉ\.?\s*Dr\.?\b', 'डॉ.', text, flags=re.IGNORECASE)
    text = re.sub(r'डॉ\.?\s*डॉ\.?', 'डॉ.', text)
    text = re.sub(r'\bDoctor\s+Dr\.?\b', 'Dr.', text, flags=re.IGNORECASE)
    text = re.sub(r'\bDoctor\s+Doctor\b', 'Doctor', text, flags=re.IGNORECASE)
    text = re.sub(r'डॉक्टर\s+डॉ\.?', 'डॉक्टर', text)
    text = re.sub(r'डॉक्टर\s+Dr\.?\b', 'डॉक्टर', text, flags=re.IGNORECASE)
    text = re.sub(r'डॉ\.\.+', 'डॉ.', text)
    text = re.sub(r'Dr\.\.+', 'Dr.', text)

    # 3. Apply gender neutral replacements
    for female, neutral in _GENDER_REPLACEMENTS:
        text = text.replace(female, neutral)
    return text


# ── Liveness Check Detection (Context Lock) ───────────────────────────────────

_LIVENESS_EN = [
    "am i audible", "are you there", "hello asha", "hello", "hi",
    "can you hear me", "are you listening", "still there", "you there",
    "anyone there", "asha", "hello hello", "hello asha hello"
]
_LIVENESS_HI = [
    "सुन रहे हो", "क्या आप सुन रहे", "हेलो", "हाँ", "हां",
    "सुनाई दे रहा", "हो वहाँ", "आशा", "हेलो आशा", "सुन पा रहे", "आवाज़ आ रही"
]
_ALL_LIVENESS_PHRASES = _LIVENESS_EN + _LIVENESS_HI


def _is_liveness_check(text: str) -> bool:
    """True if caller input is a connection check, not a new inquiry."""
    normalized = text.lower().strip().rstrip("?!.,")
    if len(normalized.split()) > 6:
        return False
    return any(phrase in normalized for phrase in _ALL_LIVENESS_PHRASES)


# ── Language Injection Instructions ──────────────────────────────────────────

LANGUAGE_INSTRUCTIONS: Dict[str, str] = {
    "hindi": (
        "[SYSTEM: Caller spoke HINDI. Reply 100% in Hindi Devanagari script ONLY. "
        "Asha: use feminine verbs (करती हूँ, बताती हूँ). "
        "Caller: use respectful gender-neutral आप forms (चाहिए, बताइए, कीजिए, बोलिए). NEVER use चाहती/चाहते for caller.]"
    ),
    "hinglish": (
        "[SYSTEM: Caller spoke HINGLISH. Reply 100% in Hinglish Roman script ONLY. "
        "Asha: karti hoon, batati hoon. "
        "Caller: use gender-neutral forms (chahiye, bataiye, kijiye, boliye). NEVER use chahti/chahte for caller.]"
    ),
    "english": (
        "[SYSTEM: Caller spoke ENGLISH. Reply 100% in ENGLISH ONLY. "
        "CLEAR PREVIOUS HINDI/BENGALI CONTEXT IMMEDIATELY. Do NOT output any Hindi, Bengali, or Devanagari words.]"
    ),
    "bengali": (
        "[SYSTEM: Caller requested/spoke BENGALI. Reply 100% in natural Bengali script ONLY. "
        "Asha: Use polite and natural Bengali (যেমন: হ্যাঁ, আমি দেখছি, এক মুহূর্ত সময় দিন, আপনাকে কীভাবে সাহায্য করতে পারি?). "
        "Caller: Use respectful আপনি forms (বলুন, লাগবে, চান). NEVER output empty punctuation or lonely dandas.]"
    ),
}


# ── Acoustic Filler Phrases (Tool-Execution Dead Air Bridge) ──────────────────

_FILLER_PHRASES: Dict[str, List[str]] = {
    "en":    [
        "Let me check that for you.",
        "One moment please.",
        "Looking that up now."
    ],
    "hi":    [
        "जी, एक क्षण।",
        "मैं देख रही हूँ।",
        "अभी चेक करती हूँ।"
    ],
    "hi-en": [
        "Let me check that — ek moment.",
        "देखती हूँ — one moment."
    ],
    "bn":    [
        "একটু অপেক্ষা করুন, দেখছি।",
        "আমি এখনই তথ্যটি দেখছি।",
        "এক মুহূর্ত সময় দিন।"
    ],
}

_FILLER_COOLDOWN_SEC: float = 4.0
