"""The rules of the system as pure functions: no database, no Flask.

Disruption:  open -> resolved                      (one way, idempotent)
Alert:       draft -> published -> archived        (draft -> archived also allowed)
Route status is never set directly: it is the worst route_status of the route's
open disruptions, or "normal" when there are none.
"""
from typing import Iterable

from models.constants import ALERT_STATUSES, SEVERITIES

SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}
ROUTE_STATUS_RANK = {"normal": 0, "delayed": 1, "diverted": 2, "suspended": 3}

# Route status implied by a severity when nothing better is known.
DEFAULT_ROUTE_STATUS = {"minor": "delayed", "major": "diverted", "critical": "suspended"}

# Which route statuses are coherent with each severity.
ALLOWED_ROUTE_STATUS = {
    "minor": ("delayed",),
    "major": ("delayed", "diverted"),
    "critical": ("diverted", "suspended"),
}

ALERT_TRANSITIONS = {
    "draft": {"published", "archived"},
    "published": {"archived"},
    "archived": set(),
}
assert set(ALERT_TRANSITIONS) == set(ALERT_STATUSES)


def worst_route_status(statuses: Iterable[str]) -> str:
    """Highest-ranked status, or "normal" for an empty input."""
    return max(statuses, key=ROUTE_STATUS_RANK.__getitem__, default="normal")


def max_severity(a: str, b: str) -> str:
    return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b


def clamp_route_status(severity: str, route_status: str | None) -> str:
    """Return a route status that is coherent with the severity.

    A value that is missing or not allowed for this severity is replaced by the
    nearest allowed one (e.g. critical + "delayed" -> "diverted").
    """
    allowed = ALLOWED_ROUTE_STATUS[severity]
    if route_status in allowed:
        return route_status
    if route_status in ROUTE_STATUS_RANK and route_status != "normal":
        rank = ROUTE_STATUS_RANK[route_status]
        return min(allowed, key=lambda s: abs(ROUTE_STATUS_RANK[s] - rank))
    return DEFAULT_ROUTE_STATUS[severity]


def can_transition_alert(current: str, new: str) -> bool:
    return new in ALERT_TRANSITIONS.get(current, set())


def default_tone(severity: str) -> str:
    return "urgent" if severity == "critical" else "informational"
