"""Configuration management — loads thresholds from YAML with sensible defaults.

All detection parameters live here so nothing is hardcoded in analysis code.
Config can come from a YAML file, environment overrides, or pure defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Config dataclass tree
# ---------------------------------------------------------------------------

@dataclass
class CaptureConfig:
    """Input source settings (kept for live capture device paths)."""
    video_device: str = "/dev/video0"
    audio_device: str = "hw:1"
    input_format: str = "mjpeg"    # V4L2 pixel format
    # For file-based testing instead of live capture
    input_file: Optional[str] = None


@dataclass
class SignalSourceConfig:
    """FFmpeg filter detection parameters."""
    # silencedetect filter
    silence_noise_db: float = -42.0   # Noise floor threshold (filters out dialog pauses)
    silence_duration: float = 0.5     # Minimum silence duration (seconds)
    # blackdetect filter
    black_min_duration: float = 0.05  # Minimum black frame duration
    black_pixel_threshold: float = 0.10  # Pixel brightness threshold (0-1)
    # scdet (scene change detection) filter
    scene_change_threshold: float = 30.0  # Scene change score threshold (10 too sensitive for live HDMI)


@dataclass
class EngineConfig:
    """Detection engine — continuous multi-signal scoring.

    Each signal type contributes a weighted score that decays over time.
    Transitions fire when the score crosses thresholds.  Coincident
    signals (silence + black within a window) get a multiplier.
    """
    # Hold-off — ignore first N seconds (avoids false triggers on intros)
    initial_hold_off: float = 60.0

    # Minimum dwell time in each state (debounce)
    min_commercial_duration: float = 15.0
    min_program_duration: float = 60.0

    # --- Signal weights (positive = commercial, negative = program) ---
    silence_weight: float = 5.0
    black_frame_weight: float = 4.0
    loudness_up_weight: float = 4.0       # loudness jump = ad start
    loudness_down_weight: float = -3.0    # loudness drop = program resumes
    scene_change_weight: float = 0.5      # individual scene change
    transcript_commercial_weight: float = 3.0
    transcript_program_weight: float = -4.0

    # --- Coincidence detection ---
    coincidence_window: float = 2.0       # seconds for silence+black co-occurrence
    coincidence_multiplier: float = 1.5

    # --- Score dynamics ---
    score_decay_rate: float = 0.6         # points per second (tuned: 25s post-break gap × 0.6 = 15 = max_score)
    commercial_threshold: float = 8.0     # score to enter COMMERCIAL
    program_threshold: float = -3.0       # score to enter PROGRAM
    max_score: float = 15.0              # clamp ceiling (tuned: 15 - 10×0.6 - 4 = 5 > threshold, safe during breaks)
    min_score: float = -10.0             # clamp score floor

    # --- Loudness ---
    loudness_shift_db: float = 5.0        # LUFS deviation to emit signal (raised from 3.0 to reduce noise)
    loudness_ema_alpha: float = 0.02      # EMA smoothing for baseline

    # --- Scene change rate ---
    scene_rate_window: float = 30.0       # seconds for rate calculation
    scene_rate_threshold: float = 12.0    # changes/min for "high rate" boost
    scene_rate_boost: float = 2.0         # extra score for high scene rate

    # --- Deprecated (kept for config-file backwards compatibility) ---
    silence_gap_window: float = 60.0
    commercial_gap_count: int = 2
    program_gap_timeout: float = 90.0


@dataclass
class MqttConfig:
    """MQTT broker connection settings."""
    broker_host: str = "localhost"
    broker_port: int = 1883
    topic_prefix: str = "commercial-detector"
    client_id: str = "commercial-detector"
    keepalive: int = 60
    qos: int = 1
    # Auth (optional)
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class TranscriptConfig:
    """Whisper transcript analysis settings (optional feature)."""
    enabled: bool = False                  # Off by default — requires faster-whisper
    whisper_model: str = "tiny.en"         # Model size (tiny.en is fastest on ARM)
    chunk_duration: float = 10.0            # Audio chunk size in seconds (10s for reliable program detection)
    commercial_threshold: float = 2.0      # Min score to emit TRANSCRIPT_COMMERCIAL
    program_threshold: float = 2.0         # Min score to emit TRANSCRIPT_PROGRAM
    commercial_patterns: Optional[list] = None   # Override default commercial regexes
    program_min_words: int = 25            # Words without commercial indicators -> program


@dataclass
class WebConfig:
    """Web dashboard settings."""
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    """Top-level application configuration."""
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    signal_source: SignalSourceConfig = field(default_factory=SignalSourceConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    transcript: TranscriptConfig = field(default_factory=TranscriptConfig)
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _apply_dict(target, data: dict) -> None:
    """Recursively apply dict values to a dataclass instance."""
    for key, value in data.items():
        if not hasattr(target, key):
            continue
        current = getattr(target, key)
        if isinstance(value, dict) and hasattr(current, "__dataclass_fields__"):
            _apply_dict(current, value)
        else:
            setattr(target, key, value)


def load_config(path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file, falling back to defaults.

    Precedence: YAML file -> defaults.
    If path is None, looks for config.yaml in the current directory,
    then at COMMERCIAL_DETECTOR_CONFIG env var.
    """
    config = AppConfig()

    if path is None:
        path = os.environ.get("COMMERCIAL_DETECTOR_CONFIG")

    if path is None:
        default_path = Path("config.yaml")
        if default_path.exists():
            path = str(default_path)

    if path is not None:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
            if data:
                _apply_dict(config, data)

    return config
