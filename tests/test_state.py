import pytest

from services.state import (
    ALLOWED_ROUTE_STATUS,
    can_transition_alert,
    clamp_route_status,
    default_tone,
    max_severity,
    worst_route_status,
)


def test_worst_status_wins():
    assert worst_route_status([]) == "normal"
    assert worst_route_status(["delayed"]) == "delayed"
    assert worst_route_status(["delayed", "suspended", "diverted"]) == "suspended"


def test_max_severity():
    assert max_severity("minor", "critical") == "critical"
    assert max_severity("major", "minor") == "major"


@pytest.mark.parametrize("severity,given,expected", [
    ("minor", "suspended", "delayed"),
    ("minor", "normal", "delayed"),
    ("minor", None, "delayed"),
    ("minor", "free text", "delayed"),
    ("major", "suspended", "diverted"),
    ("major", "delayed", "delayed"),
    ("critical", "delayed", "diverted"),
    ("critical", "suspended", "suspended"),
    ("critical", None, "suspended"),
])
def test_clamp_route_status(severity, given, expected):
    assert clamp_route_status(severity, given) == expected
    assert expected in ALLOWED_ROUTE_STATUS[severity]


def test_alert_transitions():
    assert can_transition_alert("draft", "published")
    assert can_transition_alert("published", "archived")
    assert not can_transition_alert("archived", "published")
    assert not can_transition_alert("published", "draft")
    assert not can_transition_alert("nonsense", "published")


def test_default_tone():
    assert default_tone("critical") == "urgent"
    assert default_tone("major") == "informational"
