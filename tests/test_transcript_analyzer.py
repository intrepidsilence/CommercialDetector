"""Tests for transcript analysis — keyword scoring and engine integration.

All tests are pure Python (no faster-whisper dependency). The KeywordScorer
is tested directly, and engine integration tests verify that the scoring
engine correctly handles TRANSCRIPT_COMMERCIAL and TRANSCRIPT_PROGRAM signals.
"""

from __future__ import annotations

import pytest

from commercial_detector.config import EngineConfig, TranscriptConfig
from commercial_detector.detection_engine import DetectionEngine
from commercial_detector.models import (
    DetectionSignal,
    DetectionState,
    SignalType,
)
from commercial_detector.transcript_analyzer import KeywordScorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transcript_commercial(timestamp: float, score: float = 3.0) -> DetectionSignal:
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.TRANSCRIPT_COMMERCIAL,
        value=score,
    )


def _transcript_program(timestamp: float, score: float = 3.0) -> DetectionSignal:
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.TRANSCRIPT_PROGRAM,
        value=score,
    )


def _silence_end(timestamp: float, duration: float = 0.3) -> DetectionSignal:
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.SILENCE_END,
        value=duration,
    )


def _black_start(timestamp: float, duration: float = 0.05) -> DetectionSignal:
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.BLACK_START,
        value=duration,
    )


def _scene_change(timestamp: float, score: float = 42.0) -> DetectionSignal:
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.SCENE_CHANGE,
        value=score,
    )


def _loudness_shift(timestamp: float, delta: float) -> DetectionSignal:
    return DetectionSignal(
        timestamp=timestamp,
        signal_type=SignalType.LOUDNESS_SHIFT,
        value=delta,
    )


@pytest.fixture
def scorer() -> KeywordScorer:
    return KeywordScorer(TranscriptConfig())


