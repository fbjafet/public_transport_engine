"""Display helper: timestamps are stored in UTC and shown in the operator's timezone."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def make_localtime_filter(tz_name: str):
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # unknown name or no tz database (e.g. Windows without tzdata)
        tz = timezone.utc

    def localtime(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
        if value is None:
            return ""
        if value.tzinfo is None:  # SQLite returns naive values; they are UTC
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(tz).strftime(fmt)

    return localtime
