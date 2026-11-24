"""Test setup.

The environment is forced BEFORE the app is imported so tests can never touch a
developer's real database: DATABASE_URL is replaced by TEST_DATABASE_URL (which
must contain "test" in its name) or by a throwaway SQLite file.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_test_url = os.environ.get("TEST_DATABASE_URL")
if _test_url:
    if "test" not in _test_url.rsplit("/", 1)[-1].lower():
        raise RuntimeError("TEST_DATABASE_URL must point at a database whose name contains 'test'.")
else:
    _test_url = f"sqlite:///{tempfile.mkdtemp(prefix='transit_test_')}/test.db"

os.environ.update(
    APP_ENV="testing",
    SECRET_KEY="test-secret-key-for-pytest-only-0123456789abcdef",
    DATABASE_URL=_test_url,
    READ_REPLICA_URL="",
    LLM_API_KEY="",
    SESSION_COOKIE_SECURE="0",
    APP_TIMEZONE="Europe/Dublin",
)

import pytest  # noqa: E402

from tests.helpers import csrf_from  # noqa: E402

HAVE_DB = importlib.util.find_spec("sqlalchemy") is not None


def pytest_collection_modifyitems(config, items):
    if HAVE_DB:
        return
    skip = pytest.mark.skip(reason="SQLAlchemy is not installed; run in an environment with requirements-dev.txt")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def app():
    if not HAVE_DB:
        pytest.skip("SQLAlchemy is not installed")
    from app import create_app
    from app.db import Base, init_db, write_engine

    application = create_app({"TESTING": True})
    Base.metadata.drop_all(bind=write_engine)
    init_db()
    return application


@pytest.fixture(autouse=True)
def _clean(request):
    """Reset rate limiters, and empty every table after each DB test."""
    from app.security import reset_limiters

    reset_limiters()
    yield
    if "db" not in request.keywords or not HAVE_DB:
        return
    from app.db import Base, write_engine

    with write_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    from werkzeug.security import generate_password_hash

    from app.db import get_write_session
    from models.user import User

    def _make(username="dispatcher1", role="dispatcher", password="correct-horse-battery", active=True):
        with get_write_session() as db:
            db.add(User(username=username, password_hash=generate_password_hash(password),
                        role=role, is_active=active))
        return username, password

    return _make


@pytest.fixture
def login(client, make_user):
    def _login(role="dispatcher", username=None):
        username = username or f"{role}1"
        _, password = make_user(username, role)
        token = csrf_from(client)
        resp = client.post("/login", data={"username": username, "password": password,
                                           "csrf_token": token})
        assert resp.status_code == 302, resp.get_data(as_text=True)
        return client

    return _login


@pytest.fixture
def world(app):
    """Two routes, three stops, one stop with signage; returns ids."""
    from app.db import get_write_session
    from models.transit import Route, Stop, route_stops

    with get_write_session() as db:
        r1 = Route(route_code="B-1", name="Bus One", mode="bus", status="normal")
        r2 = Route(route_code="T-2", name="Tram Two", mode="tram", status="normal")
        s1 = Stop(stop_code="S1", name="Alpha", latitude=1, longitude=1, has_digital_signage=True)
        s2 = Stop(stop_code="S2", name="Beta", latitude=1, longitude=2, has_digital_signage=False)
        s3 = Stop(stop_code="S3", name="Gamma", latitude=1, longitude=3, has_digital_signage=False)
        db.add_all([r1, r2, s1, s2, s3])
        db.flush()
        db.execute(route_stops.insert(), [
            {"route_id": r1.id, "stop_id": s1.id, "stop_sequence": 1},
            {"route_id": r1.id, "stop_id": s2.id, "stop_sequence": 2},
            {"route_id": r2.id, "stop_id": s3.id, "stop_sequence": 1},
        ])
        return {"r1": r1.id, "r2": r2.id, "s1": s1.id, "s2": s2.id, "s3": s3.id}


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the LLM transport. Set fake_llm.responder = callable(system, user) -> dict|None."""
    from services import ai_service

    class Fake:
        responder = staticmethod(lambda system, user: None)
        calls = 0

    def _call(system, user):
        Fake.calls += 1
        return Fake.responder(system, user)

    monkeypatch.setattr(ai_service, "_call_llm", _call)
    Fake.calls = 0
    return Fake
