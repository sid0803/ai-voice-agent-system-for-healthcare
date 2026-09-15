"""
Unit tests for src/language.py (Phase 4.1).

Validates:
1. Language detection across English, Hindi (Devanagari & Romanized/Hinglish), and Bengali.
2. Gender guard grammatical corrections and robotic enthusiasm stripping.
3. Liveness check detection for caller silence checks.
4. Acoustic filler phrase structures.
5. Backward-compatibility re-exports from src.server.
"""

import pytest
from src.language import (
    detect_language,
    LANGUAGE_INSTRUCTIONS,
    _apply_gender_guard,
    _is_liveness_check,
    _ALL_LIVENESS_PHRASES,
    _FILLER_PHRASES,
    _FILLER_COOLDOWN_SEC,
)


def test_detect_language_english():
    """English text should be detected as 'english'."""
    queries = [
        "I want to book an appointment with Dr. Sharma tomorrow",
        "What are the OPD consultation hours for cardiology?",
        "Please tell me the consultation fee for neurology",
        "Where is the hospital located?",
    ]
    for q in queries:
        assert detect_language(q) == "english", f"Expected 'english' for '{q}'"


def test_detect_language_hindi_devanagari():
    """Hindi written in Devanagari script should be detected as 'hindi'."""
    queries = [
        "मुझे डॉक्टर शर्मा से मिलना है",
        "क्या कल कोई अपॉइंटमेंट खाली है?",
        "हड्डी रोग विशेषज्ञ की फीस कितनी है?",
    ]
    for q in queries:
        assert detect_language(q) == "hindi", f"Expected 'hindi' for '{q}'"


def test_detect_language_bengali_script():
    """Bengali written in Bengali script should be detected as 'bengali'."""
    queries = [
        "আমি ডাক্তার দেখাতে চাই",
        "কালকে কি কোনো সময় পাওয়া যাবে?",
        "হাসপাতাল কোথায় অবস্থিত?",
    ]
    for q in queries:
        assert detect_language(q) == "bengali", f"Expected 'bengali' for '{q}'"


def test_detect_language_hinglish_romanized():
    """Hinglish written in Latin script should be detected as 'hinglish'."""
    queries = [
        "mujhe doctor sharma se appointment chahiye kal",
        "kya kal doctor available hai subah mein?",
        "cardiology ki fees kitni hogi please bataye",
        "main kal subah doctor ke paas aana chahta hoon",
    ]
    for q in queries:
        assert detect_language(q) == "hinglish", f"Expected 'hinglish' for '{q}'"


def test_language_instructions_defined():
    """All supported language modes must have defined prompt instructions."""
    for lang in ["english", "hindi", "hinglish", "bengali"]:
        assert lang in LANGUAGE_INSTRUCTIONS
        assert len(LANGUAGE_INSTRUCTIONS[lang]) > 20
        assert "Asha" in LANGUAGE_INSTRUCTIONS[lang] or "Reply" in LANGUAGE_INSTRUCTIONS[lang]


def test_gender_guard_and_robotic_stripping():
    """_apply_gender_guard strips robotic prefixes and collapses double doctor titles."""
    # 1. Strips robotic enthusiastic prefixes
    robotic_text = "Sure thing! Dr. Sharma is available tomorrow morning."
    cleaned = _apply_gender_guard(robotic_text)
    assert not cleaned.startswith("Sure thing")
    assert "Dr. Sharma" in cleaned

    # 2. Collapses double doctor titles
    double_doc = "I will connect you with Doctor Dr. Sharma right away."
    fixed_doc = _apply_gender_guard(double_doc)
    assert "Doctor Dr." not in fixed_doc
    assert "Dr. Sharma" in fixed_doc

    # 3. Hindi neutral forms
    hindi_phrase = "आप डॉक्टर से कब मिलना चाहती हैं?"
    neutral_phrase = _apply_gender_guard(hindi_phrase)
    assert "चाहते हैं" in neutral_phrase
    assert "चाहती हैं" not in neutral_phrase


def test_liveness_detection():
    """Liveness check phrases must be recognized correctly."""
    assert _is_liveness_check("hello?")
    assert _is_liveness_check("are you there?")
    assert _is_liveness_check("hello asha")
    assert _is_liveness_check("सुन रहे हो?")

    # Normal queries should not trigger liveness
    assert not _is_liveness_check("I want to book an appointment")
    assert not _is_liveness_check("dr sharma kab aate hain")


def test_acoustic_fillers_defined():
    """Acoustic fillers must be non-empty lists for all active audio codes."""
    for lang in ["en", "hi", "hi-en", "bn"]:
        assert lang in _FILLER_PHRASES
        assert len(_FILLER_PHRASES[lang]) > 0
        for phrase in _FILLER_PHRASES[lang]:
            assert len(phrase) > 0
    assert _FILLER_COOLDOWN_SEC >= 1.0


def test_server_reexports_language_module():
    """src.server must re-export language symbols for backward compatibility."""
    import src.server as s
    assert hasattr(s, "detect_language")
    assert hasattr(s, "LANGUAGE_INSTRUCTIONS")
    assert hasattr(s, "_apply_gender_guard")
    assert hasattr(s, "_is_liveness_check")
    assert hasattr(s, "_ALL_LIVENESS_PHRASES")
    assert hasattr(s, "_FILLER_PHRASES")
