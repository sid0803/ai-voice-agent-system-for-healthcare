"""Type definitions and configuration constants.

Merged Python equivalent of TypeScript's types.ts and consts.ts.
"""

import os
from dataclasses import dataclass
from typing import Literal, Optional

# Type aliases matching TypeScript union types
ContentType = Literal["AUDIO", "TEXT", "TOOL"]
AudioType = Literal["SPEECH"]
AudioMediaType = Literal["audio/wav", "audio/lpcm", "audio/mulaw", "audio/mpeg"]
TextMediaType = Literal["text/plain", "application/json"]


@dataclass(frozen=True)
class InferenceConfig:
    # 150 tokens = ~100-120 words, optimal for 1-2 conversational sentences without chunking
    max_tokens: int = 150
    top_p: float = 0.9
    temperature: float = 0.7


@dataclass(frozen=True)
class AudioConfiguration:
    audio_type: AudioType = "SPEECH"
    media_type: AudioMediaType = "audio/lpcm"
    sample_rate_hertz: int = 8000
    sample_size_bits: int = 16
    channel_count: int = 1
    encoding: str = "base64"
    voice_id: Optional[str] = None
    endpointing_sensitivity: Optional[str] = "MEDIUM"


@dataclass(frozen=True)
class TextConfiguration:
    media_type: TextMediaType = "text/plain"


@dataclass(frozen=True)
class ToolConfiguration:
    tool_use_id: str = ""
    type: str = "TEXT"
    media_type: TextMediaType = "text/plain"


# Default configuration instances matching TypeScript consts.ts values
DEFAULT_INFERENCE_CONFIG = InferenceConfig()

DEFAULT_AUDIO_INPUT_CONFIG = AudioConfiguration(endpointing_sensitivity="MEDIUM")

DEFAULT_AUDIO_OUTPUT_CONFIG = AudioConfiguration(
    voice_id=os.getenv("NOVA_VOICE_ID", "kiara")
)

DEFAULT_TEXT_CONFIG = TextConfiguration()

DEFAULT_SYSTEM_PROMPT = (
    "You are Asha, a helpful hospital receptionist. Your goal is to assist callers "
    "with medical appointments and information efficiently and politely. "
    "Keep responses very short and clear."
)

DEFAULT_TOOL_SCHEMA = (
    '{"$schema":"http://json-schema.org/draft-07/schema#",'
    '"type":"object",'
    '"properties":{},'
    '"required":[]}'
)
