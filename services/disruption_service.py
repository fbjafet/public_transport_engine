"""Disruption and alert workflows.

Rules every function here follows:
* LLM calls happen with NO database transaction open (read phase -> LLM -> write phase).
* All writes for one route are serialised by locking the route row first, then the
  disruption row. Lock order is always route -> disruption, so no deadlocks.
* State is validated under those locks, so concurrent clicks cannot corrupt it.
* Functions return an Outcome; the caller broadcasts the event after the commit.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.db import get_primary_session, get_write_session
from models.transit import DisruptionEvent, Route, ServiceAlert, Stop, route_stops
from services import ai_service
from services.errors import Conflict, Invalid, NotFound
from services.state import default_tone, worst_route_status

MAX_NOTE_LENGTH = 2000
DUPLICATE_WINDOW = timedelta(minutes=2)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class Outcome:
    disruption_id: int
    changed: bool = True
    event: tuple[str, dict] | None = None  # (event_type, payload) to broadcast after commit
    message: str = ""


def clean_note(raw: str | None) -> str:
    note = _CONTROL_CHARS.sub("", (raw or "")).strip()
    if not note:
        raise Invalid("A driver note is required.")
    if len(note) > MAX_NOTE_LENGTH:
        raise Invalid(f"The driver note is too long (maximum {MAX_NOTE_LENGTH} characters).")
    return note


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Locking and shared write helpers
# --------------------------------------------------------------------------- #
def _lock_route(db, route_id: int) -> Route:
    route = db.scalar(
        select(Route).where(Route.id == 	route_id).with_for_update().execution_options(populate_existing=True)
    )
    if route is None:
        raise NotFound("Route not found.")
    return route


def _lock_disruption(db, disruption_id: int) -> tuple[Route, DisruptionEvent]:
    route_id = db.scalar(
        select(DisruptionEvent.route_id).where(DisruptionEvent.id == disruption_id)
    )
    if route_id is None:
        raise NotFound("Disruption not found.")
    route = _lock_route(db, route_id)
    disruption = db.scalar(
        select(DisruptionEvent)
        .where(DisruptionEvent.id == disruption_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if disruption is None:
        raise NotFound("Disruption not found.")
    return route, disruption


def _recompute_route_status(db, route: Route) -> None:
    """Route status = worst status among its open disruptions. Flushes first."""
    db.flush()
    statuses = db.scalars(
        select(DisruptionEvent.route_status).where(
            DisruptionEvent.route_id == route.id, DisruptionEvent.status == "open"
        )
    ).all()
    route.status = worst_route_status(statuses)


def _archive_drafts(db, disruption_id: int) -> None:
    db.execute(
        update(ServiceAlert)
        .where(ServiceAlert.disruption_id == disruption_id, ServiceAlert.status == "draft")
        .values(status="archived")
    )


def _add_drafts(db, disruption_id: int, drafts: dict[str, str], tone: str) -> None:
    for channel, message in drafts.items():
        db.add(
            ServiceAlert(
                disruption_id=disruption_id,
                channel=channel,
                tone=tone,
                message_body=message,
                status="draft",
            )
        )


def _route_has_signage(db, route_id: int) -> bool:
    return bool(
        db.scalar(
            select(Stop.id)
            .join(route_stops, route_stops.c.stop_id == Stop.id)
            .where(route_stops.c.route_id == route_id, Stop.has_digital_signage.is_(True))
            .limit(1)
        )
    )


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _CreateContext:
    route_code: str
    route_name: str
    mode: str
    stop_name: str | None
    signage_available: bool


def _load_create_context(route_id: int, stop_id: int | None) -> _CreateContext:
    with get_primary_session() as db:
        route = db.get(Route, route_id)
        if route is None:
            raise NotFound("Route not found.")

        stop = None
        if stop_id is not None:
            stop = db.scalar(
                select(Stop)
                .join(route_stops, route_stops.c.stop_id == Stop.id)
                .where(route_stops.c.route_id == route_id, Stop.id == stop_id)
            )
            if stop is None:
                if db.get(Stop, stop_id) is None:
                    raise Invalid("The selected stop does not exist.")
                raise Invalid("The selected stop is not on the selected route.")

        signage = stop.has_digital_signage if stop else _route_has_signage(db, route_id)
        return _CreateContext(route.route_code, route.name, route.mode,
                              stop.name if stop else None, signage)


def create_disruption(route_id: int, affected_stop_id: int | None, raw_note: str) -> Outcome:
    note = clean_note(raw_note)
    ctx = _load_create_context(route_id, affected_stop_id)  # read transaction closed here

    analysis = ai_service.analyze_disruption(
        ctx.route_code, ctx.route_name, ctx.mode, ctx.stop_name, note
    )
    tone = default_tone(analysis.severity)
    drafts = ai_service.draft_channel_alerts(
        ctx.route_code, ctx.route_name, ctx.stop_name,
        analysis.severity, analysis.summary, tone,
    )
    if not ctx.signage_available:
        drafts.pop("signage", None)

    with get_write_session() as db:
        route = _lock_route(db, route_id)

        stop_clause = (
            DisruptionEvent.affected_stop_id == affected_stop_id
            if affected_stop_id is not None
            else DisruptionEvent.affected_stop_id.is_(None)
        )
        duplicate = db.scalar(
            select(DisruptionEvent)
            .where(
                DisruptionEvent.route_id == route_id,
                DisruptionEvent.status == "open",
                DisruptionEvent.raw_driver_note == note,
                stop_clause,
                DisruptionEvent.created_at >= _now() - DUPLICATE_WINDOW,
            )
            .limit(1)
        )
        if duplicate is not None:  # double-click or resubmitted form
            return Outcome(duplicate.id, changed=False,
                           message="An identical open disruption was just logged; showing it.")

        disruption = DisruptionEvent(
            route_id=route_id,
            affected_stop_id=affected_stop_id,
            raw_driver_note=note,
            severity=analysis.severity,
            route_status=analysis.route_status,
            diversion_summary=analysis.summary,
            summary_source=analysis.source,
            status="open",
        )
        db.add(disruption)
        db.flush()
        _add_drafts(db, disruption.id, drafts, tone)
        _recompute_route_status(db, route)

        # The event carries no model-generated text: drafts are unreviewed until published.
        event = ("disruption_created", {
            "disruption_id": disruption.id,
            "route_code": route.route_code,
            "severity": disruption.severity,
            "route_status": route.status,
        })
        return Outcome(disruption.id, event=event, message="Disruption logged. Review the drafts below.")


# --------------------------------------------------------------------------- #
# Regenerate drafts
# --------------------------------------------------------------------------- #
def regenerate_alerts(disruption_id: int, tone: str) -> Outcome:
    if tone not in ("urgent", "informational"):
        tone = "informational"

    with get_primary_session() as db:
        d = db.scalar(
            select(DisruptionEvent)
            .where(DisruptionEvent.id == disruption_id)
            .options(selectinload(DisruptionEvent.route), selectinload(DisruptionEvent.affected_stop))
        )
        if d is None:
            raise NotFound("Disruption not found.")
        if d.status != "open":
            raise Conflict("This disruption is resolved, so its alerts can no longer be regenerated.")
        signage = (d.affected_stop.has_digital_signage if d.affected_stop
                   else _route_has_signage(db, d.route_id))
        args = (d.route.route_code, d.route.name,
                d.affected_stop.name if d.affected_stop else None,
                d.severity, d.diversion_summary or "", tone)

    drafts = ai_service.draft_channel_alerts(*args)  # no transaction open
    if not signage:
        drafts.pop("signage", None)

    with get_write_session() as db:
        _route, d = _lock_disruption(db, disruption_id)
        if d.status != "open":  # resolved while the model was thinking
            raise Conflict("This disruption was resolved while drafts were being generated.")
        _archive_drafts(db, d.id)  # published alerts stay live until superseded by publishing
        _add_drafts(db, d.id, drafts, tone)
        return Outcome(d.id, message="New drafts generated.")


# --------------------------------------------------------------------------- #
# Publish
# --------------------------------------------------------------------------- #
def publish_alert(alert_id: int) -> Outcome:
    with get_write_session() as db:
        disruption_id = db.scalar(
            select(ServiceAlert.disruption_id).where(ServiceAlert.id == alert_id)
        )
        if disruption_id is None:
            raise NotFound("Alert not found.")
        route, d = _lock_disruption(db, disruption_id)
        alert = db.scalar(
            select(ServiceAlert).where(ServiceAlert.id == alert_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if alert is None:
            raise NotFound("Alert not found.")

        if alert.status == "published":  # idempotent: no second broadcast
            return Outcome(d.id, changed=False, message="That alert is already published.")
        if alert.status != "draft":
            raise Conflict("That draft was replaced by a newer one and can no longer be published.")
        if d.status != "open":
            raise Conflict("This disruption is resolved, so its alerts can no longer be published.")

        # Supersede whatever is live on this channel, then publish (flush between:
        # a unique index allows only one published alert per disruption and channel).
        db.execute(
            update(ServiceAlert)
            .where(ServiceAlert.disruption_id == d.id, ServiceAlert.channel == alert.channel,
                   ServiceAlert.status == "published", ServiceAlert.id != alert.id)
            .values(status="archived")
        )
        db.flush()
        alert.status = "published"
        alert.published_at = _now()
        db.flush()

        event = ("alert_published", {
            "alert_id": alert.id,
            "route_code": route.route_code,
            "channel": alert.channel,
            "tone": alert.tone,
            "message": alert.message_body,
        })
        return Outcome(d.id, event=event, message=f"Published to {alert.channel.upper()}.")


# --------------------------------------------------------------------------- #
# Resolve
# --------------------------------------------------------------------------- #
def resolve_disruption(disruption_id: int) -> Outcome:
    with get_write_session() as db:
        route, d = _lock_disruption(db, disruption_id)
        if d.status == "resolved":  # idempotent: keep the original resolved_at
            return Outcome(d.id, changed=False, message="That disruption was already resolved.")

        d.status = "resolved"
        d.resolved_at = _now()
        _archive_drafts(db, d.id)
        _recompute_route_status(db, route)

        event = ("disruption_resolved", {
            "disruption_id": d.id,
            "route_code": route.route_code,
            "route_status": route.status,
        })
        return Outcome(d.id, event=event, message="Disruption resolved.")
