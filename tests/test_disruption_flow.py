import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.db

IS_POSTGRES = os.environ.get("DATABASE_URL", "").startswith("postgresql")


# ---- helpers ------------------------------------------------------------------------
def token(client):
    client.get("/")
    with client.session_transaction() as s:
        return s["_csrf"]


def create(client, world, note="Slow traffic near the depot", route="r1", stop=None):
    data = {"csrf_token": token(client), "route_id": world[route], "raw_driver_note": note}
    if stop:
        data["affected_stop_id"] = world[stop]
    return client.post("/disruptions", data=data)


def did(response):
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(response.headers["Location"].rsplit("/", 1)[1])


def act(client, path):
    return client.post(path, data={"csrf_token": token(client)})


def route_status(world, key="r1"):
    from app.db import get_primary_session
    from models.transit import Route

    with get_primary_session() as db:
        return db.get(Route, world[key]).status


def alerts_of(disruption_id):
    from sqlalchemy import select

    from app.db import get_primary_session
    from models.transit import ServiceAlert

    with get_primary_session() as db:
        return db.scalars(select(ServiceAlert).where(ServiceAlert.disruption_id == disruption_id)
                          .order_by(ServiceAlert.id)).all()


def draft_id(disruption_id, channel="web"):
    return next(a.id for a in alerts_of(disruption_id) if a.channel == channel and a.status == "draft")


# ---- creation -------------------------------------------------------------------------
def test_create_logs_disruption_and_drafts(login, world, client):
    login()
    d = did(create(client, world))
    page = client.get(f"/disruptions/{d}")
    assert page.status_code == 200
    assert "template, not AI" in page.get_data(as_text=True)
    assert route_status(world) == "delayed"
    assert sorted(a.channel for a in alerts_of(d)) == ["signage", "sms", "web"]


def test_signage_draft_only_when_the_stop_has_signage(login, world, client):
    login()
    d = did(create(client, world, stop="s2"))  # Beta: no signage
    assert sorted(a.channel for a in alerts_of(d)) == ["sms", "web"]
    d2 = did(create(client, world, note="another incident", stop="s1"))  # Alpha: signage
    assert "signage" in [a.channel for a in alerts_of(d2)]


def test_stop_must_belong_to_the_route(login, world, client):
    login()
    assert create(client, world, stop="s3").status_code == 400  # s3 is on the other route


def test_unknown_stop_and_route_are_rejected(login, world, client):
    login()
    data = {"csrf_token": token(client), "route_id": world["r1"], "affected_stop_id": 99999,
            "raw_driver_note": "x"}
    assert client.post("/disruptions", data=data).status_code == 400
    data = {"csrf_token": token(client), "route_id": 99999, "raw_driver_note": "x"}
    assert client.post("/disruptions", data=data).status_code == 404
    assert client.post("/disruptions", data={"csrf_token": token(client), "raw_driver_note": "x"}).status_code == 400


def test_empty_and_overlong_notes_are_rejected(login, world, client):
    login()
    assert create(client, world, note="   ").status_code == 400
    assert create(client, world, note="x" * 2001).status_code == 400
    assert create(client, world, note="x" * 2000).status_code == 302


def test_double_submit_creates_a_single_disruption(login, world, client):
    from sqlalchemy import func, select

    from app.db import get_primary_session
    from models.transit import DisruptionEvent

    login()
    first = did(create(client, world, note="same note"))
    second = did(create(client, world, note="same note"))
    assert first == second
    with get_primary_session() as db:
        assert db.scalar(select(func.count()).select_from(DisruptionEvent)) == 1


