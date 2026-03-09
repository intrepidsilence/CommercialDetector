# Web Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Flask + HTMX + SSE web dashboard for live monitoring, signal visualization, configuration editing, and system health — running in-process on the RPi with ~15 MB additional memory.

**Architecture:** Flask app runs on a background thread sharing state with the detection engine via a thread-safe `WebStateManager`. SSE pushes live updates to browsers. HTMX handles page interactions without a JS build toolchain. uPlot renders the score timeline chart.

**Tech Stack:** Flask 3.x, HTMX 2.x (CDN), uPlot (CDN), Jinja2 templates, Server-Sent Events, custom dark CSS.

---

### Task 1: WebStateManager — Thread-Safe State Bridge

**Files:**
- Create: `commercial_detector/web/__init__.py`
- Create: `commercial_detector/web/state_manager.py`
- Test: `tests/test_web_state_manager.py`

**Step 1: Write the failing tests**

```python
# tests/test_web_state_manager.py
"""Tests for the WebStateManager — thread-safe bridge between engine and web."""

import threading
import time
from queue import Queue, Empty

import pytest

from commercial_detector.models import (
    DetectionSignal,
    DetectionState,
    SignalType,
    StateTransition,
)
from commercial_detector.web.state_manager import WebStateManager


class TestPushSignal:
    def test_push_and_retrieve_signal(self):
        mgr = WebStateManager()
        sig = DetectionSignal(timestamp=10.0, signal_type=SignalType.SILENCE_END, value=1.5)
        mgr.push_signal(sig)
        signals = mgr.get_signals(limit=10)
        assert len(signals) == 1
        assert signals[0]["type"] == "silence_end"
        assert signals[0]["timestamp"] == 10.0

    def test_signal_ring_buffer_limit(self):
        mgr = WebStateManager(max_signals=5)
        for i in range(10):
            mgr.push_signal(
                DetectionSignal(timestamp=float(i), signal_type=SignalType.SCENE_CHANGE)
            )
        signals = mgr.get_signals(limit=100)
        assert len(signals) == 5
        # Oldest should be dropped
        assert signals[0]["timestamp"] == 5.0

    def test_signal_counts_increment(self):
        mgr = WebStateManager()
        mgr.push_signal(
            DetectionSignal(timestamp=1.0, signal_type=SignalType.SILENCE_END)
        )
        mgr.push_signal(
            DetectionSignal(timestamp=2.0, signal_type=SignalType.SILENCE_END)
        )
        mgr.push_signal(
            DetectionSignal(timestamp=3.0, signal_type=SignalType.BLACK_START)
        )
        snapshot = mgr.get_snapshot()
        assert snapshot["signal_counts"]["silence_end"] == 2
        assert snapshot["signal_counts"]["black_start"] == 1


class TestPushTransition:
    def test_push_and_retrieve_transition(self):
        mgr = WebStateManager()
        tr = StateTransition(
            timestamp=100.0,
            from_state=DetectionState.UNKNOWN,
            to_state=DetectionState.COMMERCIAL,
            confidence=0.8,
            signals={"trigger_signal": "black_start"},
        )
        mgr.push_transition(tr)
        transitions = mgr.get_transitions()
        assert len(transitions) == 1
        assert transitions[0]["to"] == "commercial"

    def test_transition_updates_current_state(self):
        mgr = WebStateManager()
        tr = StateTransition(
            timestamp=100.0,
            from_state=DetectionState.UNKNOWN,
            to_state=DetectionState.COMMERCIAL,
            confidence=0.8,
        )
        mgr.push_transition(tr)
        assert mgr.get_snapshot()["state"] == "commercial"


class TestUpdateScore:
    def test_score_in_snapshot(self):
        mgr = WebStateManager()
        mgr.update_score(12.5, timestamp=50.0)
        snapshot = mgr.get_snapshot()
        assert snapshot["score"] == 12.5

    def test_score_history_accumulates(self):
        mgr = WebStateManager()
        mgr.update_score(5.0, timestamp=1.0)
        mgr.update_score(10.0, timestamp=2.0)
        history = mgr.get_score_history()
        assert len(history) == 2
        assert history[0] == [1.0, 5.0]
        assert history[1] == [2.0, 10.0]

    def test_score_history_ring_buffer(self):
        mgr = WebStateManager(max_score_history=3)
        for i in range(5):
            mgr.update_score(float(i), timestamp=float(i))
        history = mgr.get_score_history()
        assert len(history) == 3
        assert history[0] == [2.0, 2.0]


class TestSSESubscription:
    def test_subscribe_receives_events(self):
        mgr = WebStateManager()
        q = mgr.subscribe()
        mgr.push_signal(
            DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START)
        )
        event = q.get(timeout=1)
        assert event["event"] == "signal"

    def test_unsubscribe_removes_queue(self):
        mgr = WebStateManager()
        q = mgr.subscribe()
        mgr.unsubscribe(q)
        mgr.push_signal(
            DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START)
        )
        with pytest.raises(Empty):
            q.get(timeout=0.1)

    def test_multiple_subscribers(self):
        mgr = WebStateManager()
        q1 = mgr.subscribe()
        q2 = mgr.subscribe()
        mgr.push_signal(
            DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START)
        )
        assert not q1.empty()
        assert not q2.empty()


class TestThreadSafety:
    def test_concurrent_pushes(self):
        mgr = WebStateManager()
        errors = []

        def push_signals():
            try:
                for i in range(100):
                    mgr.push_signal(
                        DetectionSignal(
                            timestamp=float(i), signal_type=SignalType.SCENE_CHANGE
                        )
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=push_signals) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        snapshot = mgr.get_snapshot()
        assert snapshot["signal_counts"]["scene_change"] == 400


class TestGetSnapshot:
    def test_default_snapshot(self):
        mgr = WebStateManager()
        snapshot = mgr.get_snapshot()
        assert snapshot["state"] == "unknown"
        assert snapshot["score"] == 0.0
        assert snapshot["signal_counts"] == {}
        assert "uptime" in snapshot
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_state_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'commercial_detector.web'`

