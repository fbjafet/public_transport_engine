from flask import Blueprint, Response, abort, render_template, stream_with_context
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_read_session
from models.transit import DisruptionEvent, Route, ServiceAlert, Trip
from services.sse_service import broker

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
def index():
    with get_read_session() as db:
        routes = db.scalars(select(Route).order_by(Route.mode, Route.route_code)).all()
        active_disruptions = db.scalars(
            select(DisruptionEvent)
            .where(DisruptionEvent.status == "open")
            .options(
                selectinload(DisruptionEvent.route),
                selectinload(DisruptionEvent.affected_stop),
                selectinload(DisruptionEvent.alerts),
            )
            .order_by(DisruptionEvent.created_at.desc())
        ).all()
        recent_published = db.scalars(
            select(ServiceAlert)
            .where(ServiceAlert.status == "published")
            .options(
                selectinload(ServiceAlert.disruption).selectinload(
                    DisruptionEvent.route
                )
            )
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
    with get_read_session() as db:
        routes = db.scalars(
            select(Route)
            .options(selectinload(Route.stops))
            .order_by(Route.mode, Route.route_code)
        ).all()

    return render_template("routes_list.html", routes=routes)


@dashboard_bp.get("/routes/<int:route_id>")
def route_detail(route_id: int):
    with get_read_session() as db:
        route = db.scalar(
            select(Route)
            .where(Route.id == route_id)
            .options(selectinload(Route.stops))
        )
        if not route:
            abort(404)

        upcoming_trips = db.scalars(
            select(Trip)
            .where(Trip.route_id == route_id)
            .order_by(Trip.scheduled_departure.asc())
        ).all()

        active_disruptions = db.scalars(
            select(DisruptionEvent)
            .where(
                DisruptionEvent.route_id == route_id,
                DisruptionEvent.status == "open",
            )
            .options(selectinload(DisruptionEvent.affected_stop))
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
    return Response(
        stream_with_context(broker.stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )