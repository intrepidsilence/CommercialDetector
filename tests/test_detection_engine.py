"""Tests for DetectionEngine — continuous multi-signal scoring.

Tests verify that the scoring engine accumulates weighted signal scores,
applies time-based decay, detects silence+black coincidence, and transitions
between COMMERCIAL and PROGRAM states at the correct thresholds.
"""

from __future__ import annotations

import pytest

from commercial_detector.config import EngineConfig
from commercial_detector.detection_engine import DetectionEngine
from commercial_detector.models import (
    DetectionSignal,
    DetectionState,
    SignalType,
    StateTransition,
)


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------

def _silence_end(timestamp: float, duration: float = 0.3) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.SILENCE_END, value=duration)


def _silence_start(timestamp: float) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.SILENCE_START)


def _black_start(timestamp: float, duration: float = 0.05) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.BLACK_START, value=duration)


def _scene_change(timestamp: float, score: float = 42.0) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.SCENE_CHANGE, value=score)


def _loudness_shift(timestamp: float, delta: float) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.LOUDNESS_SHIFT, value=delta)


def _transcript_commercial(timestamp: float, score: float = 3.0) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.TRANSCRIPT_COMMERCIAL, value=score)


def _transcript_program(timestamp: float, score: float = 3.0) -> DetectionSignal:
    return DetectionSignal(timestamp=timestamp, signal_type=SignalType.TRANSCRIPT_PROGRAM, value=score)


# ---------------------------------------------------------------------------
# Fixtures — test configs with fast debounce and no hold-off
# ---------------------------------------------------------------------------

@pytest.fixture
def engine() -> DetectionEngine:
    """Engine with no debounce, no hold-off, and explicit scoring parameters.

    Uses fixed values so tests aren't fragile to default tuning changes.
    """
    config = EngineConfig(
        initial_hold_off=0.0,
        min_commercial_duration=0.0,
        min_program_duration=0.0,
        score_decay_rate=0.5,
        max_score=20.0,
    )
    return DetectionEngine(config)


@pytest.fixture
def debounced_engine() -> DetectionEngine:
    """Engine with realistic debounce for safety-gate testing."""
    config = EngineConfig(
        initial_hold_off=0.0,
        min_commercial_duration=15.0,
        min_program_duration=60.0,
        score_decay_rate=0.5,
        max_score=20.0,
    )
    return DetectionEngine(config)


# ===========================================================================
# Initial state
# ===========================================================================

class TestInitialState:

    def test_starts_unknown(self, engine: DetectionEngine):
        assert engine.current_state is DetectionState.UNKNOWN

    def test_single_scene_change_no_transition(self, engine: DetectionEngine):
        """A single scene change (weight 0.5) is far below threshold 8.0."""
        result = engine.process(_scene_change(1.0))
        assert result is None

    def test_single_silence_no_transition(self, engine: DetectionEngine):
        """A single silence gap (weight 5.0) is below threshold 8.0."""
        result = engine.process(_silence_end(5.0))
        assert result is None
        assert engine.current_state is DetectionState.UNKNOWN


# ===========================================================================
# Transition to COMMERCIAL via scoring
# ===========================================================================

