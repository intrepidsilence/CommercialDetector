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


def _get_system_info() -> dict:
    import os

    info: dict = {}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp_c"] = int(f.read().strip()) / 1000.0
    except (FileNotFoundError, ValueError):
        info["cpu_temp_c"] = None
    try:
        import resource

        usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if os.uname().sysname == "Darwin":
            info["memory_mb"] = round(usage_kb / (1024 * 1024), 1)
        else:
            info["memory_mb"] = round(usage_kb / 1024, 1)
    except Exception:
        info["memory_mb"] = None
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
