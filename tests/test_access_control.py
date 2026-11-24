"""Auth gate, CSRF, roles and headers, tested on a stub app (no database needed)."""
import pytest
from flask import Blueprint, Flask

from app.security import CurrentUser, fingerprint, init_security, llm_action_limit, require_dispatcher


@pytest.fixture
def stub(monkeypatch):
    users = {
        1: CurrentUser(1, "dee", "dispatcher", fingerprint("h1")),
        2: CurrentUser(2, "vic", "viewer", fingerprint("h2")),
    }
    monkeypatch.setattr("app.security._fetch_user", lambda uid: users.get(uid))

    app = Flask(__name__)
    app.config.update(SECRET_KEY="x" * 40, TESTING=True)

    auth = Blueprint("auth", __name__)
    auth.add_url_rule("/login", "login", lambda: "login", methods=["GET", "POST"])
    auth.add_url_rule("/logout", "logout", lambda: "bye", methods=["POST"])
    dash = Blueprint("dashboard", __name__)
    dash.add_url_rule("/", "index", lambda: "home")
    dash.add_url_rule("/events/stream", "event_stream", lambda: "stream")
    dis = Blueprint("disruptions", __name__)
    dis.add_url_rule("/disruptions", "create", lambda: "created", methods=["POST"])
    dis.add_url_rule("/llm", "llm", llm_action_limit(lambda: "ok"), methods=["POST"])

    def new_form():
        require_dispatcher()
        return "form"

    dis.add_url_rule("/disruptions/new", "new", new_form)
    for bp in (auth, dash, dis):
        app.register_blueprint(bp)
    app.add_url_rule("/healthz", "healthz", lambda: "ok")
    init_security(app)
    app.users = users
    return app


def sign_in(client, uid=1, pw="h1", token="tok"):
    with client.session_transaction() as s:
        s["uid"] = uid
        s["pw"] = fingerprint(pw)
        s["_csrf"] = token


@pytest.fixture
def client(stub):
    return stub.test_client()


# ---- default deny --------------------------------------------------------------
def test_anonymous_page_redirects_to_login_with_next(client):
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_anonymous_post_is_redirected_not_executed(client):
    r = client.post("/disruptions", data={"csrf_token": "x"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_anonymous_event_stream_gets_401(client):
    r = client.get("/events/stream")
    assert r.status_code == 401 and r.get_json()["error"]


def test_public_endpoints_are_reachable(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/static/does-not-exist.css").status_code == 404  # 404, not a login redirect


# ---- CSRF ------------------------------------------------------------------------
def test_login_post_requires_csrf(client):
    assert client.post("/login", data={"username": "a"}).status_code == 400


@pytest.mark.parametrize("kwargs,status", [
    ({"data": {"csrf_token": "tok"}}, 200),
    ({"headers": {"X-CSRF-Token": "tok"}}, 200),
    ({"data": {"csrf_token": "wrong"}}, 400),
    ({"data": {}}, 400),
    ({"data": {"csrf_token": ""}}, 400),
])
def test_dispatcher_post_csrf(client, kwargs, status):
    sign_in(client)
    assert client.post("/disruptions", **kwargs).status_code == status


def test_session_without_csrf_token_rejects_empty_submission(client):
    sign_in(client, token="")
    assert client.post("/disruptions", data={"csrf_token": ""}).status_code == 400


# ---- roles -----------------------------------------------------------------------
def test_viewer_cannot_write_even_with_valid_csrf(client):
    sign_in(client, uid=2, pw="h2")
    assert client.post("/disruptions", data={"csrf_token": "tok"}).status_code == 403
    assert client.get("/").status_code == 200  # but may read


def test_viewer_cannot_open_dispatcher_only_page(client):
    sign_in(client, uid=2, pw="h2")
    assert client.get("/disruptions/new").status_code == 403


def test_viewer_can_sign_out(client):
    sign_in(client, uid=2, pw="h2")
    assert client.post("/logout", data={"csrf_token": "tok"}).status_code == 200


def test_dispatcher_can_open_form(client):
    sign_in(client)
    assert client.get("/disruptions/new").status_code == 200


# ---- session validity --------------------------------------------------------------
def test_password_change_invalidates_existing_session(client):
    sign_in(client, uid=1, pw="old-password-hash")
    assert client.get("/").status_code == 302


def test_deactivated_user_is_signed_out(client, stub):
    sign_in(client)
    assert client.get("/").status_code == 200
    del stub.users[1]
    assert client.get("/").status_code == 302


def test_non_integer_uid_in_session_is_rejected(client):
    with client.session_transaction() as s:
        s["uid"] = "1 OR 1=1"
        s["pw"] = fingerprint("h1")
    assert client.get("/").status_code == 302


# ---- headers ---------------------------------------------------------------------
def test_security_headers(client):
    sign_in(client)
    h = client.get("/").headers
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert "'unsafe-inline'" not in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Cache-Control"] == "no-store"


# ---- LLM action limit -------------------------------------------------------------
def test_llm_actions_are_rate_limited_per_user(client):
    sign_in(client)
    codes = [client.post("/llm", data={"csrf_token": "tok"}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