class TestTransitionToCommercial:

    def test_coincident_silence_black_triggers_commercial(self, engine: DetectionEngine):
        """Silence + black within 2s = (5 + 4) * 1.5 = 13.5 > 8.0 threshold."""
        engine.process(_silence_end(5.0))
        result = engine.process(_black_start(5.5))

        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL
        assert result.from_state is DetectionState.UNKNOWN

    def test_two_silence_gaps_trigger_commercial(self, engine: DetectionEngine):
        """Two quick silence gaps: 5.0 + 5.0 = 10.0 > 8.0 (minimal decay)."""
        engine.process(_silence_end(5.0))
        # 1 second later: decay = 0.5 * 1 = 0.5, score before = 5.0 - 0.5 = 4.5
        # After second silence: 4.5 + 5.0 = 9.5 > 8.0
        result = engine.process(_silence_end(6.0))
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_silence_gaps_too_far_apart_no_transition(self, engine: DetectionEngine):
        """Silence gaps 20s apart: 5.0 decays by 0.5*20=10.0 -> 0, then +5.0 = 5.0 < 8.0."""
        engine.process(_silence_end(5.0))
        result = engine.process(_silence_end(25.0))
        assert result is None
        assert engine.current_state is DetectionState.UNKNOWN

    def test_multiple_signals_accumulate(self, engine: DetectionEngine):
        """Silence + loudness up at same time: 5.0 + 4.0 = 9.0 > 8.0."""
        engine.process(_silence_end(5.0))
        result = engine.process(_loudness_shift(5.0, delta=5.0))
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_black_frames_alone_accumulate(self, engine: DetectionEngine):
        """Two black frames at same time: 4.0 + 4.0 = 8.0 >= threshold."""
        engine.process(_black_start(5.0))
        result = engine.process(_black_start(5.0))  # same timestamp, no decay
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_transcript_commercial_helps_reach_threshold(self, engine: DetectionEngine):
        """Silence (5.0) + transcript_commercial (3.0) = 8.0 >= threshold."""
        engine.process(_silence_end(5.0))
        result = engine.process(_transcript_commercial(5.0))  # same timestamp
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_sparse_scene_changes_cannot_trigger(self, engine: DetectionEngine):
        """Sparse scene changes (below rate threshold) can't reach commercial score.
        At 10s intervals the rate is 6/min, below the 12/min threshold —
        no rate boost, and 0.5 weight decays fully between events."""
        for t in range(0, 120, 10):
            result = engine.process(_scene_change(float(t), score=42.0))
        assert engine.current_state is DetectionState.UNKNOWN

    def test_already_commercial_no_duplicate_transition(self, engine: DetectionEngine):
        """Once in COMMERCIAL, more signals don't create new transitions."""
        engine.process(_silence_end(5.0))
        t1 = engine.process(_black_start(5.5))  # -> COMMERCIAL
        assert t1 is not None

        t2 = engine.process(_silence_end(10.0))
        assert t2 is None  # already COMMERCIAL


class TestCoincidenceDetection:
    """Silence + black within the coincidence window get a 1.5x multiplier."""

    def test_silence_then_black_within_window(self, engine: DetectionEngine):
        """Silence at t=5, black at t=6.5 (1.5s gap, within 2s window)."""
        engine.process(_silence_end(5.0))
        result = engine.process(_black_start(6.5))
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_black_then_silence_within_window(self, engine: DetectionEngine):
        """Black at t=5, silence at t=6 (1s gap, within 2s window)."""
        engine.process(_black_start(5.0))
        result = engine.process(_silence_end(6.0))
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_no_coincidence_outside_window(self, engine: DetectionEngine):
        """Silence at t=5, black at t=10 (5s gap, outside 2s window)."""
        engine.process(_silence_end(5.0))
        # 5s decay: 5.0 - 0.5*5 = 2.5, then + 4.0 = 6.5 < 8.0
        result = engine.process(_black_start(10.0))
        assert result is None

    def test_coincidence_not_applied_to_unrelated_signals(self, engine: DetectionEngine):
        """Two silence gaps should NOT get the coincidence multiplier."""
        engine.process(_silence_end(5.0))
        # 1s later: 5.0 - 0.5 = 4.5, + 5.0 = 9.5 (no multiplier, just additive)
        result = engine.process(_silence_end(6.0))
        assert result is not None
        # Score should be ~9.5, not ~13.5 (what coincidence would give)
        assert result.signals["commercial_score"] < 12.0


# ===========================================================================
# Transition to PROGRAM via score decay and negative signals
# ===========================================================================