def test_driver_note_markup_is_escaped_on_the_page(login, world, client):
    login()
    d = did(create(client, world, note="<script>alert(1)</script><img src=x onerror=alert(2)>"))
    html = client.get(f"/disruptions/{d}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_event_for_a_new_disruption_carries_no_model_text(login, world, client):
    from services.sse_service import broker

    login()
    sub = broker.subscribe("test")
    try:
        create(client, world, note="derailment at the depot")
        msg = sub.queue.get_nowait()
    finally:
        broker.unsubscribe(sub)
    assert "disruption_created" in msg and "B-1" in msg
    assert "summary" not in msg and "derailment" not in msg


# ---- LLM handling ---------------------------------------------------------------------
def test_garbage_llm_output_does_not_cause_a_500(login, world, client, fake_llm):
    fake_llm.responder = staticmethod(
        lambda s, u: {"severity": "minor", "route_status": "normal", "diversion_summary": None,
                      "web": 5, "sms": None, "signage": "x" * 999})
    login()
    d = did(create(client, world))
    assert route_status(world) == "delayed"
    assert all(len(a.message_body) <= 500 for a in alerts_of(d))


def test_llm_is_never_called_while_a_write_transaction_is_open(login, world, client, fake_llm):
    """If the service held a transaction/row lock during the model call, this write would block or fail."""
    from sqlalchemy import text

    from app.db import get_write_session

    def responder(system, user):
        with get_write_session() as db:
            db.execute(text("UPDATE routes SET name = name"))
        return None

    fake_llm.responder = staticmethod(responder)
    login()
    assert create(client, world).status_code == 302
    assert fake_llm.calls == 2  # analysis + drafts


# ---- route status is derived ------------------------------------------------------------
def test_route_status_is_the_worst_open_disruption(login, world, client):
    login()
    bad = did(create(client, world, note="train derailed at the depot"))
    assert route_status(world) == "suspended"
    minor = did(create(client, world, note="slow traffic"))
    assert route_status(world) == "suspended"  # a minor incident must not downgrade it
    assert act(client, f"/disruptions/{bad}/resolve").status_code == 302
    assert route_status(world) == "delayed"
    act(client, f"/disruptions/{minor}/resolve")
    assert route_status(world) == "normal"


def test_other_routes_are_unaffected(login, world, client):
    login()
    did(create(client, world, note="train derailed"))
    assert route_status(world, "r2") == "normal"


# ---- resolve ---------------------------------------------------------------------------------
def test_resolve_is_idempotent_and_archives_drafts(login, world, client):
    from app.db import get_primary_session
    from models.transit import DisruptionEvent

    login()
    d = did(create(client, world))
    act(client, f"/disruptions/{d}/resolve")
    with get_primary_session() as db:
        first = db.get(DisruptionEvent, d).resolved_at
    act(client, f"/disruptions/{d}/resolve")
    with get_primary_session() as db:
        assert db.get(DisruptionEvent, d).resolved_at == first
    assert {a.status for a in alerts_of(d)} == {"archived"}


# ---- alert state machine ---------------------------------------------------------------
def test_publish_is_idempotent_and_broadcasts_once(login, world, client):
    from services.sse_service import broker

    login()
    d = did(create(client, world))
    alert = draft_id(d)
    before = broker._seq
    assert act(client, f"/alerts/{alert}/publish").status_code == 302
    assert act(client, f"/alerts/{alert}/publish").status_code == 302
    assert broker._seq == before + 1
    assert [a.status for a in alerts_of(d) if a.id == alert] == ["published"]


def test_archived_drafts_cannot_be_published(login, world, client):
    login()
    d = did(create(client, world))
    old = draft_id(d)
    act(client, f"/disruptions/{d}/alerts")  # regenerate archives the old drafts
    assert act(client, f"/alerts/{old}/publish").status_code == 409


def test_cannot_publish_or_regenerate_after_resolve(login, world, client):
    login()
    d = did(create(client, world))
    alert = draft_id(d)
    act(client, f"/disruptions/{d}/resolve")
    assert act(client, f"/alerts/{alert}/publish").status_code == 409
    assert act(client, f"/disruptions/{d}/alerts").status_code == 409


def test_publishing_a_new_version_supersedes_the_live_one(login, world, client):
    login()
    d = did(create(client, world))
    first = draft_id(d)
    act(client, f"/alerts/{first}/publish")
    act(client, f"/disruptions/{d}/alerts")  # new drafts; the live alert stays live
    assert [a.status for a in alerts_of(d) if a.id == first] == ["published"]
    second = draft_id(d)
    act(client, f"/alerts/{second}/publish")
    web = [a for a in alerts_of(d) if a.channel == "web"]
    assert [a.status for a in web if a.id == first] == ["archived"]
    assert sum(a.status == "published" for a in web) == 1


def test_database_rejects_two_published_alerts_on_one_channel(world):
    from sqlalchemy.exc import IntegrityError

    from app.db import get_write_session
    from models.transit import DisruptionEvent, ServiceAlert

    with get_write_session() as db:
        d = DisruptionEvent(route_id=world["r1"], raw_driver_note="x", severity="minor",
                            route_status="delayed", status="open")
        db.add(d)
        db.flush()
        db.add(ServiceAlert(disruption_id=d.id, channel="web", tone="informational",
                            message_body="a", status="published"))
        db.flush()
        db.add(ServiceAlert(disruption_id=d.id, channel="web", tone="informational",
                            message_body="b", status="published"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


# ---- roles -------------------------------------------------------------------------------------
def test_viewers_can_read_but_not_write(login, world, client):
    login("dispatcher", "disp")
    d = did(create(client, world))
    alert = draft_id(d)
    client.post("/logout", data={"csrf_token": token(client)})

    login("viewer", "view")
    assert client.get("/").status_code == 200
    assert client.get(f"/disruptions/{d}").status_code == 200
    page = client.get(f"/disruptions/{d}").get_data(as_text=True)
    assert "Publish to" not in page and "Mark Resolved" not in page
    assert client.get("/disruptions/new").status_code == 403
    assert act(client, f"/alerts/{alert}/publish").status_code == 403
    assert act(client, f"/disruptions/{d}/resolve").status_code == 403
    assert create(client, world).status_code == 403


# ---- live feed + pages ---------------------------------------------------------------------------
def test_event_stream_requires_login_and_streams_to_users(client, login):
    assert client.get("/events/stream").status_code == 401
    login()
    resp = client.get("/events/stream", buffered=False)
    try:
        assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
        first = next(iter(resp.response))
        assert "retry:" in (first.decode() if isinstance(first, bytes) else first)
    finally:
        resp.close()


def test_pages_render_and_upcoming_trips_exclude_the_past(login, world, client):
    from app.db import get_write_session
    from models.transit import Trip

    now = datetime.now(timezone.utc)
    with get_write_session() as db:
        db.add_all([
            Trip(route_id=world["r1"], headsign="Past Place", scheduled_departure=now - timedelta(hours=3),
                 delay_minutes=0, status="scheduled"),
            Trip(route_id=world["r1"], headsign="Future Place", scheduled_departure=now + timedelta(minutes=30),
                 delay_minutes=0, status="scheduled"),
        ])
    login()
    assert client.get("/").status_code == 200
    assert client.get("/routes").status_code == 200
    html = client.get(f"/routes/{world['r1']}").get_data(as_text=True)
    assert "Future Place" in html and "Past Place" not in html


def test_read_session_switches_to_primary_after_a_write(app, monkeypatch):
    from contextlib import contextmanager

    import app.consistency as c

    used = []

    @contextmanager
    def fake(name):
        used.append(name)
        yield name

    monkeypatch.setattr(c, "get_primary_session", lambda: fake("primary"))
    monkeypatch.setattr(c, "get_read_session", lambda: fake("replica"))
    with app.test_request_context("/"):
        with c.read_session():
            pass
        c.mark_write()
        with c.read_session():
            pass
    assert used == ["replica", "primary"]


# ---- concurrency (needs real row locks, so PostgreSQL only) --------------------------------------
needs_postgres = pytest.mark.skipif(not IS_POSTGRES, reason="row locking is a no-op on SQLite")


def _run_threads(fns):
    results, errors = [None] * len(fns), []

    def runner(i, fn):
        try:
            results[i] = fn()
        except Exception as exc:  # collected and asserted on below
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert not errors, errors
    return results


@needs_postgres
def test_concurrent_publishes_of_one_alert_broadcast_exactly_once(app, world):
    from services import disruption_service as svc

    d = svc.create_disruption(world["r1"], None, "slow traffic").disruption_id
    alert = draft_id(d)
    outcomes = _run_threads([lambda: svc.publish_alert(alert)] * 8)
    assert sum(o.changed for o in outcomes) == 1
    assert sum(a.status == "published" for a in alerts_of(d)) == 1


@needs_postgres
def test_concurrent_creates_leave_the_worst_status(app, world):
    from services import disruption_service as svc

    _run_threads([
        lambda: svc.create_disruption(world["r1"], None, "train derailed"),
        lambda: svc.create_disruption(world["r1"], None, "slow traffic"),
        lambda: svc.create_disruption(world["r1"], None, "slow traffic again"),
    ])
    assert route_status(world) == "suspended"
