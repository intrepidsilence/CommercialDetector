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
from commercial_detector.device_discovery import list_audio_devices, list_video_devices
from commercial_detector.web.state_manager import WebStateManager

logger = logging.getLogger(__name__)

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

    app.extensions["cd_config"] = config
    app.extensions["cd_state"] = state_manager
    app.extensions["cd_config_path"] = config_path

    @app.context_processor
    def inject_globals():
        return {"whisper_enabled": config.transcript.enabled}

    # --- Page routes ----------------------------------------------------------

    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        snapshot = state_manager.get_snapshot()
        signals = state_manager.get_signals(limit=50)
        return render_template("dashboard.html", page="dashboard", signals=signals, **snapshot)

    @app.route("/history")
    def history_page():
        transitions = state_manager.get_transitions()
        return render_template("history.html", page="history", transitions=transitions)

    @app.route("/config")
    def config_page():
        cfg = _config_to_dict(config)
        return render_template("config.html", page="config", config=cfg)

    # --- REST API -------------------------------------------------------------

    @app.route("/api/snapshot")
    def api_snapshot():
        snapshot = state_manager.get_snapshot()
        components = app.extensions.get("cd_components", {})
        publisher = components.get("publisher")
        if publisher is not None:
            snapshot["mqtt"] = publisher.status
        else:
            snapshot["mqtt"] = {"connected": False, "broker": None, "error": "disabled (dry-run)"}
        return jsonify(snapshot)

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
            if config_path:
                _save_config(config, config_path)
            return jsonify({"status": "ok"})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.route("/api/devices")
    def api_devices():
        return jsonify({
            "video": list_video_devices(),
            "audio": list_audio_devices(),
        })

    @app.route("/api/system")
    def api_system():
        return jsonify(_get_system_info(app))

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
                        yield ": keepalive\n\n"
            finally:
                state_manager.unsubscribe(q)

        return Response(stream(), mimetype="text/event-stream")

    return app


def _config_to_dict(config: AppConfig) -> dict:
    d = asdict(config)
    for key in ("silence_gap_window", "commercial_gap_count", "program_gap_timeout"):
        d.get("engine", {}).pop(key, None)
    return d


def _save_config(config: AppConfig, path: str) -> None:
    import yaml

    d = _config_to_dict(config)
    with open(path, "w") as f:
        yaml.dump(d, f, default_flow_style=False, sort_keys=False)


def _get_system_info(app: Flask) -> dict:
    """Gather system health metrics including component status."""
    import os
    import shutil

    info: dict = {}

    # CPU temperature (Linux — RPi thermal zone)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp_c"] = int(f.read().strip()) / 1000.0
    except (FileNotFoundError, ValueError):
        info["cpu_temp_c"] = None

    # Process memory (RSS)
    try:
        import resource
        usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if os.uname().sysname == "Darwin":
            info["memory_mb"] = round(usage_kb / (1024 * 1024), 1)
        else:
            info["memory_mb"] = round(usage_kb / 1024, 1)
    except Exception:
        info["memory_mb"] = None

    # System memory (total + used from /proc/meminfo)
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total_mb = meminfo.get("MemTotal", 0) / 1024
            avail_mb = meminfo.get("MemAvailable", 0) / 1024
            info["mem_total_mb"] = round(total_mb, 0)
            info["mem_used_mb"] = round(total_mb - avail_mb, 0)
            info["mem_pct"] = round((total_mb - avail_mb) / total_mb * 100, 1) if total_mb else 0
    except (FileNotFoundError, ValueError):
        pass

    # System uptime
    try:
        with open("/proc/uptime") as f:
            info["system_uptime"] = float(f.read().split()[0])
    except (FileNotFoundError, ValueError):
        info["system_uptime"] = None

    # Load average
    try:
        load = os.getloadavg()
        info["load_avg"] = [round(x, 2) for x in load]
    except OSError:
        info["load_avg"] = None

    # Disk usage for working directory
    try:
        usage = shutil.disk_usage(".")
        info["disk_total_gb"] = round(usage.total / (1024**3), 1)
        info["disk_used_gb"] = round(usage.used / (1024**3), 1)
        info["disk_free_gb"] = round(usage.free / (1024**3), 1)
    except Exception:
        info["disk_total_gb"] = None
        info["disk_used_gb"] = None
        info["disk_free_gb"] = None

    # Component status from app.extensions
    components = app.extensions.get("cd_components", {})

    # MQTT publisher
    publisher = components.get("publisher")
    if publisher is not None:
        info["mqtt_connected"] = getattr(publisher, "_connected", False)
    else:
        info["mqtt_connected"] = None  # dry-run mode

    # Transcript analyzer
    transcript = components.get("transcript")
    if transcript is not None:
        info["whisper_running"] = getattr(transcript, "is_running", False)
        info["whisper_text"] = getattr(transcript, "last_text", "")
    else:
        info["whisper_enabled"] = False

    return info


def start_web_server(
    config: AppConfig,
    state_manager: WebStateManager,
    config_path: Optional[str] = None,
    publisher=None,
    transcript=None,
) -> None:
    """Start the Flask server on a background thread (non-blocking)."""
    import threading

    app = create_app(config, state_manager, config_path)
    app.extensions["cd_components"] = {
        "publisher": publisher,
        "transcript": transcript,
    }
    web_cfg = config.web

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
