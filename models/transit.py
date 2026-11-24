from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from models.constants import (
    ALERT_STATUSES,
    CHANNELS,
    DISRUPTION_ROUTE_STATUSES,
    DISRUPTION_STATUSES,
    MODES,
    ROUTE_STATUSES,
    SEVERITIES,
    SUMMARY_SOURCES,
    TONES,
    TRIP_STATUSES,
    in_list,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


route_stops = Table(
    "route_stops",
    Base.metadata,
    Column("route_id", ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True),
    Column("stop_id", ForeignKey("stops.id", ondelete="CASCADE"), primary_key=True),
    Column("stop_sequence", Integer, nullable=False, default=1),
    Index("ix_route_stops_stop_id", "stop_id"),
)


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        CheckConstraint(in_list("mode", MODES), name="ck_routes_mode"),
        CheckConstraint(in_list("status", ROUTE_STATUSES), name="ck_routes_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # Derived: the worst route_status among this route's OPEN disruptions
    # (see services.state.worst_route_status). Never set it by hand.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")

    stops: Mapped[list["Stop"]] = relationship(
        secondary=route_stops, back_populates="routes", order_by=route_stops.c.stop_sequence
    )
    trips: Mapped[list["Trip"]] = relationship(
        back_populates="route", cascade="all, delete-orphan"
    )
    disruptions: Mapped[list["DisruptionEvent"]] = relationship(
        back_populates="route", cascade="all, delete-orphan"
    )


class Stop(Base):
    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    has_digital_signage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    routes: Mapped[list["Route"]] = relationship(
        secondary=route_stops, back_populates="stops"
    )


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint(in_list("status", TRIP_STATUSES), name="ck_trips_status"),
        Index("ix_trips_route_departure", "route_id", "scheduled_departure"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    headsign: Mapped[str] = mapped_column(String(150), nullable=False)
    scheduled_departure: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")

    route: Mapped["Route"] = relationship(back_populates="trips")


class DisruptionEvent(Base):
    __tablename__ = "disruption_events"
    __table_args__ = (
        CheckConstraint(in_list("severity", SEVERITIES), name="ck_disruptions_severity"),
        CheckConstraint(in_list("status", DISRUPTION_STATUSES), name="ck_disruptions_status"),
        CheckConstraint(
            in_list("route_status", DISRUPTION_ROUTE_STATUSES),
            name="ck_disruptions_route_status",
        ),
        CheckConstraint(
            "summary_source IS NULL OR " + in_list("summary_source", SUMMARY_SOURCES),
            name="ck_disruptions_summary_source",
        ),
        Index("ix_disruption_events_route_status", "route_id", "status"),
        Index("ix_disruption_events_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    affected_stop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("stops.id", ondelete="SET NULL"), nullable=True
    )
    raw_driver_note: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="minor")
    # What this disruption implies for the route while it is open.
    route_status: Mapped[str] = mapped_column(String(20), nullable=False, default="delayed")
    diversion_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # "ai", "template", or NULL for rows created before this column existed.
    summary_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    route: Mapped["Route"] = relationship(back_populates="disruptions")
    affected_stop: Mapped[Optional["Stop"]] = relationship()
    alerts: Mapped[list["ServiceAlert"]] = relationship(
        back_populates="disruption",
        cascade="all, delete-orphan",
        order_by="ServiceAlert.id.desc()",
    )


class ServiceAlert(Base):
    __tablename__ = "service_alerts"
    __table_args__ = (
        CheckConstraint(in_list("channel", CHANNELS), name="ck_alerts_channel"),
        CheckConstraint(in_list("tone", TONES), name="ck_alerts_tone"),
        CheckConstraint(in_list("status", ALERT_STATUSES), name="ck_alerts_status"),
        Index("ix_service_alerts_disruption_status", "disruption_id", "status"),
        Index("ix_service_alerts_status_published", "status", "published_at"),
        # At most one live alert per disruption and channel: publishing a new one
        # supersedes (archives) the old one, so passengers never see two versions.
        Index(
            "uq_service_alerts_one_published",
            "disruption_id",
            "channel",
            unique=True,
            postgresql_where=text("status = 'published'"),
            sqlite_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    disruption_id: Mapped[int] = mapped_column(
        ForeignKey("disruption_events.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    tone: Mapped[str] = mapped_column(String(20), nullable=False)
    message_body: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    disruption: Mapped["DisruptionEvent"] = relationship(back_populates="alerts")
