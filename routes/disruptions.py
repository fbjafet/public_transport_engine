"""HTTP layer only: parse input, call the service, broadcast, redirect."""
import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.consistency import mark_write, read_session
from app.security import llm_action_limit, require_dispatcher
from models.transit import DisruptionEvent, Route, Stop
from services import disruption_service as svc
from services.sse_service import broker

log = logging.getLogger(__name__)
disruptions_bp = Blueprint("disruptions", __name__)


def _finish(outcome: svc.Outcome, endpoint: str = "disruptions.disruption_detail"):
    """Common tail for every write: stamp read-your-writes, broadcast, flash, redirect."""
    mark_write()
    if outcome.event is not None:
        try:
            broker.publish(*outcome.event)
        except Exception:  # the write is committed; a feed hiccup must not turn it into a 500
            log.exception("failed to broadcast %s", outcome.event[0])
    if outcome.message:
        flash(outcome.message, "info" if outcome.changed else "notice")
    if endpoint == "dashboard.index":
        return redirect(url_for(endpoint))
    return redirect(url_for(endpoint, disruption_id=outcome.disruption_id))


@disruptions_bp.get("/disruptions/new")
def new_disruption_form():
    require_dispatcher()
    with read_session() as db:
        routes = db.scalars(select(Route).order_by(Route.route_code)).all()
        stops = db.scalars(
            select(Stop).options(selectinload(Stop.routes)).order_by(Stop.name)
        ).all()
    return render_template("disruption_new.html", routes=routes, stops=stops,
                           max_note=svc.MAX_NOTE_LENGTH)


@disruptions_bp.post("/disruptions")
@llm_action_limit
def create_disruption():
    route_id = request.form.get("route_id", type=int)
    if not route_id:
        abort(400, description="Choose a route.")
    stop_id = request.form.get("affected_stop_id", type=int) or None
    outcome = svc.create_disruption(route_id, stop_id, request.form.get("raw_driver_note"))
    return _finish(outcome)


@disruptions_bp.get("/disruptions/<int:disruption_id>")
def disruption_detail(disruption_id: int):
    with read_session() as db:
        disruption = db.scalar(
            select(DisruptionEvent)
            .where(DisruptionEvent.id == disruption_id)
            .options(
                selectinload(DisruptionEvent.route),
                selectinload(DisruptionEvent.affected_stop),
                selectinload(DisruptionEvent.alerts),
            )
        )
        if not disruption:
            abort(404)
    return render_template("disruption_detail.html", disruption=disruption)


@disruptions_bp.post("/disruptions/<int:disruption_id>/alerts")
@llm_action_limit
def generate_alerts(disruption_id: int):
    outcome = svc.regenerate_alerts(disruption_id, request.form.get("tone", "informational"))
    return _finish(outcome)


@disruptions_bp.post("/alerts/<int:alert_id>/publish")
def publish_alert(alert_id: int):
    return _finish(svc.publish_alert(alert_id))


@disruptions_bp.post("/disruptions/<int:disruption_id>/resolve")
def resolve_disruption(disruption_id: int):
    return _finish(svc.resolve_disruption(disruption_id), endpoint="dashboard.index")
