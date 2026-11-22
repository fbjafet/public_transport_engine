from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

route_stops = Table(
    "route_stops",
    Base.metadata,
    Column("route_id", ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True),
    Column("stop_id", ForeignKey("stops.id", ondelete="CASCADE"), primary_key=True),
    Column("stop_sequence", Integer, nullable=False, default=1),
)


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        CheckConstraint("mode IN ('bus', 'rail', 'tram')", name="ck_routes_mode"),
        CheckConstraint(
            "status IN ('normal', 'delayed', 'diverted', 'suspended')",
            name="ck_routes_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
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
        CheckConstraint(
            "status IN ('scheduled', 'active', 'delayed', 'cancelled')",
            name="ck_trips_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"), nullable=False)
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
        CheckConstraint(
            "severity IN ('minor', 'major', 'critical')",
            name="ck_disruptions_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved')", name="ck_disruptions_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"), nullable=False)
    affected_stop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("stops.id"), nullable=True
    )
    raw_driver_note: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="minor")
    diversion_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
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
        CheckConstraint(
            "channel IN ('web', 'sms', 'signage')", name="ck_alerts_channel"
        ),
        CheckConstraint(
            "tone IN ('urgent', 'informational')", name="ck_alerts_tone"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_alerts_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    disruption_id: Mapped[int] = mapped_column(
        ForeignKey("disruption_events.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    tone: Mapped[str] = mapped_column(String(20), nullable=False)
    message_body: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    disruption: Mapped["DisruptionEvent"] = relationship(back_populates="alerts")