@pytest.fixture
def engine() -> DetectionEngine:
    """Engine with no debounce, no hold-off for unit testing.

    Uses fixed scoring values so tests aren't fragile to default tuning changes.
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
    """Engine with realistic debounce to test safety gates."""
    config = EngineConfig(
        initial_hold_off=0.0,
        min_commercial_duration=15.0,
        min_program_duration=0.0,
        score_decay_rate=0.5,
        max_score=20.0,
    )
    return DetectionEngine(config)


# ===========================================================================
# KeywordScorer tests
# ===========================================================================

class TestKeywordScorerCommercial:
    """Commercial phrases should produce TRANSCRIPT_COMMERCIAL."""

    def test_call_now(self, scorer: KeywordScorer):
        signal_type, score = scorer.score("Call now to get your free trial!")
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0

    def test_phone_number(self, scorer: KeywordScorer):
        signal_type, score = scorer.score("Dial 1-800-555-0123 for details")
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0

    def test_price(self, scorer: KeywordScorer):
        signal_type, score = scorer.score("Only $19.99 plus free shipping today")
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0

    def test_side_effects(self, scorer: KeywordScorer):
        signal_type, score = scorer.score(
            "Ask your doctor about Nexium. Side effects may include nausea."
        )
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0

    def test_multiple_indicators(self, scorer: KeywordScorer):
        """Multiple commercial indicators should produce a higher score."""
        signal_type, score = scorer.score(
            "Call now! Only $9.99 with free shipping! "
            "Visit example dot com or dial 1-800-555-1234. Act now!"
        )
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 6.0

    def test_website(self, scorer: KeywordScorer):
        signal_type, score = scorer.score(
            "Visit www.example.com for more information. Order now!"
        )
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0

    def test_results_may_vary(self, scorer: KeywordScorer):
        signal_type, score = scorer.score(
            "Results may vary. Not available in all areas."
        )
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0

    def test_as_seen_on_tv(self, scorer: KeywordScorer):
        signal_type, score = scorer.score("As seen on TV! Money-back guarantee!")
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 2.0


class TestKeywordScorerProgram:
    """Long dialog without commercial indicators -> TRANSCRIPT_PROGRAM."""

    def test_long_dialog(self, scorer: KeywordScorer):
        """25+ words of regular dialog should classify as program."""
        dialog = (
            "The detective walked into the room and looked around carefully. "
            "Something was clearly wrong here and she could feel it in her bones. "
            "The witness had described a tall man leaving through the back door."
        )
        signal_type, score = scorer.score(dialog)
        assert signal_type is SignalType.TRANSCRIPT_PROGRAM
        assert score >= 2.0

    def test_short_dialog_inconclusive(self, scorer: KeywordScorer):
        """Short dialog (under threshold) should be inconclusive."""
        signal_type, score = scorer.score("I don't think that's going to work.")
        assert signal_type is None

    def test_long_dialog_with_commercial_words_stays_commercial(self, scorer: KeywordScorer):
        """If commercial indicators are present, even long text is commercial."""
        text = (
            "Are you tired of the same old thing day after day? "
            "Well now there is a better way to get things done around the house. "
            "Call now and we will send you two for the price of one! "
            "That is right, call 1-800-555-9999 and get free shipping on your order. "
            "This offer is only available for a limited time so act now. "
            "Don't miss out on this incredible deal."
        )
        signal_type, score = scorer.score(text)
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL


class TestKeywordScorerEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_string(self, scorer: KeywordScorer):
        signal_type, score = scorer.score("")
        assert signal_type is None
        assert score == 0.0

    def test_whitespace_only(self, scorer: KeywordScorer):
        signal_type, score = scorer.score("   \n\t  ")
        assert signal_type is None
        assert score == 0.0

    def test_case_insensitive(self, scorer: KeywordScorer):
        """Commercial patterns should match regardless of case."""
        signal_type, score = scorer.score("CALL NOW FOR FREE SHIPPING")
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL

    def test_below_threshold(self, scorer: KeywordScorer):
        """A single weak match below threshold should be inconclusive."""
        signal_type, score = scorer.score("Don't miss out on the show tonight")
        assert signal_type is None

    def test_custom_patterns(self):
        """Custom patterns from config should override defaults."""
        config = TranscriptConfig(
            commercial_patterns=[
                (r"\bcustom\s+pattern\b", 5.0),
            ]
        )
        scorer = KeywordScorer(config)
        signal_type, score = scorer.score("This has a custom pattern in it")
        assert signal_type is SignalType.TRANSCRIPT_COMMERCIAL
        assert score >= 5.0

    def test_custom_patterns_ignores_defaults(self):
        """When custom patterns are set, default patterns should not apply."""
        config = TranscriptConfig(
            commercial_patterns=[
                (r"\bunique\b", 5.0),
            ]
        )
        scorer = KeywordScorer(config)
        signal_type, _score = scorer.score("Call now for free shipping")
        assert signal_type is None


# ===========================================================================
# Engine integration tests — transcript signals with scoring engine
# ===========================================================================

class TestTranscriptCommercialScoring:
    """TRANSCRIPT_COMMERCIAL contributes positive score toward commercial state."""

    def test_transcript_commercial_with_silence_enters_commercial(self, engine: DetectionEngine):
        """Silence (5.0) + transcript_commercial (3.0) = 8.0 >= threshold."""
        engine.process(_silence_end(5.0))
        result = engine.process(_transcript_commercial(5.0))  # same timestamp, no decay
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_multiple_transcript_commercial_accumulate(self, engine: DetectionEngine):
        """Multiple transcript commercials can reach threshold: 3.0 + 3.0 + 3.0 = 9.0."""
        engine.process(_transcript_commercial(5.0))  # 3.0
        engine.process(_transcript_commercial(6.0))  # 3.0 - 0.5 + 3.0 = 5.5
        result = engine.process(_transcript_commercial(7.0))  # 5.5 - 0.5 + 3.0 = 8.0
        assert result is not None
        assert result.to_state is DetectionState.COMMERCIAL

    def test_transcript_commercial_reinforces_during_commercial(self, engine: DetectionEngine):
        """During COMMERCIAL, transcript_commercial keeps score elevated."""
        # Enter COMMERCIAL
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Feed transcript_commercial signals to keep score up
        engine.process(_transcript_commercial(15.0))
        engine.process(_transcript_commercial(25.0))

        # Score should still be positive
        assert engine._state.commercial_score > 0


class TestTranscriptProgramScoring:
    """TRANSCRIPT_PROGRAM contributes negative score toward program state."""

    def test_transcript_program_helps_exit_commercial(self, engine: DetectionEngine):
        """After score decays, TRANSCRIPT_PROGRAM pushes below program threshold."""
        # Enter COMMERCIAL
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Wait for decay (score ~13.5 decays toward 0), then negative signal
        # At t=34: score ~13.5 - 14.25 = ~0, then -4.0 = -4.0 < -3.0
        result = engine.process(_transcript_program(34.0))
        assert result is not None
        assert result.to_state is DetectionState.PROGRAM

    def test_transcript_program_faster_than_pure_decay(self, engine: DetectionEngine):
        """With negative signals, exit happens sooner than waiting for decay alone."""
        # Enter COMMERCIAL
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Combined negative signals after partial decay
        # At t=30: score ~13.25 - (24.5 * 0.5) = ~13.25 - 12.25 = ~1.0
        # transcript_program: 1.0 - 4.0 = -3.0 <= program_threshold (-3.0)
        result = engine.process(_transcript_program(30.0))
        assert result is not None
        assert result.to_state is DetectionState.PROGRAM

    def test_transcript_program_respects_debounce(self, debounced_engine: DetectionEngine):
        """Even with negative signals, min_commercial_duration must be respected."""
        engine = debounced_engine

        # Enter COMMERCIAL at t=5.5
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        assert engine.current_state is DetectionState.COMMERCIAL

        # Try exit at t=10 — only 4.5s in COMMERCIAL (< 15s min)
        # Score: 13.5 - 2.25 = 11.25 - 4.0 = 7.25 (also above program threshold)
        result = engine.process(_transcript_program(10.0))
        assert result is None
        assert engine.current_state is DetectionState.COMMERCIAL

    def test_transcript_program_ignored_in_unknown(self, engine: DetectionEngine):
        """TRANSCRIPT_PROGRAM has no special effect in UNKNOWN state (just score)."""
        result = engine.process(_transcript_program(5.0))
        assert result is None
        assert engine.current_state is DetectionState.UNKNOWN


class TestTranscriptSignalMetadata:
    """Verify transcript signals produce correct transition metadata."""

    def test_transcript_commercial_metadata(self, engine: DetectionEngine):
        engine.process(_silence_end(5.0))
        result = engine.process(_transcript_commercial(5.0))  # same timestamp

        assert result is not None
        assert result.signals["trigger_signal"] == "transcript_commercial"
        assert "commercial_score" in result.signals

    def test_transcript_program_metadata(self, engine: DetectionEngine):
        # Enter COMMERCIAL then exit
        engine.process(_silence_end(5.0))
        engine.process(_black_start(5.5))
        result = engine.process(_transcript_program(34.0))

        assert result is not None
        assert result.signals["trigger_signal"] == "transcript_program"
