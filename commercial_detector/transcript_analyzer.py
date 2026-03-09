"""Whisper-based transcript analysis for commercial detection.

Runs faster-whisper in a background thread alongside the existing FFmpeg
signal pipeline. Transcribed audio is scored by a keyword matcher that
emits TRANSCRIPT_COMMERCIAL or TRANSCRIPT_PROGRAM signals via a queue
consumed by the main loop.

The KeywordScorer is pure Python (no Whisper dependency) for easy testing.
The TranscriptAnalyzer requires faster-whisper but wraps the import in
try/except so the entire system works without it installed.
"""

from __future__ import annotations

import logging
import re
import struct
import subprocess
import threading
from queue import Queue
from typing import Optional

from .config import AppConfig, TranscriptConfig
from .models import DetectionSignal, SignalType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default commercial indicator patterns (regex, weight)
# ---------------------------------------------------------------------------

_DEFAULT_COMMERCIAL_PATTERNS: list[tuple[str, float]] = [
    # Direct-response / call-to-action
    (r"\bcall\s+now\b", 3.0),
    (r"\bact\s+now\b", 3.0),
    (r"\border\s+now\b", 2.5),
    (r"\bbuy\s+now\b", 2.5),
    (r"\bsign\s+up\s+(?:now|today)\b", 2.0),
    (r"\bdon'?t\s+miss\s+(?:out|this)\b", 1.5),
    (r"\bbut\s+wait\b.*\bmore\b", 2.0),
    # Pricing and offers (Whisper often omits $ signs)
    (r"\$\s*\d+", 2.0),
    (r"\b\d+\s*%\s*(?:off|financing|apr|interest)\b", 2.0),
    (r"\b\d+\s*(?:dollars|cents)\b", 1.5),
    (r"\btotal\s+savings\b", 2.0),
    (r"\bfree\s+shipping\b", 2.5),
    (r"\blimited\s+time\b", 2.0),
    (r"\boffer\s+expires\b", 2.0),
    (r"\bmoney[\s-]?back\s+guarantee\b", 2.5),
    (r"\bsatisfaction\s+guaranteed\b", 2.5),
    (r"\bstarting\s+at\s+(?:\$\s*)?\d+", 1.5),
    (r"\bfinancing\s+for\s+\d+\s+months\b", 2.0),
    (r"\bsave\s+(?:up\s+to\s+)?\d+", 1.5),
    # Contact / web
    (r"\bvisit\s+\S+\s*\.?\s*(?:dot\s+)?com\b", 2.5),
    (r"\b1[\s-]?800[\s-]?\d+", 3.0),
    (r"\bwww\.\S+", 2.0),
    (r"\bdot\s+com\b", 1.5),
    # Medical / pharmaceutical
    (r"\bside\s+effects?\b", 3.0),
    (r"\bresults\s+may\s+vary\b", 3.0),
    (r"\bas\s+seen\s+on\s+tv\b", 3.0),
    (r"\bask\s+your\s+doctor\b", 3.0),
    # Sponsorship / ad identity markers
    (r"\bproud\s+sponsor\b", 2.5),
    (r"\bofficial\s+(?:\w+\s+)*(?:sponsor|partner|company)\b", 2.0),
    (r"\bbrought\s+to\s+you\s+by\b", 2.5),
    (r"\bpresented\s+by\b", 2.0),
    (r"\bis\s+presented\s+by\b", 2.0),
    # Brand-awareness commercial language
    (r"\bbuilt\s+(?:to|for)\s+\w+\b", 1.0),
    (r"\byou\s+(?:can\s+)?depend\s+on\b", 1.0),
    (r"\bprotect(?:ing|ion)?\s+your\b", 1.0),
    (r"\bconveniently\s+located\b", 1.5),
    (r"\bamerica'?s?\s+(?:best|number\s+one|#1|favorite)\b", 1.5),
    (r"\b(?:award[\s-]?winning|most\s+awarded)\b", 1.5),
    # Security / home automation (conversational commercial style)
    (r"\b(?:24\s*[/x]\s*7|twenty[\s-]?four[\s-]?seven)\s+monitoring\b", 2.5),
    (r"\bprofessional\s+monitoring\b", 2.0),
    (r"\bpeace\s+of\s+mind\b", 1.5),
    (r"\bprotect\s+your\s+(?:home|family)\b", 1.5),
    (r"\bperimeter\s+protection\b", 2.0),
    (r"\bsmart\s+home\s+(?:security|company)\b", 2.0),
    # Healthcare / dental / wellness (beyond pharma)
    (r"\bhighly\s+skilled\s+(?:doctors?|dentists?)\b", 1.5),
    (r"\bdental\s+care\b", 1.5),
    (r"\b(?:offices?|locations?)\s+(?:conveniently\s+)?(?:located\s+)?across\b", 1.5),
    # Insurance / financial services
    (r"\bget\s+a\s+(?:free\s+)?quote\b", 2.0),
    (r"\bcoverage\s+for\b", 1.0),
    (r"\bget\s+approved\b", 2.0),
    # Urgency / limited availability (softer than "call now")
    (r"\bwhile\s+supplies\s+last\b", 2.0),
    (r"\bthis\s+(?:week|month)\s+only\b", 2.0),
    (r"\bfor\s+a\s+limited\s+time\b", 2.0),
]


