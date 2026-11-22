import json
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Iterator


class EventBroker:
    def __init__(self) -> None:
        self._listeners: list[queue.Queue[str]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=50)
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        message = json.dumps(
            {
                "type": event_type,
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                "data": payload,
            }
        )
        formatted = f"event: {event_type}\ndata: {message}\n\n"
        with self._lock:
            dead: list[queue.Queue[str]] = []
            for q in self._listeners:
                try:
                    q.put_nowait(formatted)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._listeners.remove(q)

    def stream(self) -> Iterator[str]:
        q = self.subscribe()
        try:
            yield 'event: connected\ndata: {"status": "listening"}\n\n'
            while True:
                try:
                    yield q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            self.unsubscribe(q)


broker = EventBroker()