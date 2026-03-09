# CommercialDetector

Real-time commercial break detection for live TV streams via HDMI capture, running on a Raspberry Pi or similar single-board computer. State changes are published to MQTT.

## How It Works

Two parallel FFmpeg processes analyze audio and video in real time:

- **Audio**: silence detection, loudness monitoring (EBU R128)
- **Video**: black frame detection, scene change detection
- **Optional**: Whisper speech-to-text for transcript-based classification

A continuous scoring engine combines these signals. When the score crosses a threshold, a state transition (`program` <-> `commercial`) is published to MQTT.

**Typical performance**: 5-10 second detection latency for both entry and exit, zero false exits during commercial breaks.

See [docs/detection-system-overview.md](docs/detection-system-overview.md) for the full technical deep-dive.

## Quick Install

On a Raspberry Pi (or any Debian/Ubuntu system):

```bash
curl -sSL https://raw.githubusercontent.com/intrepidsilence/CommercialDetector/main/install.sh | bash
```

This installs system dependencies, clones the repo, creates a Python virtual environment, installs Python packages, and optionally sets up a systemd service.

## Manual Installation

### Prerequisites

- Python 3.11+
- FFmpeg (with silencedetect, blackdetect, scdet, ebur128 filters — included in standard builds)
- Mosquitto MQTT broker
- A USB HDMI capture device (for live use)

### System Dependencies

```bash
# Debian / Ubuntu / Raspberry Pi OS
sudo apt update
sudo apt install -y ffmpeg mosquitto mosquitto-clients python3 python3-pip python3-venv

# macOS (for development/testing)
brew install ffmpeg mosquitto python@3.11
```

### Clone and Install

```bash
git clone https://github.com/intrepidsilence/CommercialDetector.git
cd CommercialDetector

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Optional: Whisper Transcript Analysis

Enables speech-based detection for faster exit latency. Adds ~400 MB memory usage.

```bash
pip install faster-whisper numpy
```

Then enable in `config.yaml`:

```yaml
transcript:
  enabled: true
```

## Usage

### Live Capture (Default)

```bash
# Start detection with default config
python -m commercial_detector

# Monitor MQTT output in another terminal
mosquitto_sub -t "commercial-detector/#" -v
```

### Test with a Recorded File

```bash
python -m commercial_detector --input recording.mp4 --dry-run
```

### Options

| Flag | Description |
|------|-------------|
| `--input PATH` | Use a video file instead of live capture |
| `--config PATH` | Custom config file (default: `./config.yaml`) |
| `--dry-run` | Log state changes without connecting to MQTT |
| `--log-level LEVEL` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

## MQTT Topics

| Topic | Retained | Description |
|-------|----------|-------------|
| `commercial-detector/state` | Yes | Current state: `program`, `commercial`, or `unknown` |
| `commercial-detector/state/changed` | No | Transition event with timestamp, confidence, and trigger signal |
| `commercial-detector/health` | No | Heartbeat every 30s with timestamp and current state |

### Example: Transition Event

```json
{
  "timestamp": 354.9,
  "from": "program",
  "to": "commercial",
  "confidence": 0.713,
  "signals": {
    "commercial_score": 11.41,
    "trigger_signal": "black_start"
  }
}
```

## Configuration

All detection parameters are tunable in `config.yaml`. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `engine.score_decay_rate` | 0.6 | Score decay speed (pts/sec) |
| `engine.commercial_threshold` | 8.0 | Score to trigger COMMERCIAL |
| `engine.program_threshold` | -3.0 | Score to trigger PROGRAM |
| `engine.max_score` | 15.0 | Score ceiling clamp |
| `signal_source.silence_noise_db` | -42.0 | Silence detection floor (dB) |
| `transcript.enabled` | false | Enable Whisper transcript analysis |

See [config.yaml](config.yaml) for the complete reference with comments.

## Running as a Service

### Automatic Setup

The installer can set up everything including the systemd service and log directory:

```bash
curl -sSL https://raw.githubusercontent.com/intrepidsilence/CommercialDetector/main/install.sh | \
  SETUP_SERVICE=1 bash
```

For the full stack (Whisper + service):

```bash
curl -sSL https://raw.githubusercontent.com/intrepidsilence/CommercialDetector/main/install.sh | \
  ENABLE_WHISPER=1 SETUP_SERVICE=1 bash
```

### Manual Setup

```bash
# Create log directory
sudo mkdir -p /var/log/commercial-detector
sudo chown pi:pi /var/log/commercial-detector

# Create logrotate config
sudo tee /etc/logrotate.d/commercial-detector << 'EOF'
/var/log/commercial-detector/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF

# Create systemd service
sudo tee /etc/systemd/system/commercial-detector.service << 'EOF'
[Unit]
Description=CommercialDetector - TV commercial break detection
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/commercial-detector
ExecStart=/opt/commercial-detector/.venv/bin/python -m commercial_detector
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/commercial-detector/output.log
StandardError=append:/var/log/commercial-detector/error.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now commercial-detector
```

### Service Management

```bash
# Start / stop / restart
sudo systemctl start commercial-detector
sudo systemctl stop commercial-detector
sudo systemctl restart commercial-detector

# Check status
sudo systemctl status commercial-detector

# Tail logs
tail -f /var/log/commercial-detector/output.log
tail -f /var/log/commercial-detector/error.log
```

Logs are rotated daily (7 days retained, compressed) via logrotate.

## Running Tests

```bash
python -m pytest tests/ -v
```

94 tests, runs in ~0.1 seconds. No external services required.

## Hardware

### Recommended

- **Raspberry Pi 5 (4 GB)** — ~580 MB total with Whisper enabled
- USB 3.0 HDMI capture device
- Active cooling (fan or heatsink) for sustained operation

### Minimum

- Any ARM SBC with 2+ GB RAM (without Whisper)
- USB HDMI capture device with V4L2 support

## Project Structure

```
commercial_detector/
  config.py              # Configuration dataclasses + YAML loader
  detection_engine.py    # Continuous scoring engine with decay
  main.py                # Orchestrator — wires signal source → engine → MQTT
  models.py              # Shared data models (signals, states, transitions)
  mqtt_publisher.py      # paho-mqtt v2.x publisher
  signal_source.py       # FFmpeg stderr parser (silencedetect + blackdetect + scdet + ebur128)
  transcript_analyzer.py # Optional Whisper-based keyword scoring
docs/
  detection-system-overview.md   # Full technical documentation
  detection-system-overview.pdf  # PDF version
tests/
  test_detection_engine.py       # 36 tests — scoring, transitions, coincidence
  test_signal_source.py          # 19 tests — FFmpeg line parsing
  test_transcript_analyzer.py    # 22 tests — keyword scoring
  test_mqtt_publisher.py         # 17 tests — MQTT publishing
```

## License

Private repository. All rights reserved.
