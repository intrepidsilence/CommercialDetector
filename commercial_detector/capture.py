"""FFmpeg subprocess capture pipeline for HDMI video/audio.

Launches FFmpeg to capture from a V4L2 video device and ALSA audio device
(or from a file for testing), producing CaptureFrame objects with raw
video frames and audio chunks.

Uses two separate FFmpeg processes — one for video, one for audio — to
avoid the complexity of demultiplexing a combined stream from a single pipe.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import Iterator, Optional

import numpy as np

from commercial_detector.config import AppConfig, CaptureConfig
from commercial_detector.models import CaptureFrame

logger = logging.getLogger(__name__)


class CaptureError(Exception):
    """Raised when FFmpeg capture fails to start or encounters a fatal error."""


class StreamCapture:
    """Captures video frames and audio chunks from an HDMI capture device via FFmpeg.

    Supports two modes:
      - Live capture from V4L2 video + ALSA audio devices (default)
      - File input when ``config.capture.input_file`` is set (for testing)

    Uses two FFmpeg subprocesses: one pipes raw RGB24 video frames to stdout,
    the other pipes raw PCM s16le mono audio to stdout via a reader thread.

    Usage::

        with StreamCapture(config) as cap:
            for frame in cap:
                process(frame)
    """

    def __init__(self, config: AppConfig) -> None:
        self._cfg: CaptureConfig = config.capture
        self._video_proc: Optional[subprocess.Popen] = None
        self._audio_proc: Optional[subprocess.Popen] = None
        self._audio_thread: Optional[threading.Thread] = None
        self._audio_buffer: bytearray = bytearray()
        self._audio_lock = threading.Lock()
        self._started = False
        self._stop_event = threading.Event()
        self._start_time: float = 0.0
        self._frame_count: int = 0
        self._frame_bytes = self._cfg.video_width * self._cfg.video_height * 3

    # ------------------------------------------------------------------
    # FFmpeg command construction
    # ------------------------------------------------------------------

    def _build_video_cmd(self) -> list[str]:
        """Build the FFmpeg command for video capture."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

        if self._cfg.input_file:
            cmd += ["-re", "-i", self._cfg.input_file]
        else:
            cmd += [
                "-f", "v4l2",
                "-input_format", self._cfg.input_format,
                "-video_size", f"{self._cfg.video_width}x{self._cfg.video_height}",
                "-framerate", str(self._cfg.video_framerate),
                "-i", self._cfg.video_device,
            ]

        # Build filter chain: scale to analysis resolution + downsample fps.
        # For file input the source may be any resolution (e.g. 4K); for V4L2
        # the capture card already outputs at video_size, but the scale filter
        # is a no-op in that case so it's always safe to include.
        vf = (
            f"scale={self._cfg.video_width}:{self._cfg.video_height},"
            f"fps={self._cfg.analysis_fps}"
        )

        cmd += [
            "-an",  # no audio in this process
            "-vf", vf,
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "pipe:1",
        ]
        return cmd

    def _build_audio_cmd(self) -> list[str]:
        """Build the FFmpeg command for audio capture."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

        if self._cfg.input_file:
            cmd += ["-re", "-i", self._cfg.input_file]
        else:
            cmd += ["-f", "alsa", "-i", self._cfg.audio_device]

        cmd += [
            "-vn",  # no video in this process
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(self._cfg.audio_sample_rate),
            "-ac", str(self._cfg.audio_channels),
            "pipe:1",
        ]
        return cmd

    # ------------------------------------------------------------------
    # Audio reader thread
    # ------------------------------------------------------------------

    def _audio_reader(self) -> None:
        """Background thread that reads audio data from the audio FFmpeg process."""
        assert self._audio_proc is not None
        assert self._audio_proc.stdout is not None

        chunk_size = 4096  # bytes per read
        try:
            while not self._stop_event.is_set():
                data = self._audio_proc.stdout.read(chunk_size)
                if not data:
                    break
                with self._audio_lock:
                    self._audio_buffer.extend(data)
        except Exception:
            if not self._stop_event.is_set():
                logger.exception("Audio reader thread error")

    def _drain_audio(self) -> Optional[np.ndarray]:
        """Drain all accumulated audio samples from the buffer.

        Returns a numpy int16 array of audio samples, or None if no data.
        """
        with self._audio_lock:
            if len(self._audio_buffer) < 2:
                return None
            # Ensure even number of bytes (int16 = 2 bytes per sample)
            n_bytes = len(self._audio_buffer) - (len(self._audio_buffer) % 2)
            raw = bytes(self._audio_buffer[:n_bytes])
            del self._audio_buffer[:n_bytes]

        return np.frombuffer(raw, dtype=np.int16)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the FFmpeg subprocesses for video and audio capture."""
        if self._started:
            return

        video_cmd = self._build_video_cmd()
        audio_cmd = self._build_audio_cmd()

        logger.info("Starting video capture: %s", " ".join(video_cmd))
        logger.info("Starting audio capture: %s", " ".join(audio_cmd))

        try:
            self._video_proc = subprocess.Popen(
                video_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self._frame_bytes * 2,
            )
        except FileNotFoundError:
            raise CaptureError(
                "ffmpeg not found. Install it with: sudo apt install ffmpeg"
            )
        except OSError as exc:
            raise CaptureError(f"Failed to start video FFmpeg: {exc}")

        try:
            self._audio_proc = subprocess.Popen(
                audio_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except (FileNotFoundError, OSError) as exc:
            # Clean up the video process if audio fails to start
            self._video_proc.terminate()
            self._video_proc.wait()
            self._video_proc = None
            raise CaptureError(f"Failed to start audio FFmpeg: {exc}")

        # Give FFmpeg a moment to fail on bad device paths
        time.sleep(0.3)

        if self._video_proc.poll() is not None:
            stderr = self._video_proc.stderr.read().decode(errors="replace") if self._video_proc.stderr else ""
            self._cleanup()
            raise CaptureError(f"Video FFmpeg exited immediately: {stderr.strip()}")

        if self._audio_proc.poll() is not None:
            stderr = self._audio_proc.stderr.read().decode(errors="replace") if self._audio_proc.stderr else ""
            self._cleanup()
            raise CaptureError(f"Audio FFmpeg exited immediately: {stderr.strip()}")

        self._stop_event.clear()
        self._audio_thread = threading.Thread(
            target=self._audio_reader, daemon=True, name="audio-reader"
        )
        self._audio_thread.start()

        self._start_time = time.monotonic()
        self._frame_count = 0
        self._started = True
        logger.info("Capture started (video %dx%d @ %d analysis fps, audio %d Hz)",
                     self._cfg.video_width, self._cfg.video_height,
                     self._cfg.analysis_fps, self._cfg.audio_sample_rate)

    def stop(self) -> None:
        """Terminate FFmpeg subprocesses and clean up resources."""
        if not self._started:
            return
        logger.info("Stopping capture (yielded %d frames)", self._frame_count)
        self._stop_event.set()
        self._cleanup()
        self._started = False

    def _cleanup(self) -> None:
        """Terminate processes and join threads."""
        for proc, name in [
            (self._video_proc, "video"),
            (self._audio_proc, "audio"),
        ]:
            if proc is None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("FFmpeg %s process did not terminate, killing", name)
                proc.kill()
                proc.wait()
            except Exception:
                logger.exception("Error terminating %s process", name)

        if self._audio_thread is not None and self._audio_thread.is_alive():
            self._audio_thread.join(timeout=3)

        self._video_proc = None
        self._audio_proc = None
        self._audio_thread = None

        with self._audio_lock:
            self._audio_buffer.clear()

    # ------------------------------------------------------------------
    # Iterator
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[CaptureFrame]:
        """Yield CaptureFrame objects from the running capture.

        Each iteration reads one full video frame from stdout (blocking),
        then drains any accumulated audio samples from the background thread.
        Stops when FFmpeg exits or stop() is called.
        """
        if not self._started:
            self.start()

        assert self._video_proc is not None
        assert self._video_proc.stdout is not None

        while not self._stop_event.is_set():
            raw = self._video_proc.stdout.read(self._frame_bytes)
            if len(raw) < self._frame_bytes:
                if raw:
                    logger.debug("Incomplete video frame (%d/%d bytes), ending",
                                 len(raw), self._frame_bytes)
                break

            video_frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (self._cfg.video_height, self._cfg.video_width, 3)
            )

            audio_chunk = self._drain_audio()

            timestamp = time.monotonic() - self._start_time
            self._frame_count += 1

            yield CaptureFrame(
                timestamp=timestamp,
                video_frame=video_frame,
                audio_chunk=audio_chunk,
                sample_rate=self._cfg.audio_sample_rate,
            )

        # Check if FFmpeg exited with an error
        if self._video_proc is not None and self._video_proc.poll() is not None:
            rc = self._video_proc.returncode
            if rc != 0 and not self._stop_event.is_set():
                stderr = ""
                if self._video_proc.stderr:
                    stderr = self._video_proc.stderr.read().decode(errors="replace").strip()
                logger.error("Video FFmpeg exited with code %d: %s", rc, stderr)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> StreamCapture:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
