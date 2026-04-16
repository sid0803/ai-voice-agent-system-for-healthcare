import audioop
import logging

logger = logging.getLogger(__name__)

class AudioHardener:
    """Hardens audio for clinical environments using Noise Gating and AGC.
    
    Optimized for 16-bit signed LE, 8kHz, mono audio.
    """
    def __init__(self, sample_width=2):
        self.sample_width = sample_width
        self.noise_floor = 100.0  # Initial noise floor estimate
        self.alpha = 0.05         # Smoothing factor for noise floor adaptation
        self.gate_multiplier = 1.8 # Signal must be 1.8x louder than noise floor
        self.target_rms = 4000.0   # Target level for human speech (normalized)
        self.max_gain = 5.0       # Maximum boost factor
        
    def process_chunk(self, data: bytes) -> bytes:
        """Apply noise suppression and gain normalization to a PCM chunk."""
        if not data:
            return data
            
        try:
            current_rms = float(audioop.rms(data, self.sample_width))
            
            # 1. Update Adaptive Noise Floor (slowly adapt to constant hums)
            if current_rms < self.noise_floor:
                self.noise_floor = (1 - self.alpha) * self.noise_floor + self.alpha * current_rms
            elif current_rms < self.noise_floor * 2:
                # Still likely background noise, adapt slightly
                self.noise_floor = (1 - self.alpha * 0.1) * self.noise_floor + (self.alpha * 0.1) * current_rms

            # 2. Noise Gate (Suppress steady hum or chaotic low-level chatter)
            if current_rms < self.noise_floor * self.gate_multiplier:
                # Signal is too close to noise floor, return digital silence
                return b'\x00' * len(data)

            # 3. Automatic Gain Control (Boost low patient voices)
            if current_rms > 0:
                gain = min(self.max_gain, self.target_rms / current_rms)
                if gain > 1.1: # Only boost if significant gain is needed
                    data = audioop.mul(data, self.sample_width, gain)
            
            return data
            
        except Exception:
            logger.exception("Error in audio hardening pipeline")
            return data

def exotel_to_pcm(data: bytes) -> bytes:
    """Convert Exotel audio to Nova Sonic PCM format."""
    return data

def pcm_to_exotel(data: bytes) -> bytes:
    """Convert Nova Sonic PCM audio to Exotel format."""
    return data
