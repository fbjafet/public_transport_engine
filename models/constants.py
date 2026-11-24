"""Single source of truth for every status/enum literal in the app.

Models (CHECK constraints), the state machine, the AI validation layer and the
migrations all read from here. This module must stay free of third-party imports.
"""

MODES = ("bus", "rail", "tram")
ROUTE_STATUSES = ("normal", "delayed", "diverted", "suspended")
# What a single disruption can imply for its route (never "normal").
DISRUPTION_ROUTE_STATUSES = ("delayed", "diverted", "suspended")
TRIP_STATUSES = ("scheduled", "active", "delayed", "cancelled")
SEVERITIES = ("minor", "major", "critical")
DISRUPTION_STATUSES = ("open", "resolved")
SUMMARY_SOURCES = ("ai", "template")
CHANNELS = ("web", "sms", "signage")
TONES = ("urgent", "informational")
ALERT_STATUSES = ("draft", "published", "archived")
USER_ROLES = ("dispatcher", "viewer")


def in_list(column: str, values) -> str:
    """Build the SQL text for a CHECK constraint: column IN ('a', 'b')."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"
