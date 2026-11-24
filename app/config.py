"""Application configuration.

Fail-closed: there are no insecure fallbacks for secrets. In production the app
refuses to start unless SECRET_KEY and DATABASE_URL are set and look sane.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

VALID_ENVS = {"development", "testing", "production"}
_WEAK_SECRET_FRAGMENTS = (
    "dev-secret",
    "dev-only",
    "change-me",
    "changeme",
    "secret-key",
    "not-for-production",
)
_DEV_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/transit_ops"

# Anything that is not explicitly "development" or "testing" is treated as production.
_ENV = os.getenv("APP_ENV", "production").strip().lower()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Config:
    APP_ENV = _ENV
    TESTING = _ENV == "testing"

    SECRET_KEY = os.getenv("SECRET_KEY", "")
    DATABASE_URL = os.getenv("DATABASE_URL") or (
        _DEV_DATABASE_URL if _ENV == "development" else ""
    )
    READ_REPLICA_URL = os.getenv("READ_REPLICA_URL") or DATABASE_URL

    # Sessions / cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", _ENV == "production")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_int("SESSION_HOURS", 8))
    MAX_CONTENT_LENGTH = 64 * 1024

    # Number of reverse proxies in front of the app (enables ProxyFix when > 0).
    TRUSTED_PROXY_COUNT = _int("TRUSTED_PROXY_COUNT", 0)

    # Dev convenience only. Production schema is managed by Alembic.
    AUTO_CREATE_SCHEMA = _bool("AUTO_CREATE_SCHEMA", False)

    # After a write, this user's reads go to the primary for this many seconds.
    READ_AFTER_WRITE_SECONDS = _int("READ_AFTER_WRITE_SECONDS", 30)

    # Timezone used when displaying stored UTC timestamps.
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Dublin")

    # Live feed limits
    SSE_MAX_CLIENTS = _int("SSE_MAX_CLIENTS", 100)
    SSE_MAX_PER_USER = _int("SSE_MAX_PER_USER", 3)

    # LLM
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_API_URL = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
    LLM_TIMEOUT_SECONDS = _int("LLM_TIMEOUT_SECONDS", 15)


class ConfigError(RuntimeError):
    """Raised at startup when configuration is missing or unsafe."""


def validate_config(cfg) -> None:
    """Raise ConfigError listing every problem found. `cfg` is any mapping."""
    problems: list[str] = []
    env = cfg.get("APP_ENV")
    secret = cfg.get("SECRET_KEY") or ""

    if env not in VALID_ENVS:
        problems.append(f"APP_ENV must be one of {sorted(VALID_ENVS)}, got {env!r}.")

    if not secret:
        problems.append("SECRET_KEY is not set.")

    if env == "production":
        if secret and len(secret) < 32:
            problems.append("SECRET_KEY must be at least 32 characters in production.")
        if secret and any(frag in secret.lower() for frag in _WEAK_SECRET_FRAGMENTS):
            problems.append("SECRET_KEY looks like a placeholder; generate a random one.")
        if not cfg.get("DATABASE_URL"):
            problems.append("DATABASE_URL is not set.")
        if not cfg.get("SESSION_COOKIE_SECURE"):
            problems.append("SESSION_COOKIE_SECURE must be enabled in production.")
        if cfg.get("LLM_API_KEY") and not str(cfg.get("LLM_API_URL", "")).startswith("https://"):
            problems.append("LLM_API_URL must use https:// when LLM_API_KEY is set.")

    if problems:
        raise ConfigError("Invalid configuration:\n- " + "\n- ".join(problems))
