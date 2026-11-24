import pytest

from app.config import ConfigError, validate_config
from app.security import SlidingWindowLimiter, fingerprint, is_safe_next
from app.timeutil import make_localtime_filter

GOOD = {
    "APP_ENV": "production",
    "SECRET_KEY": "k" * 8 + "x9Qz" * 8,
    "DATABASE_URL": "postgresql+psycopg://u:p@db/x",
    "SESSION_COOKIE_SECURE": True,
    "LLM_API_KEY": "",
    "LLM_API_URL": "https://api.example.com/v1",
}


def test_valid_production_config_passes():
    validate_config(GOOD)


@pytest.mark.parametrize("change,fragment", [
    ({"SECRET_KEY": ""}, "SECRET_KEY is not set"),
    ({"SECRET_KEY": "short"}, "at least 32"),
    ({"SECRET_KEY": "dev-secret-key-change-me-" + "a" * 20}, "placeholder"),
    ({"DATABASE_URL": ""}, "DATABASE_URL"),
    ({"SESSION_COOKIE_SECURE": False}, "SESSION_COOKIE_SECURE"),
    ({"APP_ENV": "staging"}, "APP_ENV"),
    ({"LLM_API_KEY": "k", "LLM_API_URL": "http://insecure"}, "https"),
])
def test_bad_production_config_is_rejected(change, fragment):
    with pytest.raises(ConfigError) as exc:
        validate_config({**GOOD, **change})
    assert fragment in str(exc.value)


def test_development_still_requires_a_secret_key():
    with pytest.raises(ConfigError):
        validate_config({**GOOD, "APP_ENV": "development", "SECRET_KEY": ""})


def test_all_problems_are_reported_together():
    with pytest.raises(ConfigError) as exc:
        validate_config({**GOOD, "SECRET_KEY": "", "DATABASE_URL": ""})
    assert "SECRET_KEY" in str(exc.value) and "DATABASE_URL" in str(exc.value)


@pytest.mark.parametrize("target,ok", [
    ("/routes", True), ("/disruptions/3?x=1", True),
    ("//evil.example", False), ("https://evil.example", False), ("/\\evil", False),
    ("javascript:alert(1)", False), ("", False), (None, False), ("/ok\r\nSet-Cookie: a=b", False),
])
def test_is_safe_next(target, ok):
    assert is_safe_next(target) is ok


def test_limiter_blocks_then_recovers():
    now = [0.0]
    lim = SlidingWindowLimiter(2, 10, clock=lambda: now[0])
    assert lim.hit("k") and lim.hit("k") and not lim.hit("k")
    assert lim.hit("other")
    now[0] = 11
    assert lim.hit("k")


def test_limiter_blocked_and_record_are_separate():
    lim = SlidingWindowLimiter(1, 10)
    assert not lim.blocked("k")
    lim.record("k")
    assert lim.blocked("k")
    lim.clear("k")
    assert not lim.blocked("k")


def test_fingerprint_changes_with_hash():
    assert fingerprint("a") != fingerprint("b") and fingerprint("a") == fingerprint("a")


def test_localtime_filter_converts_utc_to_dublin_summer_time():
    from datetime import datetime, timezone

    f = make_localtime_filter("Europe/Dublin")
    assert f(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc), "%H:%M") == "13:00"
    assert f(datetime(2026, 7, 1, 12, 0), "%H:%M") == "13:00"  # naive treated as UTC
    assert f(None) == ""


def test_localtime_filter_falls_back_to_utc_for_unknown_zone():
    from datetime import datetime, timezone

    f = make_localtime_filter("Not/AZone")
    assert f(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc), "%H:%M") == "12:00"
