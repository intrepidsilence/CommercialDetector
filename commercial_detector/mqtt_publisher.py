"""MQTT publisher — pushes state transitions and heartbeats to an MQTT broker.

Uses paho-mqtt v2.x API. Connection errors are logged but never crash the
detection pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import paho.mqtt.client as mqtt

from .config import MqttConfig
from .models import DetectionState, StateTransition

logger = logging.getLogger(__name__)


class MqttPublisher:
    """Publishes detection events to an MQTT broker.

    All public methods swallow connection/publish errors so that a flaky
    broker never takes down the detection pipeline.
    """

    def __init__(self, config: MqttConfig) -> None:
        self._config = config
        self._client: mqtt.Client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id,
        )
        self._connected = False
        self._last_state: Optional[DetectionState] = None
        self._last_error: Optional[str] = None

        # Auth
        if config.username:
            self._client.username_pw_set(config.username, config.password)

        # Callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    # -- callbacks ----------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        rc: mqtt.ReasonCode,
        properties: Optional[mqtt.Properties] = None,
    ) -> None:
        if rc == 0:
            logger.info(
                "Connected to MQTT broker %s:%d",
                self._config.broker_host,
                self._config.broker_port,
            )
            self._connected = True
            self._last_error = None
        else:
            logger.warning("MQTT connect failed: %s", rc)
            self._connected = False
            self._last_error = str(rc)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.DisconnectFlags,
        rc: mqtt.ReasonCode,
        properties: Optional[mqtt.Properties] = None,
    ) -> None:
        logger.info("Disconnected from MQTT broker (rc=%s)", rc)
        self._connected = False

    # -- public API ---------------------------------------------------------

    @property
    def status(self) -> dict:
        """Return MQTT connection status for the web UI."""
        return {
            "connected": self._connected,
            "broker": f"{self._config.broker_host}:{self._config.broker_port}",
            "error": self._last_error,
        }

    def connect(self) -> None:
        """Connect to the MQTT broker. Errors are logged, not raised."""
        try:
            self._client.connect(
                self._config.broker_host,
                self._config.broker_port,
                keepalive=self._config.keepalive,
            )
            self._client.loop_start()
        except Exception:
            logger.exception(
                "Failed to connect to MQTT broker at %s:%d",
                self._config.broker_host,
                self._config.broker_port,
            )

    def publish_transition(self, event: StateTransition) -> None:
        """Publish a state transition event."""
        topic = f"{self._config.topic_prefix}/state/changed"
        payload = json.dumps(event.to_dict())
        self._publish(topic, payload, retain=False)
        # Also update the retained current-state topic
        self.publish_state(event.to_state)

    def publish_state(self, state: DetectionState) -> None:
        """Publish the current detection state as a retained message."""
        topic = f"{self._config.topic_prefix}/state"
        self._publish(topic, state.value, retain=True)
        self._last_state = state

    def publish_heartbeat(self) -> None:
        """Publish a health heartbeat with timestamp and current state."""
        topic = f"{self._config.topic_prefix}/health"
        payload = json.dumps({
            "timestamp": time.time(),
            "state": self._last_state.value if self._last_state else "unknown",
        })
        self._publish(topic, payload, retain=False)

    def disconnect(self) -> None:
        """Cleanly disconnect from the broker."""
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            logger.exception("Error during MQTT disconnect")
        self._connected = False

    # -- internal -----------------------------------------------------------

    def _publish(self, topic: str, payload: str, *, retain: bool) -> None:
        """Publish a message, swallowing errors."""
        if not self._connected:
            logger.debug("MQTT not connected; dropping publish to %s", topic)
            return
        try:
            self._client.publish(
                topic,
                payload,
                qos=self._config.qos,
                retain=retain,
            )
        except Exception:
            logger.exception("Failed to publish to %s", topic)