**Step 3: Write the implementation**

```python
# commercial_detector/web/__init__.py
"""Web dashboard for CommercialDetector."""

# commercial_detector/web/state_manager.py
"""Thread-safe shared state between the detection engine and the web dashboard.

Ring buffers hold recent signals, transitions, and score history.
SSE subscribers receive real-time push events via Queue.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from queue import Queue
from typing import Any

from commercial_detector.models import (
    DetectionSignal,
    DetectionState,
    StateTransition,
)


class WebStateManager:
    """Thread-safe bridge between the detection engine and the web layer."""

    def __init__(
        self,
        max_signals: int = 500,
        max_transitions: int = 100,
        max_score_history: int = 1800,  # 30 min at 1 sample/sec
    ) -> None:
        self._lock = threading.Lock()
        self._signals: deque[dict] = deque(maxlen=max_signals)
        self._transitions: deque[dict] = deque(maxlen=max_transitions)
        self._score_history: deque[list] = deque(maxlen=max_score_history)
        self._signal_counts: dict[str, int] = {}
        self._current_state: str = "unknown"
        self._current_score: float = 0.0
        self._start_time: float = time.monotonic()
        self._subscribers: list[Queue] = []

    def push_signal(self, signal: DetectionSignal) -> None:
        """Record a detection signal and notify SSE subscribers."""
        entry = {
            "type": signal.signal_type.value,
            "timestamp": signal.timestamp,
            "value": signal.value,
        }
        with self._lock:
            self._signals.append(entry)
            self._signal_counts[signal.signal_type.value] = (
                self._signal_counts.get(signal.signal_type.value, 0) + 1
            )
            self._broadcast({"event": "signal", "data": entry})

    def push_transition(self, transition: StateTransition) -> None:
        """Record a state transition and notify SSE subscribers."""
        entry = transition.to_dict()
        with self._lock:
            self._transitions.append(entry)
            self._current_state = transition.to_state.value
            self._broadcast({"event": "transition", "data": entry})

    def update_score(self, score: float, timestamp: float) -> None:
        """Update the current score and append to history."""
        with self._lock:
            self._current_score = score
            self._score_history.append([timestamp, score])

    def get_snapshot(self) -> dict:
        """Return the current dashboard state."""
        with self._lock:
            return {
                "state": self._current_state,
                "score": self._current_score,
                "signal_counts": dict(self._signal_counts),
                "transition_count": len(self._transitions),
                "uptime": time.monotonic() - self._start_time,
            }

    def get_signals(self, limit: int = 50) -> list[dict]:
        """Return recent signals (newest first up to limit)."""
        with self._lock:
            items = list(self._signals)
        return items[-limit:] if limit < len(items) else items

    def get_transitions(self) -> list[dict]:
        """Return all recorded transitions."""
        with self._lock:
            return list(self._transitions)

    def get_score_history(self) -> list[list]:
        """Return score history as [[timestamp, score], ...]."""
        with self._lock:
            return list(self._score_history)

    def subscribe(self) -> Queue:
        """Add an SSE subscriber. Returns a Queue that receives events."""
        q: Queue = Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        """Remove an SSE subscriber."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict[str, Any]) -> None:
        """Push event to all subscribers. Drops if subscriber queue is full.
        Must be called while holding self._lock.
        """
        dead: list[Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_state_manager.py -v`
