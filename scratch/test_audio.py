import sys
import os
sys.path.append(os.path.join(os.getcwd(), "src"))
from audio_utils import pcm_to_exotel, exotel_to_pcm
import numpy as np

# Create a test 16-bit PCM chunk with a simple sine wave (to avoid zero silence)
t = np.linspace(0, 1, 8000)
sine_wave = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
pcm_bytes = sine_wave.tobytes()

# Convert to Exotel (u-law)
exotel_bytes = pcm_to_exotel(pcm_bytes)

# Convert back to PCM
recovered_pcm_bytes = exotel_to_pcm(exotel_bytes)
recovered_samples = np.frombuffer(recovered_pcm_bytes, dtype=np.int16)

# Print results
print(f"Original size: {len(pcm_bytes)} bytes")
print(f"Exotel size: {len(exotel_bytes)} bytes")
print(f"Recovered size: {len(recovered_pcm_bytes)} bytes")

# Check if it is all zeros
exotel_samples = np.frombuffer(exotel_bytes, dtype=np.uint8)
all_zeros_exotel = np.all(exotel_samples == 0)
all_silent_exotel = np.all(exotel_samples == 0xFF) # 0xFF is silence in u-law

print(f"Are Exotel bytes all 0x00? {all_zeros_exotel}")
print(f"Are Exotel bytes all 0xFF (u-law silence)? {all_silent_exotel}")
print(f"Mean absolute amplitude: {np.mean(np.abs(recovered_samples))}")
print(f"Max absolute amplitude: {np.max(np.abs(recovered_samples))}")
