"""Baseline: the schema as created by the original create_all() at app startup.

Existing databases that already have these tables must NOT run this migration.
Mark them as being at this revision instead:

    alembic stamp 0001
    alembic upgrade head

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("route_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("route_code"),
        sa.CheckConstraint("mode IN ('bus', 'rail', 'tram')", name="ck_routes_mode"),
        sa.CheckConstraint(
            "status IN ('normal', 'delayed', 'diverted', 'suspended')", name="ck_routes_status"
        ),
    )
    op.create_table(
        "stops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stop_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("has_digital_signage", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("stop_code"),
    )
    op.create_table(
        "route_stops",
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.Integer(), nullable=False),
        sa.Column("stop_sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("route_id", "stop_id"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name="route_stops_route_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stop_id"], ["stops.id"], name="route_stops_stop_id_fkey", ondelete="CASCADE"),
    )
    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("headsign", sa.String(150), nullable=False),
        sa.Column("scheduled_departure", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delay_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name="trips_route_id_fkey"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'active', 'delayed', 'cancelled')", name="ck_trips_status"
        ),
    )
    op.create_table(
        "disruption_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("affected_stop_id", sa.Integer(), nullable=True),
        sa.Column("raw_driver_note", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("diversion_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name="disruption_events_route_id_fkey"),
        sa.ForeignKeyConstraint(["affected_stop_id"], ["stops.id"], name="disruption_events_affected_stop_id_fkey"),
        sa.CheckConstraint("severity IN ('minor', 'major', 'critical')", name="ck_disruptions_severity"),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_disruptions_status"),
    )
    op.create_table(
        "service_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("disruption_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("tone", sa.String(20), nullable=False),
        sa.Column("message_body", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["disruption_id"], ["disruption_events.id"], name="service_alerts_disruption_id_fkey"),
        sa.CheckConstraint("channel IN ('web', 'sms', 'signage')", name="ck_alerts_channel"),
        sa.CheckConstraint("tone IN ('urgent', 'informational')", name="ck_alerts_tone"),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_alerts_status"),
    )


def downgrade() -> None:
    for table in ("service_alerts", "disruption_events", "trips", "route_stops", "stops", "routes"):
        op.drop_table(table)