class TestTransitionToProgram:

    def test_score_decays_to_program(self, engine: DetectionEngine):
        """Without reinforcing signals, score decays below program threshold."""
        # Enter COMMERCIAL
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Score is ~13.5. Needs to decay to -3.0.
        # That's 16.5 points at 0.5/s = 33s to zero, then 6s more = ~39s total
        # But decay stops at 0 and needs negative signals to go below.
        # Actually, decay toward zero means score hits 0 at ~27s, then stays at 0.
        # We need negative signals to push below -3.0.
        # Scene change at t=50: 45s later, score decayed to 0, +0.5 = 0.5
        result = engine.process(_scene_change(50.0))
        assert result is None  # score ~0.5, not below -3.0

        # Loudness drop pushes below threshold: 0.5 - 3.0 = -2.5, not enough
        result = engine.process(_loudness_shift(50.5, delta=-5.0))
        assert result is None  # -2.5, need <= -3.0

        # Transcript program: -2.5 - 4.0 = -6.5 < -3.0
        result = engine.process(_transcript_program(51.0))
        assert result is not None
        assert result.to_state is DetectionState.PROGRAM

    def test_transcript_program_exits_commercial(self, engine: DetectionEngine):
        """TRANSCRIPT_PROGRAM (-4.0) after score has decayed can exit COMMERCIAL."""
        # Enter COMMERCIAL with minimum score
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Wait for score to decay to ~0, then negative signals push below threshold
        # Score ~13.5 at t=5.5, decay at 0.5/s. At t=33: 13.5 - 13.75 = ~0
        # transcript_program at t=34: 0 - 4.0 = -4.0 < -3.0
        result = engine.process(_transcript_program(34.0))
        assert result is not None
        assert result.to_state is DetectionState.PROGRAM

    def test_loudness_drop_helps_exit(self, engine: DetectionEngine):
        """Loudness drop (-3.0) + transcript program (-4.0) for fast exit."""
        # Enter COMMERCIAL
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Wait for decay, then negative signals
        # At t=30: score ~13.5 - 12.25 = ~1.25
        engine.process(_loudness_shift(30.0, delta=-5.0))  # +(-3.0) = -1.75
        result = engine.process(_transcript_program(30.5))  # -1.75 - 4.0 = -5.75
        assert result is not None
        assert result.to_state is DetectionState.PROGRAM

    def test_reinforcing_signals_prevent_decay_exit(self, engine: DetectionEngine):
        """Continued commercial signals keep score above program threshold."""
        # Enter COMMERCIAL
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Keep feeding silence gaps every 15s to maintain score
        for t in [20.0, 35.0, 50.0]:
            engine.process(_silence_end(t))

        # Score should still be positive after reinforcement
        assert engine.current_state is DetectionState.COMMERCIAL


# ===========================================================================
# Score decay
# ===========================================================================

class TestScoreDecay:

    def test_positive_score_decays_toward_zero(self, engine: DetectionEngine):
        """Score decays at 0.5 per second toward zero."""
        engine.process(_silence_end(5.0))  # score = 5.0

        # 8 seconds later: decay = 0.5 * 8 = 4.0, score = 5.0 - 4.0 = 1.0
        # Adding scene change: 1.0 + 0.5 = 1.5
        engine.process(_scene_change(13.0))
        score = engine._state.commercial_score
        assert 1.0 < score < 2.5  # approximately 1.5

    def test_score_does_not_decay_below_zero(self, engine: DetectionEngine):
        """Decay stops at zero — doesn't push positive scores negative."""
        engine.process(_silence_end(5.0))  # score = 5.0
        # 30 seconds later: decay = 0.5 * 30 = 15.0, clamped at 0
        engine.process(_scene_change(35.0))  # score = 0 + 0.5 = 0.5
        assert engine._state.commercial_score >= 0.0

    def test_negative_score_decays_toward_zero(self, engine: DetectionEngine):
        """Negative scores also decay toward zero."""
        engine.process(_transcript_program(5.0))  # score = -4.0
        # 5 seconds later: decay = 0.5*5 = 2.5, score = -4.0 + 2.5 = -1.5
        engine.process(_scene_change(10.0))  # -1.5 + 0.5 = -1.0
        score = engine._state.commercial_score
        assert -2.0 < score < 0.0


# ===========================================================================
# Initial hold-off
# ===========================================================================

