"""FFmpeg filter-based signal source for commercial detection.

Runs a single FFmpeg process with silencedetect, blackdetect, and scdet
filters. Parses the filter output from stderr in real-time and yields
DetectionSignal events with proper presentation timestamps.

This replaces the old dual-subprocess capture + Python analysis approach.
FFmpeg's built-in filters are battle-tested C code that operate at full
sample rate, giving us much better signal quality than our custom Python
analysis ever could.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from typing import Iterator, Optional

from .config import AppConfig, CaptureConfig, EngineConfig, SignalSourceConfig
from .models import DetectionSignal, SignalType

logger = logging.getLogger(__name__)


class SignalSourceError(Exception):
    """Raised when FFmpeg fails to start or encounters a fatal error."""


# ---------------------------------------------------------------------------
# Regex patterns for FFmpeg filter output lines
# ---------------------------------------------------------------------------

# [silencedetect @ 0x...] silence_start: 5.47
_RE_SILENCE_START = re.compile(
    r"\[silencedetect\s*@\s*\S+\]\s*silence_start:\s*([\d.]+)"
)

# [silencedetect @ 0x...] silence_end: 5.77 | silence_duration: 0.3
_RE_SILENCE_END = re.compile(
    r"\[silencedetect\s*@\s*\S+\]\s*silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)"
)

# [blackdetect @ 0x...] black_start:5.47 black_end:5.52 black_duration:0.05
_RE_BLACK_DETECT = re.compile(
    r"\[blackdetect\s*@\s*\S+\]\s*black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)"
)

# [Parsed_scdet_...] lavfi.scd.score=42.35 lavfi.scd.time=12.50
# Also matches: [scdet @ ...] lavfi.scd.score: 42.35, lavfi.scd.time: 12.50
_RE_SCENE_CHANGE = re.compile(
    r"lavfi\.scd\.score[=:]\s*([\d.]+).*?lavfi\.scd\.time[=:]\s*([\d.]+)"
)

# [Parsed_ebur128_1 @ 0x...] t: 1.2  TARGET:-23 LUFS  M: -22.3 S: -21.8 ...
# We capture t (timestamp), M (momentary LUFS), and S (short-term LUFS)
_RE_EBUR128 = re.compile(
    r"\[Parsed_ebur128_\d+\s*@\s*\S+\]\s*t:\s*([\d.]+)\s+.*?"
    r"M:\s*(-?[\d.]+)\s+S:\s*(-?[\d.]+)"
)


def parse_stderr_line(line: str) -> Optional[DetectionSignal]:
    """Parse a single line of FFmpeg stderr for filter events.

    Returns a DetectionSignal if the line contains a recognized filter
    event, or None otherwise.  Note: ebur128 lines are parsed separately
    by :func:`parse_ebur128_line` because they require stateful baseline
    tracking.
    """
    m = _RE_SILENCE_START.search(line)
    if m:
        return DetectionSignal(
            timestamp=float(m.group(1)),
            signal_type=SignalType.SILENCE_START,
        )

    m = _RE_SILENCE_END.search(line)
    if m:
        return DetectionSignal(
            timestamp=float(m.group(1)),
            signal_type=SignalType.SILENCE_END,
            value=float(m.group(2)),  # silence duration
        )

    m = _RE_BLACK_DETECT.search(line)
    if m:
        # Emit both start and end signals from a single blackdetect line
        # We return the start event here; the caller can infer the end
        # from the duration if needed. For simplicity, emit a BLACK_START
        # at the start time with the duration as value.
        return DetectionSignal(
            timestamp=float(m.group(1)),
            signal_type=SignalType.BLACK_START,
            value=float(m.group(3)),  # black_duration
        )

    m = _RE_SCENE_CHANGE.search(line)
    if m:
        return DetectionSignal(
            timestamp=float(m.group(2)),
            signal_type=SignalType.SCENE_CHANGE,
            value=float(m.group(1)),  # scene change score
        )

    return None


def parse_ebur128_line(line: str) -> Optional[tuple[float, float, float]]:
    """Parse an ebur128 measurement line.

    Returns (timestamp, momentary_lufs, short_term_lufs) or None.
    """
    m = _RE_EBUR128.search(line)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return None


class SignalSource:
    """Runs FFmpeg with detection filters and yields signal events.

    Usage::

        source = SignalSource(config)
        with source:
            for signal in source:
                engine.process(signal)

    The FFmpeg process runs silencedetect on the audio stream and
    blackdetect + scdet on the video stream. All filter output appears
    on stderr, which we parse line-by-line.
    """

    def __init__(self, config: AppConfig) -> None:
        self._capture_cfg: CaptureConfig = config.capture
        self._signal_cfg: SignalSourceConfig = config.signal_source
        self._engine_cfg: EngineConfig = config.engine
        self._proc: Optional[subprocess.Popen] = None
        self._started = False
        # Loudness baseline tracking (EMA of short-term LUFS)
        self._loudness_baseline: float = -24.0  # EBU R128 reference
        self._loudness_initialized: bool = False
        self._last_loudness_signal_time: float = -10.0  # debounce timer

    def _build_cmd(self) -> list[str]:
        """Build the FFmpeg command with detection filters."""
        cmd = ["ffmpeg", "-hide_banner"]

        if self._capture_cfg.input_file:
            cmd += ["-i", self._capture_cfg.input_file]
        else:
            # Live capture: video from V4L2, audio from ALSA
            cmd += [
                "-f", "v4l2",
                "-input_format", self._capture_cfg.input_format,
                "-i", self._capture_cfg.video_device,
                "-f", "alsa",
                "-i", self._capture_cfg.audio_device,
            ]

        sc = self._signal_cfg

        # Video filters: blackdetect + scene change detection
        vf = (
            f"blackdetect=d={sc.black_min_duration}:pix_th={sc.black_pixel_threshold},"
            f"scdet=threshold={sc.scene_change_threshold}"
        )

        # Audio filters: silence detection + EBU R128 loudness metering
        af = (
            f"silencedetect=noise={sc.silence_noise_db}dB"
            f":d={sc.silence_duration}"
            f",ebur128=peak=true"
        )

        cmd += [
            "-vf", vf,
            "-af", af,
            "-f", "null",
            "-",
        ]
        return cmd

    def start(self) -> None:
        """Launch the FFmpeg process."""
        if self._started:
            return

        cmd = self._build_cmd()
        logger.info("Starting signal source: %s", " ".join(cmd))

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
            )
        except FileNotFoundError:
            raise SignalSourceError(
                "ffmpeg not found. Install it with: sudo apt install ffmpeg"
            )
        except OSError as exc:
            raise SignalSourceError(f"Failed to start FFmpeg: {exc}")

        self._started = True
        logger.info("Signal source started")

    def stop(self) -> None:
        """Terminate the FFmpeg process."""
        if not self._started or self._proc is None:
            return

        logger.info("Stopping signal source")
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg did not terminate, killing")
            self._proc.kill()
            self._proc.wait()
        except Exception:
            logger.exception("Error terminating FFmpeg")

        self._proc = None
        self._started = False

    def _process_ebur128(self, line: str) -> Optional[DetectionSignal]:
        """Check for ebur128 measurement and emit LOUDNESS_SHIFT if significant.

        Tracks an EMA baseline and only emits a signal when the short-term
        loudness deviates by more than ``loudness_shift_db`` from baseline.
        The signal's ``value`` carries the signed delta (positive = louder).
        """
        parsed = parse_ebur128_line(line)
        if parsed is None:
            return None

        timestamp, momentary, short_term = parsed

        # Ignore invalid readings (ebur128 outputs -inf for silence)
        if momentary < -70.0:
            return None

        alpha = self._engine_cfg.loudness_ema_alpha
        shift_threshold = self._engine_cfg.loudness_shift_db

        if not self._loudness_initialized:
            self._loudness_baseline = short_term
            self._loudness_initialized = True
            return None

        # Update EMA baseline (tracks long-term average using short-term LUFS)
        self._loudness_baseline += alpha * (short_term - self._loudness_baseline)

        # Use short-term LUFS (3s window) for deviation — stable boundary detection
        delta = short_term - self._loudness_baseline
        if abs(delta) >= shift_threshold:
            # Debounce: minimum 5s between loudness shift signals
            if timestamp - self._last_loudness_signal_time < 5.0:
                return None
            # Reset baseline toward current to avoid repeated triggers
            self._loudness_baseline = short_term
            self._last_loudness_signal_time = timestamp
            return DetectionSignal(
                timestamp=timestamp,
                signal_type=SignalType.LOUDNESS_SHIFT,
                value=delta,  # positive = louder, negative = quieter
            )

        return None

    def __iter__(self) -> Iterator[DetectionSignal]:
        """Yield DetectionSignal events parsed from FFmpeg stderr.

        Blocks on each line of FFmpeg's stderr output. The FFmpeg process
        runs at real-time (or faster for files) and emits filter events
        as they occur.
        """
        if not self._started:
            self.start()

        assert self._proc is not None
        assert self._proc.stderr is not None

        for line in self._proc.stderr:
            # Try standard filter events first
            signal = parse_stderr_line(line)
            if signal is not None:
                logger.debug(
                    "Signal: %s @ %.2fs (value=%.3f)",
                    signal.signal_type.value,
                    signal.timestamp,
                    signal.value,
                )
                yield signal
                continue

            # Try ebur128 loudness measurement (stateful — needs baseline tracking)
            signal = self._process_ebur128(line)
            if signal is not None:
                logger.debug(
                    "Signal: %s @ %.2fs (delta=%.1f LUFS)",
                    signal.signal_type.value,
                    signal.timestamp,
                    signal.value,
                )
                yield signal

        # Check exit code
        rc = self._proc.wait()
        if rc != 0:
            logger.warning("FFmpeg exited with code %d", rc)

    def __enter__(self) -> SignalSource:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
