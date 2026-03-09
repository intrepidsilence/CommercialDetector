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
