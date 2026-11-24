import json

import pytest

from services.sse_service import BrokerFull, EventBroker


def _take(gen, n):
    return [next(gen) for _ in range(n)]


def _event_id(msg):
    return int(msg.split("\n", 1)[0].removeprefix("id: "))


def test_event_ids_increase_and_payload_is_json():
    b = EventBroker()
    sub = b.subscribe("u1")
    first = b.publish("x", {"a": 1})
    second = b.publish("x", {"a": 2})
    assert second == first + 1
    gen = b.stream(sub)
    _take(gen, 2)  # retry + connected
    msg = next(gen)
    assert msg.startswith(f"id: {first}\nevent: x\ndata: ")
    assert json.loads(msg.split("data: ", 1)[1])["data"] == {"a": 1}


def test_slow_client_is_disconnected_cleanly_not_left_silent():
    b = EventBroker(queue_size=2)
    sub = b.subscribe("u1")
    for i in range(5):
        b.publish("x", {"i": i})
    assert b.client_count == 0 and sub.closed
    gen = b.stream(sub, keepalive=0.05)
    out = list(gen)  # must END, not loop forever sending keep-alives
    assert out[0].startswith("retry:")
    assert not any(m.startswith(": keep-alive") for m in out)


def test_idle_stream_sends_keepalive():
    b = EventBroker()
    gen = b.stream(b.subscribe("u1"), keepalive=0.01)
    _take(gen, 2)
    assert next(gen).startswith(": keep-alive")


def test_reconnect_replays_missed_events():
    b = EventBroker()
    ids = [b.publish("x", {"i": i}) for i in range(5)]
    sub = b.subscribe("u1", last_event_id=ids[1])
    replayed = [_event_id(m) for m in sub.backlog]
    assert replayed == ids[2:]


def test_gap_beyond_history_asks_client_to_resync():
    b = EventBroker(history=3)
    for i in range(10):
        b.publish("x", {"i": i})
    sub = b.subscribe("u1", last_event_id=2)
    assert sub.backlog == ["event: resync\ndata: {}\n\n"]


def test_unknown_future_id_after_restart_resyncs():
    b = EventBroker()
    sub = b.subscribe("u1", last_event_id=999)
    assert sub.backlog[0].startswith("event: resync")


def test_no_missed_or_duplicate_events_between_replay_and_live():
    b = EventBroker()
    first = b.publish("x", {})
    sub = b.subscribe("u1", last_event_id=0)
    live = b.publish("x", {})
    seen = [_event_id(m) for m in sub.backlog] + [_event_id(sub.queue.get_nowait())]
    assert seen == [first, live]


def test_total_cap():
    b = EventBroker(max_clients=2, max_per_owner=5)
    b.subscribe("a")
    b.subscribe("b")
    with pytest.raises(BrokerFull):
        b.subscribe("c")


def test_per_owner_cap_evicts_oldest_connection():
    b = EventBroker(max_per_owner=2)
    s1, s2, s3 = b.subscribe("u"), b.subscribe("u"), b.subscribe("u")
    assert s1.closed and not s2.closed and not s3.closed
    assert b.client_count == 2
    assert list(b.stream(s1, keepalive=0.05))[-1].startswith("event: connected")


def test_unsubscribe_is_idempotent():
    b = EventBroker()
    s = b.subscribe("u")
    b.unsubscribe(s)
    b.unsubscribe(s)
    assert b.client_count == 0
