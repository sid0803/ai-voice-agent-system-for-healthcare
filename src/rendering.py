"""Phonetic and text-to-speech value renderers for speech synthesis.

Converts structured entities (reference IDs, currency, time) into spoken
conversational representations optimized for voice assistants.
"""

from typing import Union


def render_reference_id(ref_id: str) -> str:
    """Spells out reference ID clearly for voice synthesis.
    
    Example: 'IS-APP-123023-BCA6' -> 'I S dash A P P dash one two three zero two three dash B C A six'
    """
    if not ref_id:
        return ""
    digit_words = {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"
    }
    parts = ref_id.split("-")
    rendered_parts = []
    for part in parts:
        rendered_chars = []
        for ch in part:
            if ch.isdigit():
                rendered_chars.append(digit_words.get(ch, ch))
            elif ch.isalpha():
                rendered_chars.append(ch.upper())
            else:
                rendered_chars.append(ch)
        rendered_parts.append(" ".join(rendered_chars))
    return " dash ".join(rendered_parts)


def render_currency(amount: Union[int, float, str]) -> str:
    """Renders currency deterministically into spoken format.
    
    Example: 900 -> '900 rupees'
    """
    if amount is None:
        return ""
    try:
        val = int(amount)
        return f"{val} rupees"
    except (ValueError, TypeError):
        return f"{amount} rupees"


def render_time(time_24h: str) -> str:
    """Renders 24h time into spoken conversational format.
    
    Example: '17:00' -> '5 PM', '09:30' -> '9:30 AM'
    """
    if not time_24h or ":" not in str(time_24h):
        return str(time_24h)
    try:
        parts = str(time_24h).split(":")
        hr = int(parts[0])
        mn = int(parts[1])
        disp_hr = hr if hr <= 12 else hr - 12
        disp_hr = 12 if disp_hr == 0 else disp_hr
        period = "AM" if hr < 12 else "PM"
        return f"{disp_hr}:{mn:02d} {period}" if mn > 0 else f"{disp_hr} {period}"
    except Exception:
        return str(time_24h)
