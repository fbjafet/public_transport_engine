"""Read-your-writes support when a read replica is configured.

After a user writes, redirects land on pages that read data. If those reads hit
a lagging replica the user sees a 404 or stale data. mark_write() stamps the
user's session; read_session() then serves that user from the primary until the
stamp expires.
"""
import time
from contextlib import contextmanager

from flask import current_app, session

from app.db import get_primary_session, get_read_session

_KEY = "primary_until"


def mark_write() -> None:
    window = current_app.config.get("READ_AFTER_WRITE_SECONDS", 30)
    session[_KEY] = time.time() + window


def _use_primary() -> bool:
    try:
        return float(session.get(_KEY, 0)) > time.time()
    except (TypeError, ValueError):
        return False


@contextmanager
def read_session():
    manager = get_primary_session() if _use_primary() else get_read_session()
    with manager as db:
        yield db