Expected: All 13 tests PASS

**Step 5: Commit**

```bash
git add commercial_detector/web/__init__.py commercial_detector/web/state_manager.py tests/test_web_state_manager.py
git commit -m "feat(web): add WebStateManager — thread-safe state bridge"
```

---

### Task 2: WebConfig + Flask Dependency

**Files:**
- Modify: `commercial_detector/config.py:120-128` (add WebConfig to AppConfig)
- Modify: `config.yaml` (add web section)
- Modify: `requirements.txt` (add flask)

**Step 1: Add WebConfig dataclass**

Add after `TranscriptConfig` in `config.py`:

```python
@dataclass
class WebConfig:
    """Web dashboard settings."""
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
```

Add `web` field to `AppConfig`:

```python
@dataclass
class AppConfig:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    signal_source: SignalSourceConfig = field(default_factory=SignalSourceConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    transcript: TranscriptConfig = field(default_factory=TranscriptConfig)
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"
```

**Step 2: Add web section to config.yaml**

```yaml
# Web dashboard (runs on port 8080)
web:
  enabled: true
  host: "0.0.0.0"
  port: 8080
```

**Step 3: Add flask to requirements.txt**

```
flask>=3.0
```

**Step 4: Install flask**

Run: `pip install flask>=3.0`

**Step 5: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All 94 tests PASS

**Step 6: Commit**

```bash
git add commercial_detector/config.py config.yaml requirements.txt
git commit -m "feat(web): add WebConfig and flask dependency"
```

---

### Task 3: Flask App + SSE Endpoint + REST API

**Files:**
- Create: `commercial_detector/web/server.py`
- Test: `tests/test_web_server.py`

**Step 1: Write the failing tests**

```python
# tests/test_web_server.py
"""Tests for the Flask web server routes and SSE endpoint."""

import json
import pytest

from commercial_detector.config import AppConfig
from commercial_detector.web.state_manager import WebStateManager
from commercial_detector.web.server import create_app
from commercial_detector.models import (
    DetectionSignal,
    DetectionState,
    SignalType,
    StateTransition,
)


@pytest.fixture
def state_manager():
    return WebStateManager()


@pytest.fixture
def app(state_manager):
    config = AppConfig()
    return create_app(config, state_manager)


@pytest.fixture
def client(app):
    return app.test_client()


class TestDashboardRoutes:
    def test_root_redirects_to_dashboard(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["Location"]

    def test_dashboard_page(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b"CommercialDetector" in resp.data

    def test_signals_page(self, client):
        resp = client.get("/signals")
        assert resp.status_code == 200

    def test_history_page(self, client):
        resp = client.get("/history")
        assert resp.status_code == 200

    def test_config_page(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200

    def test_system_page(self, client):
        resp = client.get("/system")
        assert resp.status_code == 200


class TestRESTEndpoints:
    def test_api_snapshot(self, client):
        resp = client.get("/api/snapshot")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "state" in data
        assert "score" in data

    def test_api_signals(self, client, state_manager):
        state_manager.push_signal(
            DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START)
        )
        resp = client.get("/api/signals")
        data = json.loads(resp.data)
        assert len(data) == 1

    def test_api_transitions(self, client, state_manager):
        state_manager.push_transition(
            StateTransition(
                timestamp=100.0,
                from_state=DetectionState.UNKNOWN,
                to_state=DetectionState.COMMERCIAL,
            )
        )
        resp = client.get("/api/transitions")
        data = json.loads(resp.data)
        assert len(data) == 1

    def test_api_score_history(self, client, state_manager):
        state_manager.update_score(5.0, timestamp=1.0)
        resp = client.get("/api/score-history")
        data = json.loads(resp.data)
        assert len(data) == 1

    def test_api_config_get(self, client):
        resp = client.get("/api/config")
        data = json.loads(resp.data)
        assert "engine" in data
        assert "signal_source" in data

    def test_api_config_post(self, client):
        resp = client.post(
            "/api/config",
            data=json.dumps({"engine": {"silence_weight": 6.0}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_server.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_app'`

