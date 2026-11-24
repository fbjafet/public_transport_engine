import pytest

from tests.helpers import csrf_from

pytestmark = pytest.mark.db


def _post_login(client, username, password, **extra):
    return client.post("/login", data={"username": username, "password": password,
                                       "csrf_token": csrf_from(client), **extra})


def test_valid_login_then_logout(client, make_user):
    user, pw = make_user()
    assert _post_login(client, user, pw).status_code == 302
    assert client.get("/").status_code == 200
    with client.session_transaction() as s:
        token = s["_csrf"]
    assert client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert client.get("/").status_code == 302  # signed out


def test_logout_requires_csrf(client, login):
    login()
    assert client.post("/logout", data={}).status_code == 400


def test_unknown_user_and_wrong_password_look_identical(client, make_user):
    user, _ = make_user()
    a = _post_login(client, user, "wrong-password-123")
    b = _post_login(client, "nobody", "wrong-password-123")
    assert a.status_code == b.status_code == 401
    assert "Invalid username or password." in a.get_data(as_text=True)
    assert "Invalid username or password." in b.get_data(as_text=True)


def test_inactive_user_cannot_sign_in(client, make_user):
    user, pw = make_user(active=False)
    assert _post_login(client, user, pw).status_code == 401


def test_repeated_failures_lock_the_account_out_briefly(client, make_user):
    user, pw = make_user()
    for _ in range(5):
        assert _post_login(client, user, "bad-password-xyz").status_code == 401
    assert _post_login(client, user, pw).status_code == 429  # even the right password


def test_next_parameter_cannot_redirect_off_site(client, make_user):
    user, pw = make_user()
    r = _post_login(client, user, pw, next="https://evil.example/")
    assert r.status_code == 302 and r.headers["Location"] == "/"


def test_next_parameter_allows_local_paths(client, make_user):
    user, pw = make_user()
    r = _post_login(client, user, pw, next="/routes")
    assert r.headers["Location"] == "/routes"


def test_anonymous_requests_cannot_reach_anything_but_login(client):
    for path in ("/", "/routes", "/routes/1", "/disruptions/new", "/disruptions/1"):
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    assert client.get("/events/stream").status_code == 401
    assert client.post("/disruptions/1/resolve").status_code == 302
    assert client.post("/alerts/1/publish").status_code == 302


def test_error_pages_do_not_leak_internals(client, login):
    login()
    r = client.get("/disruptions/424242")
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert "Traceback" not in body and "werkzeug" not in body.lower()


def test_create_user_cli(app, make_user):
    runner = app.test_cli_runner()
    ok = runner.invoke(args=["create-user", "newbie", "--role", "dispatcher"],
                       input="a-long-enough-password\na-long-enough-password\n")
    assert ok.exit_code == 0, ok.output
    short = runner.invoke(args=["create-user", "weak"], input="short\nshort\n")
    assert short.exit_code != 0 and "at least" in short.output
    dup = runner.invoke(args=["create-user", "newbie"],
                        input="a-long-enough-password\na-long-enough-password\n")
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_production_refuses_to_start_with_a_weak_secret():
    from app import create_app
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        create_app({"APP_ENV": "production", "SECRET_KEY": "dev-secret-key"})
