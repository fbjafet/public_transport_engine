"""In-process server-sent-events broker.

Properties:
* Every event has an increasing id and the last HISTORY events are kept, so a
  client that reconnects with Last-Event-ID receives what it missed.
* A client that falls behind (full queue) is disconnected cleanly, not left
  looking connected. The browser then reconnects and replays from history.
* Total and per-user connection caps bound resource use.

Limitation: state lives in this process. Run a single worker process (threads
are fine); see gunicorn.conf.py and FIX_NOTES.md.
"""
import json
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

_CLOSE = object()


class BrokerFull(Exception):
    """Too many live connections."""


@dataclass(eq=False)
class Subscription:
    owner: Any
    queue: "queue.Queue[Any]"
    backlog: list[str] = field(default_factory=list)
    closed: bool = False


class EventBroker:
    def __init__(
        self,
        max_clients: int = 100,
        max_per_owner: int = 3,
        history: int = 200,
        queue_size: int = 50,
    ) -> None:
        self.max_clients = max_clients
        self.max_per_owner = max_per_owner
        self._queue_size = queue_size
        self._history: deque[tuple[int, str]] = deque(maxlen=history)
        self._seq = 0
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()

    def configure(self, max_clients: int, max_per_owner: int) -> None:
        self.max_clients = max_clients
        self.max_per_owner = max_per_owner

    # -- subscription management ------------------------------------------- #
    def _close_locked(self, sub: Subscription) -> None:
        """Remove `sub` and tell its stream to finish. Caller holds the lock."""
        if sub in self._subs:
            self._subs.remove(sub)
        sub.closed = True
        try:  # make room, then wake the consumer with the close marker
            while True:
                sub.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            sub.queue.put_nowait(_CLOSE)
        except queue.Full:  # consumer will see sub.closed on its next wake-up
            pass

    def subscribe(self, owner: Any = None, last_event_id: int | None = None) -> Subscription:
        with self._lock:
            if len(self._subs) >= self.max_clients:
                raise BrokerFull("too many live connections")

            # Per-owner cap: evict that owner's oldest connection (e.g. a stale
            # tab or a connection the server has not yet noticed is dead).
            mine = [s for s in self._subs if s.owner == owner]
            while owner is not None and len(mine) >= self.max_per_owner:
                self._close_locked(mine.pop(0))

            sub = Subscription(owner=owner, queue=queue.Queue(maxsize=self._queue_size))
            if last_event_id is not None:
                oldest = self._history[0][0] if self._history else self._seq + 1
                if last_event_id > self._seq or last_event_id < oldest - 1:
                    # Server restarted or the gap is larger than our history.
                    sub.backlog.append("event: resync\ndata: {}\n\n")
                else:
                    sub.backlog.extend(m for i, m in self._history if i > last_event_id)
            self._subs.append(sub)
            return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
            sub.closed = True

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # -- publishing --------------------------------------------------------- #
    def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        body = json.dumps(
            {
                "type": event_type,
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                "data": payload,
            }
        )
        with self._lock:
            self._seq += 1
            event_id = self._seq
            formatted = f"id: {event_id}\nevent: {event_type}\ndata: {body}\n\n"
            self._history.append((event_id, formatted))
            for sub in list(self._subs):
                try:
                    sub.queue.put_nowait(formatted)
                except queue.Full:
                    self._close_locked(sub)
        return event_id

    # -- streaming ---------------------------------------------------------- #
    def stream(self, sub: Subscription, keepalive: float = 15.0) -> Iterator[str]:
        try:
            yield "retry: 3000\n\n"
            yield 'event: connected\ndata: {"status": "listening"}\n\n'
            for message in sub.backlog:
                yield message
            while True:
                try:
                    item = sub.queue.get(timeout=keepalive)
                except queue.Empty:
                    if sub.closed:
                        return
                    yield ": keep-alive\n\n"
                    continue
                if item is _CLOSE:
                    return
                yield item
        finally:
            self.unsubscribe(sub)


broker = EventBroker()
