"""Orchestrator — wires signal source -> engine -> MQTT.

Entry point for the commercial detection pipeline. Handles CLI parsing,
logging setup, signal handling, and the main processing loop.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from queue import Empty, Queue
from typing import Optional

from commercial_detector.config import AppConfig, load_config
from commercial_detector.detection_engine import DetectionEngine
from commercial_detector.mqtt_publisher import MqttPublisher
from commercial_detector.signal_source import SignalSource, SignalSourceError
from commercial_detector.transcript_analyzer import TranscriptAnalyzer
from commercial_detector.web.state_manager import WebStateManager
from commercial_detector.web.server import start_web_server

logger = logging.getLogger("commercial_detector")

# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 30.0


def _setup_logging(level: str) -> None:
    """Configure logging with a structured format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="commercial_detector",
        description="Real-time commercial break detection for live TV streams.",
    )
    parser.add_argument(
        "--config", "-c",
        metavar="PATH",
        help="Path to config.yaml (default: ./config.yaml or $COMMERCIAL_DETECTOR_CONFIG)",
    )
    parser.add_argument(
        "--input", "-i",
        metavar="PATH",
        help="Input file for testing (e.g., test_video.mp4). Overrides capture.input_file.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip MQTT connection (just log state changes).",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable the web dashboard.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Main entry point. Returns 0 on clean exit, 1 on error."""
    if argv is None:
        argv = sys.argv[1:]

    args = _parse_args(argv)

    # Load config
    config = load_config(args.config)

    # CLI overrides
    if args.input:
        config.capture.input_file = args.input
    if args.log_level:
        config.log_level = args.log_level

    _setup_logging(config.log_level)
    logger.info("CommercialDetector starting")

    # Wire up components
    engine = DetectionEngine(config.engine)

    publisher: Optional[MqttPublisher] = None
    if not args.dry_run:
        publisher = MqttPublisher(config.mqtt)
        publisher.connect()
        logger.info("MQTT publisher connected (topic prefix: %s)", config.mqtt.topic_prefix)
    else:
        logger.info("Dry-run mode — MQTT publishing disabled")

    # Graceful shutdown
    shutdown_requested = False

    def _handle_signal(signum: int, _frame) -> None:
        nonlocal shutdown_requested
        name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", name)
        shutdown_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Transcript analyzer (optional — requires faster-whisper)
    transcript_queue: Queue = Queue()
    transcript_analyzer = TranscriptAnalyzer(config, transcript_queue)
    transcript_running = transcript_analyzer.start()
    if transcript_running:
        logger.info("Transcript analysis active")

    # Web dashboard (optional — started after other components for status reporting)
    web_state: Optional[WebStateManager] = None
    if config.web.enabled and not args.no_web:
        web_state = WebStateManager()
        start_web_server(
            config, web_state, config_path=args.config,
            publisher=publisher, transcript=transcript_analyzer,
        )
        logger.info("Web dashboard at http://%s:%d", config.web.host, config.web.port)
    else:
        logger.info("Web dashboard disabled")

    # Transcript signals held back until FFmpeg catches up to their timestamp.
    # In file mode, audio-only FFmpeg races far ahead of the main FFmpeg with
    # video filters, producing transcript timestamps in the "future" relative
    # to the main signal stream. Feeding these to the engine would corrupt the
    # sliding window. The pending buffer holds them until the main loop's
    # current PTS catches up.
    _pending_transcript: list = []

    # Main processing loop
    last_heartbeat = time.monotonic()
    signal_count = 0
    _RETRY_DELAY = 10  # seconds between signal source retries

    def _publish_transition(transition):
        logger.info(
            "STATE CHANGE: %s -> %s @ %.1fs (confidence=%.3f)",
            transition.from_state.value,
            transition.to_state.value,
            transition.timestamp,
            transition.confidence,
        )
        if publisher is not None:
            publisher.publish_transition(transition)
        if web_state is not None:
            web_state.push_transition(transition)

    def _run_signal_loop():
        """Run the signal source loop once. Returns on source exit or error."""
        nonlocal last_heartbeat, signal_count
        try:
            with SignalSource(config) as source:
                logger.info("Signal source started, entering detection loop")

                for detection_signal in source:
                    if shutdown_requested:
                        return

                    transition = engine.process(detection_signal)

                    # Push to web dashboard
                    if web_state is not None:
                        web_state.push_signal(detection_signal)
                        web_state.update_score(
                            engine._state.commercial_score, detection_signal.timestamp
                        )

                    if transition is not None:
                        _publish_transition(transition)

                    current_pts = detection_signal.timestamp

                    # Pull new transcript signals from queue into pending buffer
                    while True:
                        try:
                            _pending_transcript.append(transcript_queue.get_nowait())
                        except Empty:
                            break

                    # Release pending transcript signals whose timestamp <= current PTS
                    still_pending = []
                    for ts in _pending_transcript:
                        if ts.timestamp <= current_pts:
                            transition = engine.process(ts)
                            if web_state is not None:
                                web_state.push_signal(ts)
                                web_state.update_score(
                                    engine._state.commercial_score, ts.timestamp
                                )
                            if transition is not None:
                                _publish_transition(transition)
                        else:
                            still_pending.append(ts)
                    _pending_transcript[:] = still_pending

                    # Periodic heartbeat
                    now = time.monotonic()
                    if publisher is not None and (now - last_heartbeat) >= _HEARTBEAT_INTERVAL:
                        publisher.publish_heartbeat()
                        last_heartbeat = now

                    signal_count += 1

        except SignalSourceError as exc:
            logger.error("Signal source failed: %s", exc)

    try:
        while not shutdown_requested:
            _run_signal_loop()

            if shutdown_requested:
                break

            # Signal source exited — wait before retrying so the web UI
            # stays up for configuration changes.
            logger.info(
                "Signal source stopped. Retrying in %ds... "
                "(web dashboard still available at http://%s:%d)",
                _RETRY_DELAY, config.web.host, config.web.port,
            )
            for _ in range(_RETRY_DELAY):
                if shutdown_requested:
                    break
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception:
        logger.exception("Unexpected error in main loop")
        return 1
    finally:
        transcript_analyzer.stop()
        if publisher is not None:
            publisher.disconnect()
            logger.info("MQTT publisher disconnected")

    logger.info("CommercialDetector stopped after processing %d signals", signal_count)
    return 0
