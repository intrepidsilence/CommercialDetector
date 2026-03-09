# Web Dashboard Design

**Date**: 2026-03-09
**Status**: Approved
**Target**: Raspberry Pi 5 4GB (~580 MB already used by detection + Whisper)

## Goal

Add a web-based dashboard to CommercialDetector that provides live monitoring, signal visualization, configuration editing, and system health — all running on the same RPi process with minimal additional memory (~15-25 MB).

## Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Backend | Flask | ~15 MB memory, mature, simple routing |
| Live updates | Server-Sent Events (SSE) | One-directional push, no WebSocket overhead |
| Frontend interactivity | HTMX | Modern UX without JS build toolchain |
| Charts | uPlot (~30 KB) | Fastest/lightest time-series chart library |
| Styling | Custom dark CSS + minimal Tailwind utility classes via CDN | Modern look, no build step |
| Templates | Jinja2 (built into Flask) | Server-rendered, cacheable |

## Architecture

### Process Model

The web server runs **in the same Python process** as the detection engine, on a background thread managed by Flask's Werkzeug server. No IPC, no database — just a thread-safe shared state object.

```
main.py (orchestrator)
    │
    ├── DetectionEngine ──────► WebStateManager (thread-safe)
    │                                │
    │                                ├─► SSE /api/events stream
    │                                ├─► REST /api/* endpoints
    │                                └─► Jinja2 templates
    │
    └── Flask app (background thread, port 8080)
         ├── templates/
         │   ├── base.html
         │   ├── dashboard.html
         │   ├── signals.html
         │   ├── config.html
         │   ├── history.html
         │   └── system.html
         └── static/
             ├── style.css
             └── app.js
```

### WebStateManager

Central bridge between the detection engine and the web layer. Thread-safe via `threading.Lock`.

**Responsibilities:**
- Holds a ring buffer of recent signals (last 500)
- Holds a ring buffer of recent transitions (last 100)
- Holds current score, state, uptime, signal counts
- Holds score history for the timeline chart (last 30 min at 1 sample/sec)
- Provides a subscriber list for SSE — new events are pushed to all connected browsers

**Interface:**
```python
class WebStateManager:
    def push_signal(self, signal: DetectionSignal) -> None
    def push_transition(self, transition: StateTransition) -> None
    def update_score(self, score: float, timestamp: float) -> None
    def get_snapshot(self) -> dict          # Current state for dashboard
    def get_signals(self, limit=50) -> list  # Recent signals
    def get_transitions(self) -> list        # All transitions
    def get_score_history(self) -> list      # For chart
    def subscribe(self) -> Queue             # SSE subscriber
    def unsubscribe(self, queue: Queue) -> None
```

### SSE Event Types

| Event | Data | Trigger |
|-------|------|---------|
| `state` | `{state, score, uptime, signal_counts}` | Every 1s tick |
| `signal` | `{type, timestamp, value, weight}` | Each detection signal |
| `transition` | `{from, to, timestamp, confidence, trigger}` | State change |
| `health` | `{cpu_temp, memory_mb, ffmpeg_alive, mqtt_connected}` | Every 5s |

### REST Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Dashboard (redirect) |
| GET | `/dashboard` | Live status page |
| GET | `/signals` | Signal log page |
| GET | `/history` | Transition history page |
| GET | `/config` | Configuration editor page |
| GET | `/system` | System health page |
| GET | `/api/events` | SSE event stream |
| GET | `/api/snapshot` | Current state JSON |
| GET | `/api/signals` | Recent signals JSON |
| GET | `/api/transitions` | Transition history JSON |
| GET | `/api/score-history` | Score timeline data |
| GET | `/api/config` | Current config as JSON |
| POST | `/api/config` | Update config (validates + applies) |
| POST | `/api/config/reload` | Reload config from disk |

### Configuration Editor

The config editor loads the current `config.yaml` as a structured form (not raw YAML editing). Each section is collapsible. Changes are validated server-side before writing to disk.

**Hot-reload strategy:** Engine parameters (weights, thresholds, decay) can be updated live by mutating the shared `EngineConfig` dataclass. FFmpeg filter parameters require a pipeline restart (flagged in the UI).

## Pages

### Dashboard (main page)
- Large state indicator (PROGRAM / COMMERCIAL / UNKNOWN) with color
- Real-time score gauge (-10 to +15 range, color-coded)
- Score timeline chart (last 30 min, live-updating via SSE)
- State transition markers on the chart
- Signal counters (per-type, since startup)
- Uptime counter

### Signal Log
- Live-scrolling table: timestamp, signal type, value, score contribution
- Color-coded by signal type
- Auto-scroll with pause button
- Filterable by signal type

### Transition History
- Table: timestamp, from-state, to-state, confidence, trigger signal, duration-in-state
- Color-coded rows (green=program, red=commercial)
- Entry/exit latency if ground truth is available

### Configuration
- Grouped form sections matching config.yaml structure
- Each field has label, current value, input, description
- "Requires restart" badge on FFmpeg filter params
- Save button with validation feedback
- Reset-to-defaults button

### System Health
- CPU temperature (from `/sys/class/thermal/thermal_zone0/temp`)
- Memory usage (from `/proc/meminfo` or `psutil`)
- FFmpeg process status (PID, running/stopped)
- MQTT connection status
- Whisper status (enabled/disabled, model loaded)
- Disk usage

## Visual Design

- Dark theme (dark gray background, light text)
- Accent colors: green for PROGRAM, red for COMMERCIAL, amber for UNKNOWN
- Monospace font for data values
- Card-based layout with subtle borders
- Responsive — usable on mobile for quick checks
- Smooth transitions on state changes (CSS animations)

## File Structure

```
commercial_detector/
  web/
    __init__.py
    server.py           # Flask app factory, routes, SSE endpoint
    state_manager.py    # Thread-safe shared state + ring buffers
    config_api.py       # Config read/write/validate endpoints
    system_info.py      # CPU temp, memory, process status helpers
    templates/
      base.html         # Layout: nav sidebar + main content area
      dashboard.html    # Live dashboard with score chart
      signals.html      # Signal log table
      history.html      # Transition history
      config.html       # Configuration editor form
      system.html       # System health
    static/
      style.css         # Dark theme styles
      app.js            # HTMX config, uPlot chart init, SSE handling
```

## Integration Points

### main.py changes
- Import and start Flask app on background thread
- Hook WebStateManager into the signal processing loop:
  - After `engine.process(signal)` → `state_manager.push_signal(signal)`
  - After transition detected → `state_manager.push_transition(transition)`
  - Periodic score update → `state_manager.update_score(score, pts)`

### config.py changes
- Add `WebConfig` dataclass (enabled, host, port)
- Add `web:` section to config.yaml

### requirements.txt changes
- Add `flask>=3.0`

## Memory Budget

| Component | Estimate |
|-----------|----------|
| Flask + Werkzeug | ~10 MB |
| Ring buffers (500 signals + 100 transitions + 1800 score samples) | ~2 MB |
| Jinja2 template cache | ~1 MB |
| SSE subscriber queues (1-3 browsers) | ~1 MB |
| Static assets (CSS + JS served from disk) | negligible |
| **Total** | **~15 MB** |

Current system: ~580 MB with Whisper. After web dashboard: ~595 MB. Well within 4 GB.

## Non-Goals

- No authentication (LAN-only device)
- No database (all state is in-memory, ephemeral)
- No video preview/streaming (too expensive for RPi)
- No multi-user collaboration