class TestInitialHoldOff:

    def test_signals_during_hold_off_ignored(self):
        config = EngineConfig(
            initial_hold_off=30.0,
            min_commercial_duration=0.0,
            min_program_duration=0.0,
        )
        engine = DetectionEngine(config)

        # Strong coincident signals during hold-off
        engine.process(_silence_end(5.0))
        result = engine.process(_black_start(5.5))
        assert result is None
        assert engine.current_state is DetectionState.UNKNOWN

    def test_signals_after_hold_off_work(self):
        config = EngineConfig(
            initial_hold_off=30.0,
            min_commercial_duration=0.0,
            min_program_duration=0.0,
        )
        engine = DetectionEngine(config)

        engine.process(_silence_end(35.0))
        result = engine.process(_black_start(35.5))
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL


# ===========================================================================
# Debounce
# ===========================================================================

class TestDebounce:

    def test_commercial_debounce_prevents_rapid_exit(self, debounced_engine: DetectionEngine):
        """min_commercial_duration=15s prevents exiting before 15s."""
        engine = debounced_engine

        # Enter COMMERCIAL at t=5.5
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Try to exit at t=10 with strong negative signal — blocked by debounce
        engine.process(_transcript_program(10.0))
        engine.process(_loudness_shift(10.5, delta=-5.0))
        assert engine.current_state is DetectionState.COMMERCIAL

    def test_commercial_debounce_allows_after_duration(self, debounced_engine: DetectionEngine):
        """After 15s in COMMERCIAL, negative signals can trigger exit."""
        engine = debounced_engine

        # Enter COMMERCIAL at t=5.5
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # At t=25 (19.5s in COMMERCIAL > 15s min), score has decayed
        # Score ~13.5 - 0.5*19.5 = ~3.75, then -4.0 = -0.25, then -3.0 = -3.25
        engine.process(_transcript_program(25.0))
        result = engine.process(_loudness_shift(25.5, delta=-5.0))
        assert result is not None
        assert result.to_state is DetectionState.PROGRAM

    def test_program_debounce_prevents_rapid_reentry(self, debounced_engine: DetectionEngine):
        """min_program_duration=60s prevents re-entering COMMERCIAL too quickly."""
        engine = debounced_engine

        # Enter COMMERCIAL then exit to PROGRAM
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Wait for decay + negative signals to exit
        engine.process(_transcript_program(25.0))
        engine.process(_loudness_shift(25.5, delta=-5.0))
        assert engine.current_state is DetectionState.PROGRAM

        # Try to re-enter COMMERCIAL immediately — blocked by 60s program debounce
        engine.process(_silence_end(30.0))
        result = engine.process(_black_start(30.5))
        assert result is None
        assert engine.current_state is DetectionState.PROGRAM


# ===========================================================================
# Loudness shift signals
# ===========================================================================

class TestLoudnessSignals:

    def test_loudness_up_adds_positive_score(self, engine: DetectionEngine):
        """Loudness increase adds loudness_up_weight (4.0)."""
        engine.process(_loudness_shift(5.0, delta=5.0))
        assert engine._state.commercial_score == pytest.approx(4.0)

    def test_loudness_down_adds_negative_score(self, engine: DetectionEngine):
        """Loudness decrease adds loudness_down_weight (-3.0)."""
        engine.process(_loudness_shift(5.0, delta=-5.0))
        assert engine._state.commercial_score == pytest.approx(-3.0)

    def test_loudness_up_contributes_to_commercial(self, engine: DetectionEngine):
        """Silence (5.0) + loudness up (4.0) = 9.0 > 8.0."""
        engine.process(_silence_end(5.0))
        result = engine.process(_loudness_shift(5.0, delta=5.0))
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL


# ===========================================================================
# Scene change rate boost
# ===========================================================================

