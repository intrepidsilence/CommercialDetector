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
        assert signals[0]["timestamp"] == 5.0

    def test_signal_counts_increment(self):
        mgr = WebStateManager()
        mgr.push_signal(DetectionSignal(timestamp=1.0, signal_type=SignalType.SILENCE_END))
        mgr.push_signal(DetectionSignal(timestamp=2.0, signal_type=SignalType.SILENCE_END))
        mgr.push_signal(DetectionSignal(timestamp=3.0, signal_type=SignalType.BLACK_START))
        snapshot = mgr.get_snapshot()
        assert snapshot["signal_counts"]["silence_end"] == 2
        assert snapshot["signal_counts"]["black_start"] == 1


class TestPushTransition:
    def test_push_and_retrieve_transition(self):
        mgr = WebStateManager()
        tr = StateTransition(
            timestamp=100.0, from_state=DetectionState.UNKNOWN,
            to_state=DetectionState.COMMERCIAL, confidence=0.8,
            signals={"trigger_signal": "black_start"},
        )
        mgr.push_transition(tr)
        transitions = mgr.get_transitions()
        assert len(transitions) == 1
        assert transitions[0]["to"] == "commercial"

    def test_transition_updates_current_state(self):
        mgr = WebStateManager()
        tr = StateTransition(
            timestamp=100.0, from_state=DetectionState.UNKNOWN,
            to_state=DetectionState.COMMERCIAL, confidence=0.8,
        )
        mgr.push_transition(tr)
        assert mgr.get_snapshot()["state"] == "commercial"


class TestUpdateScore:
    def test_score_in_snapshot(self):
        mgr = WebStateManager()
        mgr.update_score(12.5, timestamp=50.0)
        assert mgr.get_snapshot()["score"] == 12.5

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
        mgr.push_signal(DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START))
        event = q.get(timeout=1)
        assert event["event"] == "signal"

    def test_unsubscribe_removes_queue(self):
        mgr = WebStateManager()
        q = mgr.subscribe()
        mgr.unsubscribe(q)
        mgr.push_signal(DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START))
        with pytest.raises(Empty):
            q.get(timeout=0.1)

    def test_multiple_subscribers(self):
        mgr = WebStateManager()
        q1 = mgr.subscribe()
        q2 = mgr.subscribe()
        mgr.push_signal(DetectionSignal(timestamp=1.0, signal_type=SignalType.BLACK_START))
        assert not q1.empty()
        assert not q2.empty()


class TestThreadSafety:
    def test_concurrent_pushes(self):
        mgr = WebStateManager()
        errors = []
        def push_signals():
            try:
                for i in range(100):
                    mgr.push_signal(DetectionSignal(timestamp=float(i), signal_type=SignalType.SCENE_CHANGE))
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=push_signals) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert mgr.get_snapshot()["signal_counts"]["scene_change"] == 400


class TestGetSnapshot:
    def test_default_snapshot(self):
        mgr = WebStateManager()
        snapshot = mgr.get_snapshot()
        assert snapshot["state"] == "unknown"
        assert snapshot["score"] == 0.0
        assert snapshot["signal_counts"] == {}
        assert "uptime" in snapshot
