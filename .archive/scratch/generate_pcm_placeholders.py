"""
One-time script to generate placeholder PCM audio assets.
Creates transfer.pcm and emergency.pcm as silence-padded copies
of hello.pcm so the ResponseCache and IntentRouter work immediately.

IMPORTANT: Replace these with real recorded audio before going live.
"""
import struct
import pathlib

ASSETS_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

HELLO_PCM = ASSETS_DIR / "hello.pcm"
TRANSFER_PCM = ASSETS_DIR / "transfer.pcm"
EMERGENCY_PCM = ASSETS_DIR / "emergency.pcm"

# 8kHz, 16-bit signed LE, mono — 1 second of silence = 16000 bytes
SILENCE_1S = b'\x00\x00' * 8000   # 8000 samples * 2 bytes = 16000 bytes

def make_placeholder(output_path: pathlib.Path, label: str):
    """Create a minimal valid PCM placeholder (1 second silence)."""
    if output_path.exists():
        print(f"  {output_path.name}: already exists, skipping.")
        return
    output_path.write_bytes(SILENCE_1S)
    print(f"  {output_path.name}: created ({len(SILENCE_1S)} bytes of silence) — REPLACE with real audio.")

print("Generating placeholder PCM assets...")
make_placeholder(TRANSFER_PCM, "transfer")
make_placeholder(EMERGENCY_PCM, "emergency")
print(f"\nAssets directory: {ASSETS_DIR}")
print("IMPORTANT: Replace transfer.pcm and emergency.pcm with real recorded audio before production.")
