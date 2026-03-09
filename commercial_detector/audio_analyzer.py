"""Audio analysis for commercial detection — pure NumPy, SBC-friendly.

Computes RMS volume, silence detection, and volume shift tracking from
raw PCM int16 audio chunks. Designed for real-time frame-by-frame processing
with minimal memory footprint.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

from commercial_detector.config import AudioConfig
from commercial_detector.models import AudioSignals

logger = logging.getLogger(__name__)


class AudioAnalyzer:
    """Analyzes audio chunks to produce signals for the detection engine.

    Maintains a small ring buffer of recent RMS values for smoothing and
    tracks the previous smoothed RMS for volume shift detection.
    """

    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self._rms_buffer: deque[float] = deque(
            maxlen=config.rms_smoothing_windows,
        )
        self._previous_rms: float | None = None

    def analyze(self, chunk: np.ndarray, timestamp: float) -> AudioSignals:
        """Analyze a single audio chunk and return detection signals.

        Args:
            chunk: PCM audio samples as int16 numpy array.
            timestamp: Seconds since capture start (monotonic).

        Returns:
            AudioSignals with RMS, silence flag, and volume shift.
        """
        # Edge case: empty or zero-length chunk
        if chunk is None or chunk.size == 0:
            logger.debug("Empty audio chunk at t=%.3f", timestamp)
            return AudioSignals(timestamp=timestamp, rms=0.0, is_silent=True, volume_shift=0.0)

        # Compute RMS: convert int16 to float, normalize to 0.0–1.0 range
        samples = chunk.astype(np.float64) / 32768.0
        rms = float(np.sqrt(np.mean(samples ** 2)))

        # Smooth RMS via ring buffer
        self._rms_buffer.append(rms)
        smoothed_rms = sum(self._rms_buffer) / len(self._rms_buffer)

        # Silence detection: convert to dB scale
        rms_db = 20.0 * np.log10(smoothed_rms + 1e-10)
        is_silent = bool(rms_db < self._config.silence_threshold_db)

        # Volume shift detection
        if self._previous_rms is None:
            volume_shift = 0.0
        else:
            volume_shift = smoothed_rms - self._previous_rms

        self._previous_rms = smoothed_rms

        logger.debug(
            "Audio t=%.3f rms=%.4f smoothed=%.4f dB=%.1f silent=%s shift=%.4f",
            timestamp, rms, smoothed_rms, rms_db, is_silent, volume_shift,
        )

        return AudioSignals(
            timestamp=timestamp,
            rms=smoothed_rms,
            is_silent=is_silent,
            volume_shift=volume_shift,
        )

    def reset(self) -> None:
        """Clear internal state — call when switching sources."""
        self._rms_buffer.clear()
        self._previous_rms = None
        logger.debug("AudioAnalyzer state reset")
