"""
Generate real speech PCM audio assets using AWS Polly.

Synthesizes proper human-quality voice for:
  - transfer.pcm  : "Please hold while I connect you..." 
  - emergency.pcm : "I understand this is an emergency..."

Uses Polly's "Aditi" voice — Indian English, female — at 8kHz mono PCM,
which is the exact format expected by the Exotel WebSocket pipeline.

Run from project root:
  python scratch/generate_real_audio.py
"""

import pathlib
import sys
import os
import math
import struct

# Load .env so credentials are available
from dotenv import load_dotenv
load_dotenv()

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ASSETS_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

SAMPLE_RATE = 8000   # Hz — must match Exotel / Nova Sonic pipeline
VOICE_ID    = "Aditi"   # Indian English, female, standard engine
ENGINE      = "standard"  # Neural requires 16kHz; standard supports 8kHz PCM

# Messages — keep concise for telephony (< 5 seconds each)
MESSAGES = {
    "transfer.pcm": (
        "Please hold while I connect you to our hospital reception staff. "
        "Thank you for your patience."
    ),
    "emergency.pcm": (
        "I understand this is an emergency. "
        "Please stay on the line. "
        "I am connecting you to our emergency medical team right now."
    ),
}

# ---------------------------------------------------------------------------
# Polly synthesis
# ---------------------------------------------------------------------------
def get_polly_client():
    """Build a Polly client from .env credentials or EC2 IAM role."""
    region = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
    session_kwargs = {"region_name": region}
    key    = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    token  = os.environ.get("AWS_SESSION_TOKEN")
    if key and secret:
        session_kwargs["aws_access_key_id"]     = key
        session_kwargs["aws_secret_access_key"] = secret
        if token:
            session_kwargs["aws_session_token"] = token
    return boto3.client("polly", **session_kwargs)


def synthesize_with_polly(polly, text: str, output_path: pathlib.Path) -> bool:
    """Synthesize speech and write raw PCM bytes. Returns True on success."""
    try:
        resp = polly.synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            SampleRate=str(SAMPLE_RATE),
            VoiceId=VOICE_ID,
            Engine=ENGINE,
        )
        pcm_bytes = resp["AudioStream"].read()
        output_path.write_bytes(pcm_bytes)
        duration_ms = len(pcm_bytes) / (SAMPLE_RATE * 2) * 1000
        print(f"  [OK]  {output_path.name}: {len(pcm_bytes):,} bytes  (~{duration_ms:.0f} ms)  --  Polly [{VOICE_ID}]")
        return True
    except (BotoCoreError, ClientError) as exc:
        print(f"  [WARN] Polly failed for {output_path.name}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Fallback: synthesize a recognisable telephony hold-tone (440 Hz sine wave)
# Much better than silence — callers hear SOMETHING while waiting.
# ---------------------------------------------------------------------------
def synthesize_hold_tone(output_path: pathlib.Path, duration_sec: float = 3.0):
    """Generate a 440 Hz sine-wave tone as a last-resort PCM placeholder."""
    n_samples = int(SAMPLE_RATE * duration_sec)
    freq      = 440.0       # A4 tone
    amplitude = 8000        # ~25% of max 16-bit to avoid distortion
    samples   = [
        int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
        for i in range(n_samples)
    ]
    pcm_bytes = struct.pack(f"<{n_samples}h", *samples)
    output_path.write_bytes(pcm_bytes)
    print(f"  [WARN] {output_path.name}: {len(pcm_bytes):,} bytes  (~{duration_sec:.0f}s hold tone)  --  FALLBACK (Polly unavailable)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  InDiiServe -- PCM Audio Asset Generator")
    print("=" * 60)
    print(f"  Voice       : {VOICE_ID} (Indian English, Female)")
    print(f"  Sample Rate : {SAMPLE_RATE} Hz  |  16-bit Signed LE  |  Mono")
    print(f"  Output dir  : {ASSETS_DIR}")
    print()

    try:
        polly = get_polly_client()
        polly_available = True
        print("  AWS Polly client initialized.")
    except Exception as exc:
        print(f"  Could not init Polly client: {exc}")
        polly_available = False

    print()

    polly_success_count = 0

    for filename, text in MESSAGES.items():
        output_path = ASSETS_DIR / filename
        print(f"  Generating: {filename}")
        print(f"  Text      : \"{text[:80]}...\"" if len(text) > 80 else f"  Text      : \"{text}\"")

        success = False
        if polly_available:
            success = synthesize_with_polly(polly, text, output_path)

        if success:
            polly_success_count += 1
        else:
            synthesize_hold_tone(output_path, duration_sec=3.0)

        print()

    print("=" * 60)
    print("  Done! Assets written to:")
    for filename in MESSAGES:
        p = ASSETS_DIR / filename
        print(f"    {p.name}  ({p.stat().st_size:,} bytes)")
    print()
    if polly_success_count == len(MESSAGES):
        print("  [SUCCESS] All assets are real Polly speech. Production-ready.")
    elif polly_success_count > 0:
        print(f"  [PARTIAL] {polly_success_count}/{len(MESSAGES)} files used Polly. Rest are hold-tones.")
        print("  Re-run after fixing credentials to regenerate missing files.")
    else:
        print("  [FALLBACK] No Polly credentials found. Assets are 440Hz hold-tones.")
        print("  Fill AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in .env and re-run.")
    print("=" * 60)



if __name__ == "__main__":
    main()