**Step 3: Write the implementation**

```python
# commercial_detector/web/server.py
"""Flask web server — routes, REST API, and SSE endpoint.

Creates a Flask app with:
- Page routes (dashboard, signals, history, config, system)
- REST API (/api/*)
- SSE endpoint for live browser updates
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from commercial_detector.config import AppConfig, _apply_dict
from commercial_detector.web.state_manager import WebStateManager

logger = logging.getLogger(__name__)

# Template and static dirs relative to this file
_WEB_DIR = Path(__file__).parent
_TEMPLATE_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(
    config: AppConfig,
    state_manager: WebStateManager,
    config_path: Optional[str] = None,
) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATE_DIR),
        static_folder=str(_STATIC_DIR),
    )
    app.config["PROPAGATE_EXCEPTIONS"] = True

    # Store references for route handlers
    app.extensions["cd_config"] = config
    app.extensions["cd_state"] = state_manager
    app.extensions["cd_config_path"] = config_path

    # --- Page routes ----------------------------------------------------------

    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        snapshot = state_manager.get_snapshot()
        return render_template("dashboard.html", page="dashboard", **snapshot)

    @app.route("/signals")
    def signals_page():
        return render_template("signals.html", page="signals")

    @app.route("/history")
    def history_page():
        transitions = state_manager.get_transitions()
        return render_template("history.html", page="history", transitions=transitions)

    @app.route("/config")
    def config_page():
        cfg = _config_to_dict(config)
        return render_template("config.html", page="config", config=cfg)

    @app.route("/system")
    def system_page():
        return render_template("system.html", page="system")

    # --- REST API -------------------------------------------------------------

    @app.route("/api/snapshot")
    def api_snapshot():
        return jsonify(state_manager.get_snapshot())

    @app.route("/api/signals")
    def api_signals():
        limit = request.args.get("limit", 50, type=int)
        return jsonify(state_manager.get_signals(limit=limit))

    @app.route("/api/transitions")
    def api_transitions():
        return jsonify(state_manager.get_transitions())

    @app.route("/api/score-history")
    def api_score_history():
        return jsonify(state_manager.get_score_history())

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        return jsonify(_config_to_dict(config))

    @app.route("/api/config", methods=["POST"])
    def api_config_post():
        updates = request.get_json(force=True)
        try:
            _apply_dict(config, updates)
            # Persist to disk if we know the config path
            if config_path:
                _save_config(config, config_path)
            return jsonify({"status": "ok"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.route("/api/system")
    def api_system():
        return jsonify(_get_system_info())

    # --- SSE ------------------------------------------------------------------

    @app.route("/api/events")
    def api_events():
        def stream():
            q = state_manager.subscribe()
            try:
                while True:
                    try:
                        event = q.get(timeout=1)
                        yield f"event: {event['event']}\ndata: {json.dumps(event.get('data', {}))}\n\n"
                    except Exception:
                        # Timeout — send keepalive comment
                        yield ": keepalive\n\n"
            finally:
                state_manager.unsubscribe(q)

        return Response(stream(), mimetype="text/event-stream")

    return app


def _config_to_dict(config: AppConfig) -> dict:
    """Serialize config to a dict, excluding internal/deprecated fields."""
    d = asdict(config)
    # Remove deprecated fields
    for key in ("silence_gap_window", "commercial_gap_count", "program_gap_timeout"):
        d.get("engine", {}).pop(key, None)
    return d


def _save_config(config: AppConfig, path: str) -> None:
    """Write the current config back to YAML."""
    import yaml
    d = _config_to_dict(config)
    with open(path, "w") as f:
        yaml.dump(d, f, default_flow_style=False, sort_keys=False)


def _get_system_info() -> dict:
    """Gather system health metrics."""
    import os
    info: dict = {}

    # CPU temperature (Linux — RPi thermal zone)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp_c"] = int(f.read().strip()) / 1000.0
    except (FileNotFoundError, ValueError):
        info["cpu_temp_c"] = None

    # Memory
    try:
        import resource
        usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS returns bytes, Linux returns KB
        if os.uname().sysname == "Darwin":
            info["memory_mb"] = round(usage_kb / (1024 * 1024), 1)
        else:
            info["memory_mb"] = round(usage_kb / 1024, 1)
    except Exception:
        info["memory_mb"] = None

    # Load average
    try:
        load = os.getloadavg()
        info["load_avg"] = [round(x, 2) for x in load]
    except OSError:
        info["load_avg"] = None

    return info


def start_web_server(
    config: AppConfig,
    state_manager: WebStateManager,
    config_path: Optional[str] = None,
) -> None:
    """Start the Flask server on a background thread (non-blocking)."""
    import threading

    app = create_app(config, state_manager, config_path)
    web_cfg = config.web

    # Suppress Flask/Werkzeug request logs in production
    werkzeug_log = logging.getLogger("werkzeug")
    werkzeug_log.setLevel(logging.WARNING)

    thread = threading.Thread(
        target=app.run,
        kwargs={
            "host": web_cfg.host,
            "port": web_cfg.port,
            "threaded": True,
            "use_reloader": False,
        },
        name="web-dashboard",
        daemon=True,
    )
    thread.start()
    logger.info("Web dashboard started at http://%s:%d", web_cfg.host, web_cfg.port)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_server.py -v`
