"""Shared test fixtures — signal generators and config helpers."""

from __future__ import annotations

import pytest

from commercial_detector.config import AppConfig
from commercial_detector.models import DetectionSignal, SignalType


@pytest.fixture
def default_config() -> AppConfig:
    """Return a default AppConfig for testing."""
    return AppConfig()


# ---------------------------------------------------------------------------
# Signal generators (used by multiple test modules)
# ---------------------------------------------------------------------------

def make_silence_end(
    timestamp: float = 0.0,
    duration: float = 0.3,
) -> DetectionSignal:
    """Create a silence end signal."""
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.SILENCE_END,
        value=duration,
    )


def make_silence_start(timestamp: float = 0.0) -> DetectionSignal:
    """Create a silence start signal."""
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.SILENCE_START,
    )


def make_black_start(
    timestamp: float = 0.0,
    duration: float = 0.05,
) -> DetectionSignal:
    """Create a black frame detection signal."""
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.BLACK_START,
        value=duration,
    )


def make_scene_change(
    timestamp: float = 0.0,
    score: float = 42.0,
) -> DetectionSignal:
    """Create a scene change signal."""
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.SCENE_CHANGE,
        value=score,
    )


def make_loudness_shift(
    timestamp: float = 0.0,
    delta: float = 5.0,
) -> DetectionSignal:
    """Create a loudness shift signal (positive = louder, negative = quieter)."""
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.LOUDNESS_SHIFT,
        value=delta,
    )
