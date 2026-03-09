"""Shared data models for the commercial detection pipeline.

All components communicate through these dataclasses. Keeping them in one place
ensures signal source, engine, and publisher agree on data shapes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------

class DetectionState(Enum):
    """Current state of the detection engine."""
    PROGRAM = "program"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# FFmpeg filter signals
# ---------------------------------------------------------------------------

class SignalType(Enum):
    """Types of detection signals emitted by FFmpeg filter parsing or transcript analysis."""
    SILENCE_START = "silence_start"
    SILENCE_END = "silence_end"
    BLACK_START = "black_start"
    BLACK_END = "black_end"
    SCENE_CHANGE = "scene_change"
    LOUDNESS_SHIFT = "loudness_shift"
    TRANSCRIPT_COMMERCIAL = "transcript_commercial"
    TRANSCRIPT_PROGRAM = "transcript_program"


@dataclass(frozen=True, slots=True)
class DetectionSignal:
    """A timestamped detection event from FFmpeg's filter output.

    Produced by SignalSource, consumed by DetectionEngine.

    Attributes:
        timestamp: Presentation timestamp (seconds) from FFmpeg.
        signal_type: What kind of event this is.
        value: Contextual value — silence duration for SILENCE_END,
               scene change score for SCENE_CHANGE, black duration
               for BLACK_END, 0.0 otherwise.
    """
    timestamp: float
    signal_type: SignalType
    value: float = 0.0


# ---------------------------------------------------------------------------
# Engine output
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StateTransition:
    """A state change event emitted by the detection engine.

    Published to MQTT when the engine determines a transition has occurred.

    Attributes:
        timestamp: When the transition was detected.
        from_state: Previous state.
        to_state: New state.
        confidence: Engine's confidence in this transition (0.0 to 1.0).
        signals: Summary of the signals that triggered the transition.
    """
    timestamp: float
    from_state: DetectionState
    to_state: DetectionState
    confidence: float = 0.0
    signals: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for MQTT / JSON publishing."""
        return {
            "timestamp": self.timestamp,
            "from": self.from_state.value,
            "to": self.to_state.value,
            "confidence": round(self.confidence, 3),
            "signals": self.signals,
        }


@dataclass(slots=True)
class EngineState:
    """Internal mutable state of the detection engine.

    Not published directly — used internally to track scoring and timing.

    Attributes:
        current_state: What the engine currently believes.
        state_entered_at: PTS timestamp when current state was entered.
        last_signal_time: PTS of the most recent signal processed.
        commercial_score: Continuous score — positive = commercial-like, negative = program-like.
        last_score_update: PTS when score was last updated (for decay calculation).
        scene_change_times: Recent scene change timestamps for rate tracking.
        loudness_baseline: EMA of short-term loudness (LUFS) for shift detection.
        last_silence_time: PTS of the most recent SILENCE_END signal.
        last_black_time: PTS of the most recent BLACK_START signal.
    """
    current_state: DetectionState = DetectionState.UNKNOWN
    state_entered_at: float = 0.0
    last_signal_time: float = 0.0
    commercial_score: float = 0.0
    last_score_update: float = 0.0
    scene_change_times: list = field(default_factory=list)
    loudness_baseline: float = -24.0
    last_silence_time: float = -1.0
    last_black_time: float = -1.0
