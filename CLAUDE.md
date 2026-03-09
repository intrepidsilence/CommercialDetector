# CommercialDetector

Real-time commercial break detection for live TV streams via HDMI capture, running on a single-board computer, with state changes published to a message queue.

## Skills

When working on this project, invoke these skills for domain expertise:
- `/research-first` — use before implementing any major feature (audio fingerprinting, video analysis, ML models) to produce a research document first
- `/av-sync-architect` — when working on audio/video stream synchronization or timing-sensitive detection logic
- `/backend-developer` — when designing the message queue integration, service architecture, or API endpoints
- `/python-pro` — when writing core detection logic, stream processing, or SBC-optimized Python code
- `/debug-server-architect` — when building HTTP debug/monitoring endpoints for headless operation on the SBC
- `/cache-invalidation-pipeline` — when implementing incremental state tracking or checkpoint/resume for the detection pipeline

## Tech Stack

- **Language:** Python 3.11+
- **Signal source:** FFmpeg subprocess with `silencedetect`, `blackdetect`, `scdet` filters (V4L2/ALSA input for live, file input for testing)
- **State machine:** Python event-driven engine — tracks silence gap frequency for state decisions
- **Message queue:** Mosquitto MQTT broker + paho-mqtt Python client
- **Target hardware:** Raspberry Pi 5 (or equivalent ARM SBC)

## Build & Run

```bash
# Dependencies (Raspberry Pi OS)
sudo apt install mosquitto mosquitto-clients ffmpeg
pip install paho-mqtt pyyaml

# Run with live capture (default config.yaml)
python -m commercial_detector

# Run with a recorded file for testing
python -m commercial_detector --input test_video.mp4

# Dry run (no MQTT, just log state changes)
python -m commercial_detector --input test_video.mp4 --dry-run

# Custom config
python -m commercial_detector --config /path/to/config.yaml

# Monitor MQTT output in another terminal
mosquitto_sub -t "commercial-detector/#" -v

# Run tests
python -m pytest tests/ -v
```

## Architecture

### System Overview

```
HDMI Source (TV)
      │
      ▼
USB Capture Device ──► Single-Board Computer (SBC)
                            │
                            ▼
                       FFmpeg Process
                       (silencedetect + blackdetect + scdet + ebur128 filters)
                            │
                            ▼ (stderr events)
                       Signal Source (Python)
                       (parses FFmpeg filter output + loudness tracking)
                            │
                            ▼
                       Scoring Engine
                       (continuous multi-signal scoring with decay)
                            │
                            ▼
                       MQ Publisher ──► Message Queue
```

### Components

| Component | File | Purpose | Status |
|-----------|------|---------|--------|
| Signal Source | `signal_source.py` | Single FFmpeg process with detection filters (silencedetect + blackdetect + scdet + ebur128); parses stderr for timestamped events | v2 complete |
| Detection Engine | `detection_engine.py` | Continuous multi-signal scoring engine with decay, coincidence detection, scene rate tracking | v2 complete |
| MQ Publisher | `mqtt_publisher.py` | paho-mqtt v2.x: retained state, transition events, heartbeats | MVP complete |
| Orchestrator | `main.py` | Wires signal source → engine → MQTT with CLI and signal handling | MVP complete |
| ~~Stream Capture~~ | `capture.py` | ~~Dual FFmpeg subprocess for raw frames~~ (deprecated — replaced by signal_source.py) | Deprecated |
| ~~Audio Analyzer~~ | `audio_analyzer.py` | ~~NumPy RMS, silence detection~~ (deprecated — FFmpeg silencedetect is better) | Deprecated |
| ~~Video Analyzer~~ | `video_analyzer.py` | ~~OpenCV black frame detection~~ (deprecated — FFmpeg blackdetect is better) | Deprecated |

### Key Design Constraints

- **SBC target**: Must run on inexpensive hardware (e.g., Raspberry Pi 4/5, Orange Pi). ARM architecture, limited RAM (1-8GB), limited CPU
- **Real-time processing**: Must keep up with live TV stream — detection latency should be minimal
- **Headless operation**: No GUI expected; monitoring via logs, MQ messages, or optional debug HTTP endpoint
- **USB capture**: HDMI input via USB capture device (e.g., USB3 HDMI grabber) — likely V4L2 on Linux

## Key Constraints

### Hardware Limitations
- ARM SBC with limited compute — avoid heavy ML models unless quantized/optimized
- USB capture bandwidth constraints — may limit resolution/framerate for analysis
- Memory-constrained — stream processing must avoid buffering large amounts of video

### Commercial Detection Challenges
- No single reliable signal — commercial detection typically requires combining multiple heuristics:
  - Black frame insertion between segments
  - Audio silence or volume spikes
  - Scene change frequency
  - Channel logo presence/absence
  - Audio fingerprinting against known commercial databases
- False positives from in-program segment transitions
- Different broadcasters use different commercial insertion techniques

## Code Quality

### Patterns to Follow
- Keep detection heuristics modular and independently testable
- Use a clean state machine pattern for commercial/program state transitions
- Stream processing should be memory-efficient (process frames, don't buffer them)
- Configuration-driven thresholds (don't hardcode detection parameters)

### Patterns to Avoid
- DO NOT load entire video segments into memory — process frame-by-frame or in small windows
- DO NOT rely on a single detection signal — combine multiple heuristics for reliability
- DO NOT use heavyweight ML frameworks if simpler signal processing suffices for the SBC target

## Testing

```bash
python -m pytest tests/ -v  # 94 tests, ~0.12s
```

- Unit tests cover signal source parsing (incl. ebur128), scoring engine, transcript analysis, and MQTT publisher (94 tests)
- Signal generators in `tests/conftest.py` for reproducible testing
- TODO: Integration test with recorded commercial break sequences
- TODO: Performance test on target SBC hardware to verify real-time processing

## Dead Ends — DO NOT REPEAT

- **Custom Python audio/video analysis** — The original approach (FFmpeg → raw frames → Python numpy/OpenCV analysis) failed: audio pipe dies after 2s in file mode, wall-clock timestamps instead of PTS, subsampled analysis misses short events. Use FFmpeg's built-in filters instead.
- **Black frame detection alone** — Misses 33% of commercial breaks entirely (GDELT study). Must combine with audio silence + other signals.
- **Absolute brightness thresholds for commercial detection** — Program content varies wildly in brightness (32 in dark scenes to 113 in daytime). Commercial boundaries don't always have black frames.
- **Score accumulation per-tick** — The Comskip-inspired per-tick scoring approach couldn't adapt to content that lacks black frames. Replaced by continuous multi-signal scoring with decay.
- **Gap-counting state machine** — Required 2+ boundary markers to enter COMMERCIAL (5-25s latency) and 90s timeout to exit (45-90s latency). Replaced by continuous scoring engine where coincident silence+black triggers entry in 2-3s and negative signals (transcript, loudness drop) enable exit in 4-10s.
- **Closed captioning via HDMI capture** — USB capture cards don't pass through CC data (embedded in VBI/MPEG metadata, not in raw video frames).
- **librosa on SBC** — Heavy dependencies (scipy, numba) consume too much RAM. Use FFmpeg silencedetect instead.
- **Heavy ML models on SBC** — Full neural networks overwhelm RPi-class hardware. Use simple heuristics; if ML needed, use quantized TFLite.

## References

$FILE:SPEC.md
$FILE:docs/RESEARCH-commercial-detection-stack.md