class KeywordScorer:
    """Scores transcript text for commercial vs program content.

    Pure Python — no external dependencies. Uses regex patterns to detect
    commercial language and word count heuristics for program dialog.

    Usage::

        scorer = KeywordScorer(config)
        classification, score = scorer.score("Call now for free shipping!")
        # -> (SignalType.TRANSCRIPT_COMMERCIAL, 5.5)
    """

    def __init__(self, config: TranscriptConfig) -> None:
        self._config = config

        # Build compiled patterns
        if config.commercial_patterns:
            raw = [(p, w) for p, w in config.commercial_patterns]
        else:
            raw = _DEFAULT_COMMERCIAL_PATTERNS

        self._patterns: list[tuple[re.Pattern, float]] = [
            (re.compile(pattern, re.IGNORECASE), weight)
            for pattern, weight in raw
        ]
        self._program_min_words = config.program_min_words

    def score(self, text: str) -> tuple[Optional[SignalType], float]:
        """Score transcript text for commercial vs program content.

        Returns:
            (signal_type, score) — signal_type is TRANSCRIPT_COMMERCIAL,
            TRANSCRIPT_PROGRAM, or None if inconclusive. Score is the
            absolute strength of the classification.
        """
        if not text or not text.strip():
            return None, 0.0

        # Commercial score: sum of matched pattern weights
        commercial_score = 0.0
        for pattern, weight in self._patterns:
            matches = pattern.findall(text)
            commercial_score += len(matches) * weight

        # Program score: continuous speech without commercial indicators
        words = text.split()
        word_count = len(words)
        program_score = 0.0

        if word_count >= self._program_min_words and commercial_score < 1.0:
            # Speech without commercial markers -> likely program dialog
            # Score scales with word count relative to threshold
            program_score = min(word_count / self._program_min_words, 3.0) * 2.0

        # Classify
        if commercial_score >= self._config.commercial_threshold:
            return SignalType.TRANSCRIPT_COMMERCIAL, commercial_score
        elif program_score >= self._config.program_threshold:
            return SignalType.TRANSCRIPT_PROGRAM, program_score
        else:
            return None, max(commercial_score, program_score)


