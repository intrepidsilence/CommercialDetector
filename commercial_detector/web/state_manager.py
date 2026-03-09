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
        max_score_history: int = 1800,
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
        self._score_ticker: threading.Thread | None = None
        self._score_ticker_stop = threading.Event()
        self._latest_pts: float = 0.0

    def start_score_ticker(self, interval: float = 1.0) -> None:
        """Push a score snapshot every `interval` seconds for the chart."""
        def _tick():
            while not self._score_ticker_stop.wait(interval):
                with self._lock:
                    pts = self._latest_pts
                    score = self._current_score
                if pts > 0:
                    self.update_score(score, pts)

        self._score_ticker = threading.Thread(
            target=_tick, name="score-ticker", daemon=True,
        )
        self._score_ticker.start()

    def push_signal(self, signal: DetectionSignal) -> None:
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
        entry = transition.to_dict()
        with self._lock:
            self._transitions.append(entry)
            self._current_state = transition.to_state.value
            self._broadcast({"event": "transition", "data": entry})

    def update_score(self, score: float, timestamp: float) -> None:
        with self._lock:
            self._current_score = score
            self._latest_pts = max(self._latest_pts, timestamp)
            self._score_history.append([timestamp, score])

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._current_state,
                "score": self._current_score,
                "signal_counts": dict(self._signal_counts),
                "transition_count": len(self._transitions),
                "uptime": time.monotonic() - self._start_time,
            }

    def get_signals(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self._signals)
        return items[-limit:] if limit < len(items) else items

    def get_transitions(self) -> list[dict]:
        with self._lock:
            return list(self._transitions)

    def get_score_history(self) -> list[list]:
        with self._lock:
            return list(self._score_history)

    def subscribe(self) -> Queue:
        q: Queue = Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict[str, Any]) -> None:
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