class TestSceneChangeRate:

    def test_high_scene_rate_adds_boost(self, engine: DetectionEngine):
        """Scene changes at >12/min rate should add scene_rate_boost (2.0)."""
        # 7 scene changes in 10 seconds = 42/min (well above 12/min threshold)
        for i in range(7):
            engine.process(_scene_change(5.0 + i * 1.5))

        # Score should include individual weights (7*0.5=3.5) plus rate boosts
        assert engine._state.commercial_score > 3.5

    def test_low_scene_rate_no_boost(self, engine: DetectionEngine):
        """Scene changes at <12/min rate should not get boosted."""
        # 2 scene changes in 30 seconds = 4/min (below 12/min threshold)
        engine.process(_scene_change(5.0))
        engine.process(_scene_change(20.0))
        # Score: first decays from 0.5 by 7.5 (clamped to 0), then +0.5 = 0.5
        # No rate boost since 4/min < 12/min
        assert engine._state.commercial_score <= 1.0


# ===========================================================================
# Full commercial break pattern
# ===========================================================================

class TestFullCommercialBreakPattern:
    """Simulate a realistic commercial break with multiple signal types."""

    def test_realistic_break_entry_and_exit(self):
        config = EngineConfig(
            initial_hold_off=0.0,
            min_commercial_duration=0.0,
            min_program_duration=0.0,
        )
        engine = DetectionEngine(config)
        transitions: list[StateTransition] = []

        # Program content — only sparse scene changes for 5 minutes
        for t in range(0, 300, 30):
            r = engine.process(_scene_change(float(t)))
            if r:
                transitions.append(r)

        assert engine.current_state is DetectionState.UNKNOWN

        # Commercial break starts: coincident silence+black
        r = engine.process(_silence_end(347.0))
        if r:
            transitions.append(r)
        r = engine.process(_black_start(347.5))
        if r:
            transitions.append(r)

        # Should have entered COMMERCIAL after the coincident pair
        commercial_transitions = [t for t in transitions if t.to_state is DetectionState.COMMERCIAL]
        assert len(commercial_transitions) >= 1
        assert commercial_transitions[0].timestamp <= 348.0  # fast entry

        # More commercial signals (ad boundaries)
        for t in [362.0, 377.0, 392.0, 407.0]:
            engine.process(_silence_end(t))
            engine.process(_black_start(t + 0.5))

        assert engine.current_state is DetectionState.COMMERCIAL

        # Program resumes — score decays over time, then negative signals push below threshold.
        # Score is ~37 at t=407.5. At 0.5/s decay, reaches ~0 at t=482.
        transitions.clear()
        r = engine.process(_transcript_program(490.0))
        if r:
            transitions.append(r)
        r = engine.process(_loudness_shift(490.5, delta=-5.0))
        if r:
            transitions.append(r)

        program_transitions = [t for t in transitions if t.to_state is DetectionState.PROGRAM]
        assert len(program_transitions) >= 1


# ===========================================================================
# State transition metadata
# ===========================================================================

class TestStateTransitionEvent:

    def test_transition_has_scoring_metadata(self, engine: DetectionEngine):
        engine.process(_silence_end(5.0))
        result = engine.process(_black_start(5.5))

        assert result is not None
        assert result.from_state is DetectionState.UNKNOWN
        assert result.to_state is DetectionState.COMMERCIAL
        assert 0.0 <= result.confidence <= 1.0
        assert "commercial_score" in result.signals
        assert "trigger_signal" in result.signals
        assert "score_delta" in result.signals

    def test_to_dict_serialization(self, engine: DetectionEngine):
        engine.process(_silence_end(5.0))
        result = engine.process(_black_start(5.5))

        assert result is not None
        d = result.to_dict()
        assert d["from"] == "unknown"
        assert d["to"] == "commercial"
        assert isinstance(d["confidence"], float)
        assert isinstance(d["signals"], dict)


# ===========================================================================
# Reset
# ===========================================================================

class TestReset:

    def test_reset_clears_state(self, engine: DetectionEngine):
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        engine.reset()
        assert engine.current_state is DetectionState.UNKNOWN
        assert engine._state.commercial_score == 0.0
        assert engine._state.scene_change_times == []
        assert engine._state.last_silence_time == -1.0
        assert engine._state.last_black_time == -1.0
        assert engine._state.last_signal_time == 0.0
