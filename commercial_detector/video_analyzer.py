"""Video analysis — detect black frames and scene changes via OpenCV.

Processes individual RGB frames and produces VideoSignals for the detection
engine. Keeps minimal state (one previous grayscale frame) for scene-change
computation.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from commercial_detector.config import VideoConfig
from commercial_detector.models import VideoSignals

logger = logging.getLogger(__name__)


class VideoAnalyzer:
    """Analyzes individual video frames for commercial-break indicators.

    Stateful: retains the previous grayscale frame for scene-change scoring.
    Call reset() when switching sources or after a pipeline restart.
    """

    def __init__(self, config: VideoConfig) -> None:
        self._config = config
        self._prev_gray: Optional[np.ndarray] = None

    def analyze(self, frame: Optional[np.ndarray], timestamp: float) -> VideoSignals:
        """Analyze a single RGB frame and return video signals.

        Args:
            frame: RGB uint8 array of shape (H, W, 3), or None.
            timestamp: Seconds since capture start.

        Returns:
            VideoSignals with black-frame flag, brightness, and scene-change score.
        """
        if frame is None or frame.size == 0:
            logger.debug("Empty or None frame at t=%.3f", timestamp)
            return VideoSignals(timestamp=timestamp)

        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        brightness = float(np.mean(gray))

        is_black = (
            brightness < self._config.black_frame_threshold
            and float(np.max(gray)) < self._config.black_frame_max_bright_pixel
        )

        scene_change_score = 0.0
        if self._prev_gray is not None:
            if self._prev_gray.shape != gray.shape:
                logger.debug(
                    "Frame size changed (%s -> %s), resetting previous frame",
                    self._prev_gray.shape,
                    gray.shape,
                )
                self._prev_gray = None
            else:
                diff = cv2.absdiff(gray, self._prev_gray)
                scene_change_score = float(np.mean(diff)) / 255.0

        self._prev_gray = gray

        logger.debug(
            "t=%.3f brightness=%.1f black=%s scene_change=%.4f",
            timestamp,
            brightness,
            is_black,
            scene_change_score,
        )

        return VideoSignals(
            timestamp=timestamp,
            is_black_frame=is_black,
            brightness=brightness,
            scene_change_score=scene_change_score,
        )

    def reset(self) -> None:
        """Clear previous frame state."""
        self._prev_gray = None