class TranscriptAnalyzer:
    """Background thread that transcribes audio and emits detection signals.

    Launches a second FFmpeg subprocess extracting audio as 16kHz mono PCM,
    feeds chunks to faster-whisper, scores the transcript, and puts
    DetectionSignal instances into a shared queue.

    Requires ``faster-whisper`` to be installed. If not available, ``start()``
    logs a warning and returns without starting the thread.
    """

    def __init__(self, config: AppConfig, signal_queue: Queue) -> None:
        self._config = config
        self._transcript_config = config.transcript
        self._queue = signal_queue
        self._scorer = KeywordScorer(config.transcript)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._last_text: str = ""

    def start(self) -> bool:
        """Start the transcript analysis thread.

        Returns True if started successfully, False if faster-whisper
        is not available or the feature is disabled.
        """
        if not self._transcript_config.enabled:
            logger.debug("Transcript analysis disabled")
            return False

        # Check for faster-whisper availability
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError:
            logger.warning(
                "Transcript analysis enabled but faster-whisper not installed. "
                "Install with: pip install faster-whisper"
            )
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="transcript-analyzer",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Transcript analyzer started (model=%s, chunk=%.0fs)",
            self._transcript_config.whisper_model,
            self._transcript_config.chunk_duration,
        )
        return True

    def stop(self) -> None:
        """Stop the transcript analysis thread and FFmpeg subprocess."""
        self._stop_event.set()

        # Terminate FFmpeg first so the thread's read() unblocks
        if self._ffmpeg_proc is not None:
            try:
                self._ffmpeg_proc.terminate()
                self._ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._ffmpeg_proc.kill()
                self._ffmpeg_proc.wait()
            except Exception:
                logger.exception("Error terminating transcript FFmpeg")
            self._ffmpeg_proc = None

        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                logger.warning("Transcript analyzer thread did not exit cleanly")
            self._thread = None

        logger.info("Transcript analyzer stopped")

    def _build_audio_cmd(self) -> list[str]:
        """Build FFmpeg command for audio-only PCM extraction."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

        capture = self._config.capture
        if capture.input_file:
            cmd += ["-i", capture.input_file]
        else:
            if capture.audio_device:
                cmd += ["-f", "alsa", "-i", capture.audio_device]
            else:
                cmd += [
                    "-f", "v4l2",
                    "-input_format", capture.input_format,
                    "-i", capture.video_device,
                ]

        # Output: 16kHz mono signed 16-bit little-endian PCM to stdout
        cmd += [
            "-vn",           # no video
            "-ar", "16000",  # 16kHz sample rate (Whisper requirement)
            "-ac", "1",      # mono
            "-f", "s16le",   # raw PCM format
            "pipe:1",        # write to stdout
        ]
        return cmd

    def _run(self) -> None:
        """Background thread main loop."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster-whisper disappeared between start() and _run()")
            return

        tc = self._transcript_config

        # Load Whisper model
        try:
            model = WhisperModel(
                tc.whisper_model,
                device="cpu",
                compute_type="int8",
            )
            logger.info("Whisper model loaded: %s", tc.whisper_model)
        except Exception:
            logger.exception("Failed to load Whisper model")
            return

        # Launch audio FFmpeg
        cmd = self._build_audio_cmd()
        logger.info("Starting audio extraction: %s", " ".join(cmd))

        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError) as exc:
            logger.error("Failed to start audio FFmpeg: %s", exc)
            return

        # Read and process audio chunks
        sample_rate = 16000
        bytes_per_sample = 2  # 16-bit
        chunk_bytes = int(tc.chunk_duration * sample_rate * bytes_per_sample)
        audio_offset = 0.0  # Track position in the stream

        assert self._ffmpeg_proc.stdout is not None

        while not self._stop_event.is_set():
            # Read one chunk
            raw = self._ffmpeg_proc.stdout.read(chunk_bytes)
            if not raw:
                break  # EOF or process terminated

            chunk_timestamp = audio_offset
            audio_offset += len(raw) / (sample_rate * bytes_per_sample)

            # Convert raw bytes to float32 numpy array for Whisper
            try:
                import numpy as np
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            except Exception:
                logger.debug("Failed to convert audio chunk at %.1fs", chunk_timestamp)
                continue

            # Transcribe
            try:
                segments, _info = model.transcribe(
                    samples,
                    language="en",
                    vad_filter=True,
                    beam_size=1,
                )
                text = " ".join(seg.text.strip() for seg in segments)
            except Exception:
                logger.debug("Whisper transcription failed at %.1fs", chunk_timestamp)
                continue

            if not text.strip():
                continue

            self._last_text = text[:120]
            logger.debug("Transcript @ %.1fs: %s", chunk_timestamp, text[:100])

            # Score the transcript
            signal_type, score = self._scorer.score(text)

            if signal_type is not None:
                signal = DetectionSignal(
                    timestamp=chunk_timestamp,
                    signal_type=signal_type,
                    value=score,
                )
                self._queue.put(signal)
                logger.info(
                    "Transcript signal: %s @ %.1fs (score=%.1f) — %s",
                    signal_type.value,
                    chunk_timestamp,
                    score,
                    text[:80],
                )

        logger.debug("Transcript analyzer thread exiting")

    @property
    def is_running(self) -> bool:
        """Check if the analyzer thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_text(self) -> str:
        """Most recent transcript snippet."""
        return self._last_text
