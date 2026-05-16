import logging
import numpy as np
from scipy.signal import lfilter, lfilter_zi

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
        
        # [OPT-01] Pre-compute scipy IIR filter coefficients for HPF
        # First-order high-pass: y[n] = alpha*(y[n-1] + x[n] - x[n-1])
        # Cutoff approx 150-200Hz for 8kHz sample rate
        self.hpf_alpha = 0.85
        self._hpf_b = np.array([self.hpf_alpha, -self.hpf_alpha], dtype=np.float64)
        self._hpf_a = np.array([1.0, -self.hpf_alpha], dtype=np.float64)
        self._hpf_zi = lfilter_zi(self._hpf_b, self._hpf_a) * 0.0  # init state
        
        # Noise Gate / AGC state
        self.noise_floor = 150.0
        self.alpha_noise = 0.02
        self.target_rms = 4500.0
        self.max_gain = 4.0
        self.gate_threshold_mult = 2.0
        
    def _apply_hpf(self, samples: np.ndarray) -> np.ndarray:
        """[OPT-01] Vectorized first-order high-pass filter via scipy.signal.lfilter.
        Replaces Python for-loop — runs at C speed, ~10x faster."""
        out, self._hpf_zi = lfilter(self._hpf_b, self._hpf_a, samples.astype(np.float64), zi=self._hpf_zi)
        return out.astype(np.float32)

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
        
        # [OPT-01] Pre-compute scipy IIR coefficients for High-Shelf treble boost
        # High-shelf: boosts frequencies above ~3kHz for telephone clarity
        shelf_alpha = 0.4
        self._shelf_b = np.array([1.0 - shelf_alpha + shelf_alpha * 1.8,
                                   -(1.0 - shelf_alpha)], dtype=np.float64)
        self._shelf_a = np.array([1.0, -shelf_alpha], dtype=np.float64)
        self._shelf_zi = lfilter_zi(self._shelf_b, self._shelf_a) * 0.0

    def _apply_treble_boost(self, samples: np.ndarray) -> np.ndarray:
        """[OPT-01] Vectorized high-shelf boost via scipy.signal.lfilter."""
        out, self._shelf_zi = lfilter(self._shelf_b, self._shelf_a, samples.astype(np.float64), zi=self._shelf_zi)
        return out.astype(np.float32)

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

try:
    import audioop
    _HAS_AUDIOOP = True
except ImportError:
    _HAS_AUDIOOP = False
    
    def _init_mulaw_tables():
        ulaw_to_lin = np.zeros(256, dtype=np.int16)
        lin_to_ulaw = np.zeros(65536, dtype=np.uint8)
        
        for i in range(256):
            val = ~i & 0xFF
            sign = (val & 0x80) >> 7
            exponent = (val & 0x70) >> 4
            mantissa = val & 0x0F
            sample = ((mantissa << 3) + 132) << exponent
            sample -= 132
            if sign != 0:
                sample = -sample
            ulaw_to_lin[i] = np.clip(sample, -32768, 32767)
            
        for i in range(65536):
            sample = np.int16(i)
            sign = 0x80 if sample < 0 else 0x00
            mag = abs(int(sample))
            mag = min(mag, 32635)
            mag += 132
            
            exponent = 7
            for exp in range(7, 0, -1):
                if (mag & (0x100 << exp)) != 0:
                    exponent = exp
                    break
            else:
                exponent = 0
                
            mantissa = (mag >> (exponent + 3)) & 0x0F
            val = sign | (exponent << 4) | mantissa
            lin_to_ulaw[i] = ~val & 0xFF
            
        return ulaw_to_lin, lin_to_ulaw

    _ULAW_TO_LIN, _LIN_TO_ULAW = _init_mulaw_tables()

def exotel_to_pcm(data: bytes) -> bytes:
    """Convert Exotel 8kHz u-law (PCMU) to 16-bit linear PCM."""
    if not data:
        return data
    if _HAS_AUDIOOP:
        try:
            return audioop.ulaw2lin(data, 2)
        except Exception:
            pass
    # Numpy fallback
    ulaw_indices = np.frombuffer(data, dtype=np.uint8)
    return _ULAW_TO_LIN[ulaw_indices].tobytes()

def pcm_to_exotel(data: bytes) -> bytes:
    """Convert 16-bit linear PCM to Exotel 8kHz u-law (PCMU)."""
    if not data:
        return data
    if _HAS_AUDIOOP:
        try:
            return audioop.lin2ulaw(data, 2)
        except Exception:
            pass
    # Numpy fallback
    pcm_samples = np.frombuffer(data, dtype=np.int16).view(np.uint16)
    return _LIN_TO_ULAW[pcm_samples].tobytes()
