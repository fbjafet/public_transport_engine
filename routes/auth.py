from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import get_primary_session
from app.security import fingerprint, is_safe_next, login_ip_limiter, login_pair_limiter
from models.user import User

auth_bp = Blueprint("auth", __name__)

# Verified against when the username is unknown, so response time does not reveal
# which usernames exist.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _next_target() -> str:
    target = request.values.get("next")
    return target if is_safe_next(target) else url_for("dashboard.index")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if g.user is not None:
            return redirect(_next_target())
        return render_template("login.html", next=request.args.get("next", ""))

    ip = request.remote_addr or "unknown"
    username = (request.form.get("username") or "").strip().lower()[:80]
    password = request.form.get("password") or ""

    if not login_ip_limiter.hit(ip):
        abort(429, description="Too many sign-in attempts. Try again in a few minutes.")
    pair = f"{ip}|{username}"
    if login_pair_limiter.blocked(pair):
        abort(429, description="Too many failed sign-ins for this account. Try again in a few minutes.")

    with get_primary_session() as db:
        user = db.scalar(select(User).where(User.username == username))
        stored_hash = user.password_hash if user else _DUMMY_HASH
        password_ok = check_password_hash(stored_hash, password)
        if user is not None and user.is_active and password_ok:
            login_pair_limiter.clear(pair)
            session.clear()  # new session on login prevents fixation
            session["uid"] = user.id
            session["pw"] = fingerprint(user.password_hash)
            session.permanent = True
            return redirect(_next_target())

    login_pair_limiter.record(pair)
    return (
        render_template("login.html", next=request.form.get("next", ""),
                        error="Invalid username or password."),
        401,
    )


@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
