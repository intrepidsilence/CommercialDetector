"""Tests for MqttPublisher — message formatting and publish behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from commercial_detector.config import MqttConfig
from commercial_detector.models import DetectionState, StateTransition
from commercial_detector.mqtt_publisher import MqttPublisher


@pytest.fixture
def config() -> MqttConfig:
    return MqttConfig(
        broker_host="localhost",
        broker_port=1883,
        topic_prefix="test-detector",
    )


@pytest.fixture
def publisher(config: MqttConfig) -> MqttPublisher:
    """Create a publisher with a mocked paho client."""
    with patch("commercial_detector.mqtt_publisher.mqtt.Client") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        pub = MqttPublisher(config)
        # Simulate connected state
        pub._connected = True
        pub._client = mock_client
        yield pub


class TestPublishTransition:
    """Verify transition event publishing."""

    def test_publishes_to_correct_topic(self, publisher: MqttPublisher):
        event = StateTransition(
            timestamp=1000.0,
            from_state=DetectionState.PROGRAM,
            to_state=DetectionState.COMMERCIAL,
            confidence=0.85,
        )
        publisher.publish_transition(event)

        calls = publisher._client.publish.call_args_list
        # Should make 2 calls: one for state/changed, one for state (retained)
        assert len(calls) == 2

        # First call: state/changed event
        topic, payload = calls[0][0][:2]
        assert topic == "test-detector/state/changed"
        data = json.loads(payload)
        assert data["from"] == "program"
        assert data["to"] == "commercial"
        assert data["confidence"] == 0.85

    def test_updates_retained_state(self, publisher: MqttPublisher):
        event = StateTransition(
            timestamp=1000.0,
            from_state=DetectionState.PROGRAM,
            to_state=DetectionState.COMMERCIAL,
        )
        publisher.publish_transition(event)

        calls = publisher._client.publish.call_args_list
        # Second call should be the retained state update
        topic = calls[1][0][0]
        payload = calls[1][0][1]
        assert topic == "test-detector/state"
        assert payload == "commercial"


class TestPublishState:
    """Verify retained state publishing."""

    def test_publishes_retained(self, publisher: MqttPublisher):
        publisher.publish_state(DetectionState.PROGRAM)

        publisher._client.publish.assert_called_once()
        call_kwargs = publisher._client.publish.call_args
        assert call_kwargs[1]["retain"] is True

    def test_publishes_state_value_string(self, publisher: MqttPublisher):
        publisher.publish_state(DetectionState.COMMERCIAL)

        topic, payload = publisher._client.publish.call_args[0][:2]
        assert topic == "test-detector/state"
        assert payload == "commercial"


class TestPublishHeartbeat:
    """Verify heartbeat message format."""

    def test_heartbeat_includes_timestamp(self, publisher: MqttPublisher):
        publisher._last_state = DetectionState.PROGRAM
        publisher.publish_heartbeat()

        topic, payload = publisher._client.publish.call_args[0][:2]
        assert topic == "test-detector/health"
        data = json.loads(payload)
        assert "timestamp" in data
        assert data["state"] == "program"

    def test_heartbeat_with_unknown_state(self, publisher: MqttPublisher):
        publisher._last_state = None
        publisher.publish_heartbeat()

        _, payload = publisher._client.publish.call_args[0][:2]
        data = json.loads(payload)
        assert data["state"] == "unknown"


class TestConnectionHandling:
    """Verify graceful handling of connection issues."""

    def test_publish_skipped_when_disconnected(self, publisher: MqttPublisher):
        publisher._connected = False
        publisher.publish_state(DetectionState.PROGRAM)
        publisher._client.publish.assert_not_called()

    def test_publish_error_doesnt_raise(self, publisher: MqttPublisher):
        publisher._client.publish.side_effect = Exception("Broker gone")
        # Should not raise
        publisher.publish_state(DetectionState.PROGRAM)


class TestConnect:
    """Test connection setup."""

    def test_connect_calls_broker(self, config: MqttConfig):
        with patch("commercial_detector.mqtt_publisher.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            pub = MqttPublisher(config)
            pub.connect()
            mock_client.connect.assert_called_once_with(
                "localhost", 1883, keepalive=60
            )
            mock_client.loop_start.assert_called_once()

    def test_connect_error_doesnt_raise(self, config: MqttConfig):
        with patch("commercial_detector.mqtt_publisher.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.connect.side_effect = ConnectionRefusedError()
            MockClient.return_value = mock_client
            pub = MqttPublisher(config)
            # Should not raise
            pub.connect()


class TestDisconnect:
    """Test clean disconnection."""

    def test_disconnect_stops_loop(self, publisher: MqttPublisher):
        publisher.disconnect()
        publisher._client.loop_stop.assert_called_once()
        publisher._client.disconnect.assert_called_once()
        assert publisher._connected is False
