from datetime import datetime, timezone

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_read_session, get_write_session
from models.transit import DisruptionEvent, Route, ServiceAlert, Stop
from services.ai_service import analyze_disruption, draft_channel_alerts
from services.sse_service import broker

disruptions_bp = Blueprint("disruptions", __name__)


@disruptions_bp.get("/disruptions/new")
def new_disruption_form():
    with get_read_session() as db:
        routes = db.scalars(select(Route).order_by(Route.route_code)).all()
        stops = db.scalars(select(Stop).order_by(Stop.name)).all()

    return render_template("disruption_new.html", routes=routes, stops=stops)


@disruptions_bp.post("/disruptions")
def create_disruption():
    route_id = request.form.get("route_id", type=int)
    affected_stop_id = request.form.get("affected_stop_id", type=int) or None
    raw_driver_note = (request.form.get("raw_driver_note") or "").strip()

    if not route_id or not raw_driver_note:
        abort(400, description="Route and driver note are required.")

    with get_write_session() as db:
        route = db.get(Route, route_id)
        if not route:
            abort(404)

        stop = db.get(Stop, affected_stop_id) if affected_stop_id else None

        analysis = analyze_disruption(
            route_code=route.route_code,
            route_name=route.name,
            mode=route.mode,
            stop_name=stop.name if stop else None,
            raw_note=raw_driver_note,
        )

        disruption = DisruptionEvent(
            route_id=route.id,
            affected_stop_id=stop.id if stop else None,
            raw_driver_note=raw_driver_note,
            severity=analysis["severity"],
            diversion_summary=analysis["diversion_summary"],
            status="open",
        )
        route.status = analysis["route_status"]
        db.add(disruption)
        db.flush()

        default_tone = "urgent" if disruption.severity == "critical" else "informational"
        drafts = draft_channel_alerts(
            route_code=route.route_code,
            route_name=route.name,
            stop_name=stop.name if stop else None,
            severity=disruption.severity,
            diversion_summary=disruption.diversion_summary or "",
            tone=default_tone,
        )
        for channel, message in drafts.items():
            db.add(
                ServiceAlert(
                    disruption_id=disruption.id,
                    channel=channel,
                    tone=default_tone,
                    message_body=message,
                    status="draft",
                )
            )

        disruption_id = disruption.id
        event_payload = {
            "disruption_id": disruption.id,
            "route_code": route.route_code,
            "severity": disruption.severity,
            "route_status": route.status,
            "summary": disruption.diversion_summary,
        }

    broker.publish("disruption_created", event_payload)
    return redirect(url_for("disruptions.disruption_detail", disruption_id=disruption_id))


@disruptions_bp.get("/disruptions/<int:disruption_id>")
def disruption_detail(disruption_id: int):
    with get_read_session() as db:
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
def generate_alerts(disruption_id: int):
    tone = request.form.get("tone", "informational")
    if tone not in {"urgent", "informational"}:
        tone = "informational"

    with get_write_session() as db:
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

        for existing in disruption.alerts:
            if existing.status == "draft":
                existing.status = "archived"

        drafts = draft_channel_alerts(
            route_code=disruption.route.route_code,
            route_name=disruption.route.name,
            stop_name=disruption.affected_stop.name if disruption.affected_stop else None,
            severity=disruption.severity,
            diversion_summary=disruption.diversion_summary or "",
            tone=tone,
        )
        for channel, message in drafts.items():
            db.add(
                ServiceAlert(
                    disruption_id=disruption.id,
                    channel=channel,
                    tone=tone,
                    message_body=message,
                    status="draft",
                )
            )

    return redirect(url_for("disruptions.disruption_detail", disruption_id=disruption_id))


@disruptions_bp.post("/alerts/<int:alert_id>/publish")
def publish_alert(alert_id: int):
    with get_write_session() as db:
        alert = db.scalar(
            select(ServiceAlert)
            .where(ServiceAlert.id == alert_id)
            .options(
                selectinload(ServiceAlert.disruption).selectinload(
                    DisruptionEvent.route
                )
            )
        )
        if not alert:
            abort(404)

        alert.status = "published"
        alert.published_at = datetime.now(timezone.utc)

        disruption_id = alert.disruption_id
        payload = {
            "alert_id": alert.id,
            "route_code": alert.disruption.route.route_code,
            "channel": alert.channel,
            "tone": alert.tone,
            "message": alert.message_body,
        }

    broker.publish("alert_published", payload)
    return redirect(url_for("disruptions.disruption_detail", disruption_id=disruption_id))


@disruptions_bp.post("/disruptions/<int:disruption_id>/resolve")
def resolve_disruption(disruption_id: int):
    with get_write_session() as db:
        disruption = db.scalar(
            select(DisruptionEvent)
            .where(DisruptionEvent.id == disruption_id)
            .options(selectinload(DisruptionEvent.route))
        )
        if not disruption:
            abort(404)

        disruption.status = "resolved"
        disruption.resolved_at = datetime.now(timezone.utc)

        remaining_open = db.scalars(
            select(DisruptionEvent).where(
                DisruptionEvent.route_id == disruption.route_id,
                DisruptionEvent.status == "open",
                DisruptionEvent.id != disruption.id,
            )
        ).all()

        if not remaining_open:
            disruption.route.status = "normal"

        payload = {
            "disruption_id": disruption.id,
            "route_code": disruption.route.route_code,
            "route_status": disruption.route.status,
        }

    broker.publish("disruption_resolved", payload)
    return redirect(url_for("dashboard.index"))