Expected: All 12 tests PASS (some will need templates to exist — create placeholder templates first, see Task 4)

**Step 5: Commit**

```bash
git add commercial_detector/web/server.py tests/test_web_server.py
git commit -m "feat(web): add Flask app with routes, REST API, and SSE"
```

---

### Task 4: HTML Templates + Static Assets

**Files:**
- Create: `commercial_detector/web/templates/base.html`
- Create: `commercial_detector/web/templates/dashboard.html`
- Create: `commercial_detector/web/templates/signals.html`
- Create: `commercial_detector/web/templates/history.html`
- Create: `commercial_detector/web/templates/config.html`
- Create: `commercial_detector/web/templates/system.html`
- Create: `commercial_detector/web/static/style.css`
- Create: `commercial_detector/web/static/app.js`

This is the largest task — all the UI. The plan provides the complete template code below. The implementer should create all files and then visually verify in a browser.

**Step 1: Create `base.html` — layout with sidebar nav and dark theme**

The base template defines:
- Sidebar navigation with links to all pages
- Main content area
- HTMX and uPlot loaded from CDN
- SSE connection established globally
- Dark theme via custom CSS

**Step 2: Create `dashboard.html` — live status with score chart**

Dashboard shows:
- Large colored state badge (PROGRAM=green, COMMERCIAL=red, UNKNOWN=amber)
- Score gauge bar (-10 to +15 range)
- uPlot score timeline chart (live-updating via SSE)
- Transition markers on the chart
- Signal counter cards (per type)
- Uptime counter

All live elements use `hx-ext="sse"` with `sse-connect="/api/events"` for real-time updates.

**Step 3: Create remaining page templates**

- `signals.html`: Auto-scrolling table, color-coded rows, pause button
- `history.html`: Transition table with colored state badges
- `config.html`: Grouped form with descriptions, save/reset buttons
- `system.html`: Cards for CPU temp, memory, load, FFmpeg status

**Step 4: Create `style.css` — dark theme**

Custom dark CSS with:
- `--bg: #0f1117`, `--surface: #1a1d27`, `--border: #2a2d3a`
- `--text: #e1e4ed`, `--text-dim: #8b8fa3`
- `--green: #22c55e`, `--red: #ef4444`, `--amber: #f59e0b`, `--blue: #3b82f6`
- Card components, table styles, form inputs, badges
- CSS transitions for smooth state changes
- Responsive breakpoints for mobile

**Step 5: Create `app.js` — SSE handling and chart initialization**

JavaScript handles:
- SSE EventSource connection with auto-reconnect
- uPlot chart initialization and live data append
- Dashboard element updates on `state` events
- Signal table row prepend on `signal` events
- Config form submission via fetch POST
- System health polling (every 5s via HTMX)

