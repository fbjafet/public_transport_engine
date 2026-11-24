"""Users table, derived route status, summary provenance, indexes, FK ondelete.

Data changes (all reversible except the duplicate-alert clean-up):
* disruption_events.route_status is backfilled from severity.
* routes.status is recomputed as the worst route_status of each route's open
  disruptions (so hand-set or stale values are corrected).
* If a disruption has several PUBLISHED alerts on one channel, only the newest
  stays published; older ones become archived (needed for the unique index).

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

FKS = [
    # (table, constraint name, column, referred table, ondelete)
    ("trips", "trips_route_id_fkey", "route_id", "routes", "CASCADE"),
    ("disruption_events", "disruption_events_route_id_fkey", "route_id", "routes", "CASCADE"),
    ("disruption_events", "disruption_events_affected_stop_id_fkey", "affected_stop_id", "stops", "SET NULL"),
    ("service_alerts", "service_alerts_disruption_id_fkey", "disruption_id", "disruption_events", "CASCADE"),
]


def upgrade() -> None:
    # --- users ---------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username"),
        sa.CheckConstraint("role IN ('dispatcher', 'viewer')", name="ck_users_role"),
    )

    # --- disruption_events: route_status + summary_source ----------------------
    op.add_column("disruption_events", sa.Column("route_status", sa.String(20), nullable=True))
    op.add_column("disruption_events", sa.Column("summary_source", sa.String(20), nullable=True))
    op.execute(
        """
        UPDATE disruption_events SET route_status = CASE severity
            WHEN 'critical' THEN 'suspended'
            WHEN 'major' THEN 'diverted'
            ELSE 'delayed' END
        """
    )
    op.alter_column("disruption_events", "route_status", nullable=False)
    op.create_check_constraint(
        "ck_disruptions_route_status", "disruption_events",
        "route_status IN ('delayed', 'diverted', 'suspended')",
    )
    op.create_check_constraint(
        "ck_disruptions_summary_source", "disruption_events",
        "summary_source IS NULL OR summary_source IN ('ai', 'template')",
    )

    # --- derive routes.status from open disruptions -----------------------------
    op.execute(
        """
        UPDATE routes SET status = COALESCE((
            SELECT CASE MAX(CASE d.route_status
                              WHEN 'suspended' THEN 3 WHEN 'diverted' THEN 2 ELSE 1 END)
                     WHEN 3 THEN 'suspended' WHEN 2 THEN 'diverted' ELSE 'delayed' END
            FROM disruption_events d
            WHERE d.route_id = routes.id AND d.status = 'open'
        ), 'normal')
        """
    )

    # --- foreign keys: add ON DELETE behaviour ---------------------------------
    for table, name, column, referred, ondelete in FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, referred, [column], ["id"], ondelete=ondelete)

    # --- one published alert per disruption and channel -------------------------
    op.execute(
        """
        UPDATE service_alerts SET status = 'archived'
        WHERE status = 'published' AND id NOT IN (
            SELECT MAX(id) FROM service_alerts WHERE status = 'published'
            GROUP BY disruption_id, channel
        )
        """
    )
    op.create_index(
        "uq_service_alerts_one_published", "service_alerts", ["disruption_id", "channel"],
        unique=True, postgresql_where=sa.text("status = 'published'"),
    )

    # --- indexes ---------------------------------------------------------------
    op.create_index("ix_route_stops_stop_id", "route_stops", ["stop_id"])
    op.create_index("ix_trips_route_departure", "trips", ["route_id", "scheduled_departure"])
    op.create_index("ix_disruption_events_route_status", "disruption_events", ["route_id", "status"])
    op.create_index("ix_disruption_events_status_created", "disruption_events", ["status", "created_at"])
    op.create_index("ix_service_alerts_disruption_status", "service_alerts", ["disruption_id", "status"])
    op.create_index("ix_service_alerts_status_published", "service_alerts", ["status", "published_at"])


def downgrade() -> None:
    for name, table in [
        ("ix_service_alerts_status_published", "service_alerts"),
        ("ix_service_alerts_disruption_status", "service_alerts"),
        ("ix_disruption_events_status_created", "disruption_events"),
        ("ix_disruption_events_route_status", "disruption_events"),
        ("ix_trips_route_departure", "trips"),
        ("ix_route_stops_stop_id", "route_stops"),
        ("uq_service_alerts_one_published", "service_alerts"),
    ]:
        op.drop_index(name, table_name=table)

    for table, name, column, referred, _ondelete in FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, referred, [column], ["id"])

    op.drop_constraint("ck_disruptions_summary_source", "disruption_events", type_="check")
    op.drop_constraint("ck_disruptions_route_status", "disruption_events", type_="check")
    op.drop_column("disruption_events", "summary_source")
    op.drop_column("disruption_events", "route_status")
    op.drop_table("users")
