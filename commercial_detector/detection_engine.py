"""Detection engine — continuous multi-signal scoring for commercial break detection.

Replaces the gap-counting state machine with a scoring engine where each signal
type contributes a weighted score that decays over time.  Transitions fire when
the running score crosses configurable thresholds.

Key capabilities over the old engine:
- **Fast entry (2-3s)**: A coincident silence+black event scores 13.5, clearing
  the default 8.0 threshold in a single event pair.
- **Fast exit with transcript (4-10s)**: Negative weights from TRANSCRIPT_PROGRAM
  and loudness drops push the score below the program threshold quickly.
- **Scene change rate**: High scene change frequency (typical of commercials)
  contributes a rate-based boost on top of individual scene change scores.
"""

from __future__ import annotations

import logging
from typing import Optional

from .config import EngineConfig
from .models import (
    DetectionSignal,
    DetectionState,
    EngineState,
    SignalType,
    StateTransition,
)

logger = logging.getLogger(__name__)


class DetectionEngine:
    """Continuous scoring engine that decides PROGRAM vs COMMERCIAL.

    Call :meth:`process` with each DetectionSignal from the signal source.
    The engine maintains a running ``commercial_score`` that rises with
    commercial-like signals (silence gaps, black frames, loudness jumps,
    commercial transcripts) and falls with program-like signals (loudness
    drops, program transcripts) plus continuous time-based decay.

    Transition logic:
    - PROGRAM/UNKNOWN → COMMERCIAL: ``score >= commercial_threshold``
    - COMMERCIAL → PROGRAM: ``score <= program_threshold``
    - Both transitions gated by minimum dwell time (debounce).
    """

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._state = EngineState()

    def process(self, signal: DetectionSignal) -> Optional[StateTransition]:
        """Ingest one signal event and optionally emit a state transition."""
        cfg = self._config
        st = self._state

        # Apply time-based score decay since last update
        self._apply_decay(signal.timestamp)

        st.last_signal_time = signal.timestamp

        # During hold-off, track state but don't accumulate score
        if signal.timestamp < cfg.initial_hold_off:
            return None

        # Apply signal contribution to score
        score_delta = self._score_signal(signal)

        if score_delta != 0.0:
            st.commercial_score += score_delta
            # Clamp score to prevent runaway accumulation
            st.commercial_score = max(cfg.min_score, min(cfg.max_score, st.commercial_score))
            logger.debug(
                "Score: %.2f (%+.2f from %s @ %.1fs)",
                st.commercial_score,
                score_delta,
                signal.signal_type.value,
                signal.timestamp,
            )

        # Check scene change rate and apply boost
        self._update_scene_rate(signal)

        # Determine desired state from score
        desired: Optional[DetectionState] = None

        if st.current_state in (DetectionState.PROGRAM, DetectionState.UNKNOWN):
            if st.commercial_score >= cfg.commercial_threshold:
                desired = DetectionState.COMMERCIAL
        elif st.current_state == DetectionState.COMMERCIAL:
            if st.commercial_score <= cfg.program_threshold:
                desired = DetectionState.PROGRAM

        # No transition needed
        if desired is None or desired == st.current_state:
            return None

        # Apply debounce — minimum time in current state
        elapsed_in_state = signal.timestamp - st.state_entered_at

        if st.current_state == DetectionState.COMMERCIAL:
            if elapsed_in_state < cfg.min_commercial_duration:
                return None
        elif st.current_state == DetectionState.PROGRAM:
            if elapsed_in_state < cfg.min_program_duration:
                return None
        # UNKNOWN -> anything: no minimum duration (initial_hold_off handles it)

        # Calculate confidence from score magnitude
        confidence = self._calculate_confidence(desired)

        transition = StateTransition(
            timestamp=signal.timestamp,
            from_state=st.current_state,
            to_state=desired,
            confidence=confidence,
            signals={
                "commercial_score": round(st.commercial_score, 2),
                "trigger_signal": signal.signal_type.value,
                "score_delta": round(score_delta, 2),
            },
        )

        logger.info(
            "State transition: %s -> %s @ %.1fs "
            "(score=%.2f, trigger=%s, confidence=%.3f)",
            st.current_state.value,
            desired.value,
            signal.timestamp,
            st.commercial_score,
            signal.signal_type.value,
            confidence,
        )

        st.current_state = desired
        st.state_entered_at = signal.timestamp
        return transition

    def _apply_decay(self, current_time: float) -> None:
        """Decay the commercial score toward zero based on elapsed time."""
        st = self._state
        if st.last_score_update <= 0.0:
            st.last_score_update = current_time
            return

        elapsed = current_time - st.last_score_update
        if elapsed <= 0.0:
            return

        decay = self._config.score_decay_rate * elapsed
        if st.commercial_score > 0:
            st.commercial_score = max(0.0, st.commercial_score - decay)
        elif st.commercial_score < 0:
            st.commercial_score = min(0.0, st.commercial_score + decay)

        st.last_score_update = current_time

    def _score_signal(self, signal: DetectionSignal) -> float:
        """Calculate the score contribution of a single signal.

        Handles per-type weights and coincidence detection for
        silence+black co-occurrence.
        """
        cfg = self._config
        st = self._state
        delta = 0.0

        if signal.signal_type == SignalType.SILENCE_END:
            delta = cfg.silence_weight
            # Check for coincidence with recent black frame
            if (
                st.last_black_time >= 0
                and abs(signal.timestamp - st.last_black_time) <= cfg.coincidence_window
            ):
                # Full multiplier when entering commercial; reduced when already in
                # commercial (ad-boundary within break, not strong new evidence)
                multiplier = (
                    cfg.coincidence_multiplier
                    if st.current_state != DetectionState.COMMERCIAL
                    else 1.0
                )
                if multiplier > 1.0:
                    delta *= multiplier
                    retroactive = cfg.black_frame_weight * (multiplier - 1.0)
                    st.commercial_score = min(
                        cfg.max_score, st.commercial_score + retroactive
                    )
                    logger.debug(
                        "Coincidence bonus: silence+black within %.1fs "
                        "(retroactive +%.2f for black)",
                        abs(signal.timestamp - st.last_black_time),
                        retroactive,
                    )
            st.last_silence_time = signal.timestamp

        elif signal.signal_type == SignalType.BLACK_START:
            delta = cfg.black_frame_weight
            # Check for coincidence with recent silence
            if (
                st.last_silence_time >= 0
                and abs(signal.timestamp - st.last_silence_time) <= cfg.coincidence_window
            ):
                multiplier = (
                    cfg.coincidence_multiplier
                    if st.current_state != DetectionState.COMMERCIAL
                    else 1.0
                )
                if multiplier > 1.0:
                    delta *= multiplier
                    retroactive = cfg.silence_weight * (multiplier - 1.0)
                    st.commercial_score = min(
                        cfg.max_score, st.commercial_score + retroactive
                    )
                    logger.debug(
                        "Coincidence bonus: black+silence within %.1fs "
                        "(retroactive +%.2f for silence)",
                        abs(signal.timestamp - st.last_silence_time),
                        retroactive,
                    )
            st.last_black_time = signal.timestamp

        elif signal.signal_type == SignalType.LOUDNESS_SHIFT:
            # Positive value = louder (commercial start), negative = quieter (program)
            if signal.value > 0:
                delta = cfg.loudness_up_weight
            else:
                delta = cfg.loudness_down_weight

        elif signal.signal_type == SignalType.SCENE_CHANGE:
            delta = cfg.scene_change_weight
            # Scene rate boost is handled in _update_scene_rate

        elif signal.signal_type == SignalType.TRANSCRIPT_COMMERCIAL:
            delta = cfg.transcript_commercial_weight

        elif signal.signal_type == SignalType.TRANSCRIPT_PROGRAM:
            delta = cfg.transcript_program_weight

        # SILENCE_START and BLACK_END are informational — no score contribution

        return delta

    def _update_scene_rate(self, signal: DetectionSignal) -> None:
        """Track scene change rate and apply a boost if rate is high."""
        cfg = self._config
        st = self._state

        # Always prune stale entries (prevents unbounded growth if non-scene
        # signals dominate for extended periods)
        if st.scene_change_times:
            cutoff = signal.timestamp - cfg.scene_rate_window
            st.scene_change_times = [
                t for t in st.scene_change_times if t > cutoff
            ]

        if signal.signal_type != SignalType.SCENE_CHANGE:
            return

        st.scene_change_times.append(signal.timestamp)

        # Calculate rate (changes per minute)
        if len(st.scene_change_times) >= 2:
            window_span = st.scene_change_times[-1] - st.scene_change_times[0]
            if window_span > 0:
                rate_per_min = (len(st.scene_change_times) / window_span) * 60.0
                if rate_per_min >= cfg.scene_rate_threshold:
                    st.commercial_score = min(
                        cfg.max_score, st.commercial_score + cfg.scene_rate_boost
                    )
                    logger.debug(
                        "Scene rate boost: +%.1f (rate=%.1f/min, threshold=%.1f/min)",
                        cfg.scene_rate_boost,
                        rate_per_min,
                        cfg.scene_rate_threshold,
                    )

    def _calculate_confidence(self, desired: DetectionState) -> float:
        """Calculate confidence from how far the score exceeds the threshold."""
        cfg = self._config
        score = self._state.commercial_score

        if desired == DetectionState.COMMERCIAL:
            excess = score - cfg.commercial_threshold
            # Normalize: 0 at threshold, 1.0 at 2x threshold
            return min(1.0, max(0.1, 0.5 + excess / (cfg.commercial_threshold * 2)))
        else:
            excess = cfg.program_threshold - score
            return min(1.0, max(0.1, 0.5 + excess / (abs(cfg.program_threshold) * 2)))

    @property
    def current_state(self) -> DetectionState:
        """Return the engine's current detection state."""
        return self._state.current_state

    def reset(self) -> None:
        """Reset the engine to its initial state."""
        self._state = EngineState()
