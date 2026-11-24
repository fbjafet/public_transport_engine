"""Demo data. Run after migrations:  python -m seeds.seed_data

Route statuses are derived from the seeded open disruptions (never hand-set), and
trip times are relative to "now", so re-seed before demos. Users are created
separately with `flask --app run create-user`.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import Config
from app.db import get_write_session, init_db
from models.transit import (
    DisruptionEvent,
    Route,
    ServiceAlert,
    Stop,
    Trip,
    route_stops,
)
from services.state import worst_route_status


def seed() -> None:
    if Config.AUTO_CREATE_SCHEMA:
        init_db()

    with get_write_session() as db:
        if db.scalar(select(Route.id).limit(1)):
            print("Database already seeded. Skipping.")
            return

        now = datetime.now(timezone.utc)

        stops = {
            "S01": Stop(stop_code="S01", name="Connolly Central Station",
                        latitude=53.3514, longitude=-6.2500, has_digital_signage=True),
            "S02": Stop(stop_code="S02", name="O'Connell Bridge",
                        latitude=53.3472, longitude=-6.2591, has_digital_signage=True),
            "S03": Stop(stop_code="S03", name="Heuston Interchange",
                        latitude=53.3464, longitude=-6.2921, has_digital_signage=True),
            "S04": Stop(stop_code="S04", name="St. Stephen's Green",
                        latitude=53.3382, longitude=-6.2591, has_digital_signage=True),
            "S05": Stop(stop_code="S05", name="Dun Laoghaire Pier",
                        latitude=53.2944, longitude=-6.1339, has_digital_signage=True),
            "S06": Stop(stop_code="S06", name="Blanchardstown Centre",
                        latitude=53.3924, longitude=-6.3910, has_digital_signage=False),
        }
        db.add_all(stops.values())
        db.flush()

        r_bus = Route(route_code="B-39A", name="City Centre – Blanchardstown", mode="bus", status="normal")
        r_rail = Route(route_code="R-DART", name="Coastal Commuter Rail", mode="rail", status="normal")
        r_tram = Route(route_code="T-RED", name="Red Line Crosstown Tram", mode="tram", status="normal")
        db.add_all([r_bus, r_rail, r_tram])
        db.flush()

        mappings = [
            (r_bus.id, stops["S04"].id, 1),
            (r_bus.id, stops["S02"].id, 2),
            (r_bus.id, stops["S06"].id, 3),
            (r_rail.id, stops["S01"].id, 1),
            (r_rail.id, stops["S05"].id, 2),
            (r_tram.id, stops["S01"].id, 1),
            (r_tram.id, stops["S02"].id, 2),
            (r_tram.id, stops["S03"].id, 3),
        ]
        db.execute(
            route_stops.insert(),
            [{"route_id": r, "stop_id": s, "stop_sequence": q} for r, s, q in mappings],
        )

        db.add_all([
            Trip(route_id=r_bus.id, headsign="Blanchardstown Centre",
                 scheduled_departure=now + timedelta(minutes=8), delay_minutes=14, status="delayed"),
            Trip(route_id=r_bus.id, headsign="St. Stephen's Green",
                 scheduled_departure=now + timedelta(minutes=22), delay_minutes=5, status="scheduled"),
            Trip(route_id=r_rail.id, headsign="Dun Laoghaire",
                 scheduled_departure=now + timedelta(minutes=5), delay_minutes=12, status="delayed"),
            Trip(route_id=r_rail.id, headsign="Connolly Central",
                 scheduled_departure=now + timedelta(minutes=18), delay_minutes=0, status="scheduled"),
            Trip(route_id=r_tram.id, headsign="Heuston Interchange",
                 scheduled_departure=now + timedelta(minutes=3), delay_minutes=0, status="active"),
            Trip(route_id=r_tram.id, headsign="Connolly Central",
                 scheduled_departure=now + timedelta(minutes=11), delay_minutes=0, status="scheduled"),
        ])

        bus_disruption = DisruptionEvent(
            route_id=r_bus.id,
            affected_stop_id=stops["S02"].id,
            raw_driver_note="Emergency water main roadworks blocking northbound lane at O'Connell Bridge; diverting via Bachelors Walk.",
            severity="major",
            route_status="diverted",
            diversion_summary="Route B-39A is diverted via Bachelors Walk, bypassing O'Connell Bridge northbound. Board at adjacent stops.",
            summary_source="template",
            status="open",
        )
        rail_disruption = DisruptionEvent(
            route_id=r_rail.id,
            affected_stop_id=stops["S01"].id,
            raw_driver_note="Signal fault outside Connolly Station; trains held 12 mins southbound.",
            severity="minor",
            route_status="delayed",
            diversion_summary="Route R-DART is experiencing delays around Connolly Central Station. Services remain running on the normal alignment.",
            summary_source="template",
            status="open",
        )
        db.add_all([bus_disruption, rail_disruption])
        db.flush()

        db.add_all([
            ServiceAlert(
                disruption_id=bus_disruption.id, channel="web", tone="informational",
                message_body="Service Update — Route B-39A: Due to emergency roadworks at O'Connell Bridge, northbound buses are diverting via Bachelors Walk. Please allow an extra 15 minutes.",
                status="published", published_at=now - timedelta(minutes=6),
            ),
            ServiceAlert(
                disruption_id=bus_disruption.id, channel="signage", tone="urgent",
                message_body="B-39A DIVERTED: O'CONNELL BRIDGE NORTHBOUND CLOSED — BOARD AT BACHELORS WALK",
                status="draft",
            ),
        ])

        # Derive route status exactly as the application does.
        db.flush()
        for route, disruption in ((r_bus, bus_disruption), (r_rail, rail_disruption)):
            route.status = worst_route_status([disruption.route_status])

    print("Seed data inserted successfully.")


if __name__ == "__main__":
    seed()
