"""Audio encoding conversion utilities for Exotel PCM ↔ Nova Sonic PCM.

Exotel voice bot applet sends/receives raw PCM audio (16-bit signed LE, 8kHz, mono).
Nova Sonic also uses 16-bit signed LE PCM at 8kHz mono, so the conversion is
essentially a passthrough. These functions exist as a shim in case Exotel changes
its audio format or we need to add resampling later.
"""


def exotel_to_pcm(data: bytes) -> bytes:
    """Convert Exotel audio to Nova Sonic PCM format.

    Both use 16-bit signed LE, 8kHz, mono — so this is a passthrough.

    Args:
        data: Raw PCM audio bytes from Exotel.

    Returns:
        16-bit signed little-endian PCM audio bytes for Nova Sonic.
    """
    return data


def pcm_to_exotel(data: bytes) -> bytes:
    """Convert Nova Sonic PCM audio to Exotel format.

    Both use 16-bit signed LE, 8kHz, mono — so this is a passthrough.

    Args:
        data: 16-bit signed little-endian PCM audio bytes from Nova Sonic.

    Returns:
        PCM audio bytes for Exotel.
    """
    return data
