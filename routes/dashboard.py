from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, g, render_template, request, stream_with_context
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.consistency import read_session
from models.transit import DisruptionEvent, Route, ServiceAlert, Trip, route_stops
from services.sse_service import BrokerFull, broker

dashboard_bp = Blueprint("dashboard", __name__)

TRIP_LOOKBACK = timedelta(minutes=15)
TRIP_LIMIT = 25


@dashboard_bp.get("/")
def index():
    with read_session() as db:
        routes = db.scalars(select(Route).order_by(Route.mode, Route.route_code)).all()
        active_disruptions = db.scalars(
            select(DisruptionEvent)
            .where(DisruptionEvent.status == "open")
            .options(selectinload(DisruptionEvent.route))
            .order_by(DisruptionEvent.created_at.desc())
        ).all()
        # Only alerts that are live: published and for a disruption that is still open.
        recent_published = db.scalars(
            select(ServiceAlert)
            .join(DisruptionEvent, ServiceAlert.disruption_id == DisruptionEvent.id)
            .where(ServiceAlert.status == "published", DisruptionEvent.status == "open")
            .options(selectinload(ServiceAlert.disruption).selectinload(DisruptionEvent.route))
            .order_by(ServiceAlert.published_at.desc())
            .limit(8)
        ).all()

    return render_template(
        "dashboard.html",
        routes=routes,
        active_disruptions=active_disruptions,
        recent_published=recent_published,
    )


@dashboard_bp.get("/routes")
def list_routes():
    with read_session() as db:
        rows = db.execute(
            select(Route, func.count(route_stops.c.stop_id))
            .outerjoin(route_stops, route_stops.c.route_id == Route.id)
            .group_by(Route.id)
            .order_by(Route.mode, Route.route_code)
        ).all()

    return render_template("routes_list.html", rows=rows)


@dashboard_bp.get("/routes/<int:route_id>")
def route_detail(route_id: int):
    with read_session() as db:
        route = db.scalar(
            select(Route).where(Route.id == route_id).options(selectinload(Route.stops))
        )
        if not route:
            abort(404)

        upcoming_trips = db.scalars(
            select(Trip)
            .where(
                Trip.route_id == route_id,
                Trip.scheduled_departure >= datetime.now(timezone.utc) - TRIP_LOOKBACK,
            )
            .order_by(Trip.scheduled_departure.asc())
            .limit(TRIP_LIMIT)
        ).all()

        active_disruptions = db.scalars(
            select(DisruptionEvent)
            .where(DisruptionEvent.route_id == route_id, DisruptionEvent.status == "open")
            .order_by(DisruptionEvent.created_at.desc())
        ).all()

    return render_template(
        "route_detail.html",
        route=route,
        trips=upcoming_trips,
        active_disruptions=active_disruptions,
    )


@dashboard_bp.get("/events/stream")
def event_stream():
    # Authentication is enforced for this endpoint by app.security (401 if signed out).
    last_id = request.headers.get("Last-Event-ID", type=int)
    try:
        sub = broker.subscribe(owner=g.user.id, last_event_id=last_id)
    except BrokerFull:
        abort(503, description="The live feed is at capacity. Try again shortly.")

    response = Response(
        stream_with_context(broker.stream(sub)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    response.call_on_close(lambda: broker.unsubscribe(sub))
    return response
