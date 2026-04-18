import audioop
import logging
import numpy as np

logger = logging.getLogger(__name__)

class AudioHardener:
    """Hardens inbound audio (Patient to AI) for noisy environments.
    
    Features: 
    - High-Pass Filter (removes traffic/engine rumble)
    - Adaptive Noise Floor estimation
    - Soft-Knee Noise Gate (suppresses background chatter without harsh cuts)
    - Automatic Gain Control (boosts distant voices)
    """
    def __init__(self, sample_rate=8000):
        self.sample_rate = sample_rate
        self.sample_width = 2
        
        # Filtering state
        self.hpf_alpha = 0.85  # Cutoff approx 150-200Hz for 8kHz
        self.prev_x = 0.0
        self.prev_y = 0.0
        
        # Noise Gate / AGC state
        self.noise_floor = 150.0
        self.alpha_noise = 0.02
        self.target_rms = 4500.0
        self.max_gain = 4.0
        self.gate_threshold_mult = 2.0
        
    def _apply_hpf(self, samples: np.ndarray) -> np.ndarray:
        """Simple first-order high-pass filter to remove low-end rumble."""
        out = np.zeros_like(samples)
        x_prev = self.prev_x
        y_prev = self.prev_y
        
        for i in range(len(samples)):
            out[i] = self.hpf_alpha * (y_prev + samples[i] - x_prev)
            x_prev = samples[i]
            y_prev = out[i]
            
        self.prev_x = x_prev
        self.prev_y = y_prev
        return out

    def process_chunk(self, data: bytes) -> bytes:
        """Apply noise suppression and gain normalization to a PCM chunk."""
        if not data:
            return data
            
        try:
            # 1. Convert to numpy for filtering
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            
            # 2. High Pass Filter (Traffic Removal)
            samples = self._apply_hpf(samples)
            
            # 3. Analyze RMS
            current_rms = np.sqrt(np.mean(samples**2))
            
            # 4. Adaptive Noise Floor
            if current_rms < self.noise_floor:
                self.noise_floor = (1 - self.alpha_noise) * self.noise_floor + self.alpha_noise * current_rms
            
            # 5. Soft-Knee Noise Gate
            # If below threshold, attenuate heavily (don't mute completely for natural feel)
            gate_threshold = self.noise_floor * self.gate_threshold_mult
            if current_rms < gate_threshold:
                # Gradual reduction instead of silence
                attenuation = max(0.05, current_rms / (gate_threshold * 2))
                samples *= attenuation
            else:
                # 6. AGC (Only when speech is detected)
                if current_rms > 0:
                    gain = min(self.max_gain, self.target_rms / current_rms)
                    if gain > 1.05:
                        samples *= gain

            # 7. Clip and return
            return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
            
        except Exception:
            logger.exception("Inbound audio hardening failed")
            return data


class AudioPolisher:
    """Polishes outbound audio (AI to Patient) for clarity in noise.
    
    Features:
    - Dynamic Range Compression (makes Asha's voice 'pierce' through background noise)
    - Treble Boost (increases intelligibility for elderly/phone speakers)
    """
    def __init__(self):
        self.threshold = 5000.0
        self.ratio = 4.0        # 4:1 compression
        self.makeup_gain = 1.6  # 4dB makeup boost
        
        # Simple Treble Boost filter (High Shelf)
        self.shelf_alpha = 0.4  # Approx 3kHz boost
        self.prev_x = 0.0
        self.prev_y = 0.0

    def _apply_treble_boost(self, samples: np.ndarray) -> np.ndarray:
        """Simple high-shelf boost for clarity."""
        out = np.zeros_like(samples)
        y_prev = self.prev_y
        
        for i in range(len(samples)):
            low_content = (1 - self.shelf_alpha) * y_prev + self.shelf_alpha * samples[i]
            high_content = samples[i] - low_content
            out[i] = low_content + (high_content * 1.8) # Boost high by 80%
            y_prev = low_content
            
        self.prev_y = y_prev
        return out

    def process_chunk(self, data: bytes) -> bytes:
        """Apply compression and HF enhancement to outbound PCM."""
        if not data:
            return data
            
        try:
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            
            # 1. High Frequency Enhancement
            samples = self._apply_treble_boost(samples)
            
            # 2. Compression Logic
            current_rms = np.sqrt(np.mean(samples**2))
            if current_rms > self.threshold:
                # Standard Dynamic Range Compression formula
                # Gain Reduction = (Threshold / CurrentRMS) ^ (1 - 1/Ratio)
                reduction_factor = (self.threshold / current_rms) ** (1 - 1.0 / self.ratio)
                samples *= reduction_factor
            
            # 3. Makeup Gain (Ensure Asha is always comfortably loud)
            samples *= self.makeup_gain
            
            return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
        except Exception:
            logger.exception("Outbound audio polishing failed")
            return data

def exotel_to_pcm(data: bytes) -> bytes:
    """Exotel raw bytes are already 8k 16-bit PCM."""
    return data

def pcm_to_exotel(data: bytes) -> bytes:
    """Ensure outgoing data is in correct PCM format."""
    return data