**Step 6: Verify in browser**

Run: `python -c "from commercial_detector.web.server import create_app; from commercial_detector.config import AppConfig; from commercial_detector.web.state_manager import WebStateManager; app = create_app(AppConfig(), WebStateManager()); app.run(port=8080, debug=True)"`
Open: `http://localhost:8080/dashboard`
Expected: Dark-themed dashboard renders with empty state

**Step 7: Commit**

```bash
git add commercial_detector/web/templates/ commercial_detector/web/static/
git commit -m "feat(web): add HTML templates and dark theme CSS"
```

---

### Task 5: Integrate WebStateManager Into Main Loop

**Files:**
- Modify: `commercial_detector/main.py`

**Step 1: Import and wire up the web layer**

Add to `main.py`:
- Import `WebStateManager` and `start_web_server`
- Create `WebStateManager` instance
- Start Flask on background thread if `config.web.enabled`
- After `engine.process(signal)`: call `state_manager.push_signal(signal)` and `state_manager.update_score(engine._state.commercial_score, signal.timestamp)`
- After transition detected: call `state_manager.push_transition(transition)`

The key integration points in the main loop:

```python
# After line: transition = engine.process(detection_signal)
if web_state is not None:
    web_state.push_signal(detection_signal)
    web_state.update_score(
        engine._state.commercial_score, detection_signal.timestamp
    )

# Inside _publish_transition:
if web_state is not None:
    web_state.push_transition(transition)
```

**Step 2: Add `--no-web` CLI flag**

Add argument to parser:
```python
parser.add_argument(
    "--no-web",
    action="store_true",
    help="Disable the web dashboard.",
)
```

**Step 3: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (94 original + 13 state_manager + 12 server = ~119)

**Step 4: Integration test with test video**

Run: `python -m commercial_detector --input /Users/spurlock/Downloads/"S2E27 Pulse Of The Pack.webm" --dry-run`
Open: `http://localhost:8080/dashboard`
Expected: Dashboard shows live score updates, state transitions appear in real-time

**Step 5: Commit**

```bash
git add commercial_detector/main.py
git commit -m "feat(web): integrate dashboard into main detection loop"
```

---

### Task 6: System Health Endpoint

**Files:**
- Modify: `commercial_detector/web/server.py` (already has `_get_system_info`, may need enhancement)

**Step 1: Enhance system info**

Add to `_get_system_info()`:
- FFmpeg process status: check if the signal source PID is alive
- MQTT connection status: check publisher connected state
- Whisper status: check if transcript analyzer is running
- Disk usage for the install directory

These require passing component references. Add them to `app.extensions` in `create_app()`:

```python
app.extensions["cd_components"] = {
    "publisher": publisher_ref,
    "transcript": transcript_ref,
}
```

**Step 2: Commit**

```bash
git add commercial_detector/web/server.py
git commit -m "feat(web): enhanced system health with component status"
```

---

### Task 7: Update README, install.sh, and Push

**Files:**
- Modify: `README.md` (add web dashboard section)
- Modify: `install.sh` (flask is now in requirements.txt, auto-installed)

**Step 1: Add dashboard section to README**

```markdown
## Web Dashboard

The built-in dashboard runs on port 8080 and provides:
- Live detection state and score visualization
- Real-time signal log
- Transition history
- Configuration editor (hot-reload engine parameters)
- System health monitoring

Access at `http://<pi-ip>:8080` after starting the detector.

Disable with `--no-web` flag or `web.enabled: false` in config.
```

**Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 3: Commit and push**

```bash
git add README.md install.sh
git commit -m "docs: add web dashboard to README"
git push
```

---

## Task Dependency Graph

```
Task 1 (WebStateManager) ──► Task 3 (Flask server) ──► Task 5 (Integration)
                                      │
Task 2 (WebConfig + deps)  ───────────┘
                                                        Task 5 ──► Task 7 (Docs)
Task 4 (Templates + CSS) ──► Task 5 (Integration) ──► Task 6 (System health)
```

Tasks 1, 2, and 4 can be worked in parallel. Task 3 depends on 1 and 2. Task 5 depends on 2, 3, and 4. Tasks 6 and 7 depend on 5.
