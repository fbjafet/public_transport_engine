"""Authentication gate, CSRF protection, rate limiting and security headers.

Access model is default-deny: every endpoint requires a signed-in user unless it
is listed in PUBLIC_ENDPOINTS, and every state-changing request (anything other
than GET/HEAD/OPTIONS) requires a valid CSRF token and the "dispatcher" role,
except logout which any signed-in user may call.
"""
import functools
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from typing import NamedTuple
from urllib.parse import urlsplit

from flask import abort, g, jsonify, redirect, request, session, url_for

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_ENDPOINTS = {"auth.login", "healthz", "static"}
LOGOUT_ENDPOINT = "auth.logout"
STREAM_ENDPOINTS = {"dashboard.event_stream"}
WRITE_ROLE = "dispatcher"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)


class CurrentUser(NamedTuple):
    id: int
    username: str
    role: str
    pw_fingerprint: str


def fingerprint(password_hash: str) -> str:
    """Short digest stored in the session so a password change ends old sessions."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Rate limiting (in-process; see CHANGES.md for the multi-worker limitation)
# --------------------------------------------------------------------------- #
class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float):
        q = self._hits.get(key)
        if q is None:
            return None
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._hits[key]
            return None
        return q

    def _blocked(self, key: str, now: float) -> bool:
        q = self._prune(key, now)
        return q is not None and len(q) >= self.limit

    def _record(self, key: str, now: float) -> None:
        q = self._prune(key, now)
        if q is None:
            q = self._hits[key] = deque()
        q.append(now)
        if len(self._hits) > 10_000:
            for k in list(self._hits):
                self._prune(k, now)

    def blocked(self, key: str) -> bool:
        with self._lock:
            return self._blocked(key, self._clock())

    def record(self, key: str) -> None:
        with self._lock:
            self._record(key, self._clock())

    def hit(self, key: str) -> bool:
        """Record an attempt; return False (without recording) if over the limit."""
        with self._lock:
            now = self._clock()
            if self._blocked(key, now):
                return False
            self._record(key, now)
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._hits.clear()


login_ip_limiter = SlidingWindowLimiter(30, 900)
login_pair_limiter = SlidingWindowLimiter(5, 900)
llm_limiter = SlidingWindowLimiter(10, 60)


def reset_limiters() -> None:
    for limiter in (login_ip_limiter, login_pair_limiter, llm_limiter):
        limiter.clear_all()


def llm_action_limit(view):
    """Limit endpoints that trigger paid LLM calls to 10 per user per minute."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = g.get("user")
        key = f"user:{user.id}" if user else f"ip:{request.remote_addr}"
        if not llm_limiter.hit(key):
            abort(429, description="Too many requests. Please wait a minute and try again.")
        return view(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def is_safe_next(target: str | None) -> bool:
    """Only allow same-site relative paths as post-login redirects."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    if "\\" in target or any(ord(c) < 32 for c in target):
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def _check_csrf() -> None:
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("_csrf") or ""
    if not expected or not hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
        abort(400, description="Your session expired or the form was invalid. Reload the page and try again.")


def require_dispatcher() -> None:
    """For GET pages that only dispatchers should see (e.g. the logging form)."""
    user = g.get("user")
    if user is None or user.role != WRITE_ROLE:
        abort(403, description="Your account does not have permission to do that.")


def _fetch_user(uid) -> CurrentUser | None:
    """Look the user up on the primary so deactivation takes effect immediately."""
    from app.db import get_primary_session
    from models.user import User

    if not isinstance(uid, int):
        return None
    with get_primary_session() as db:
        user = db.get(User, uid)
        if user is None or not user.is_active:
            return None
        return CurrentUser(user.id, user.username, user.role, fingerprint(user.password_hash))


# --------------------------------------------------------------------------- #
# Request hooks
# --------------------------------------------------------------------------- #
def _load_user():
    g.user = None
    if request.endpoint in (None, "static"):
        return None
    uid = session.get("uid")
    if uid is None:
        return None
    user = _fetch_user(uid)
    if user is not None and hmac.compare_digest(str(session.get("pw", "")), user.pw_fingerprint):
        g.user = user
    else:
        session.pop("uid", None)
        session.pop("pw", None)
    return None


def _enforce_access():
    endpoint = request.endpoint
    if endpoint is None:  # unknown URL: let Flask return its 404
        return None

    is_public = endpoint in PUBLIC_ENDPOINTS
    user = g.get("user")

    if not is_public and user is None:
        if endpoint in STREAM_ENDPOINTS:
            return jsonify(error="authentication required"), 401
        if request.method == "GET":
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return redirect(url_for("auth.login"))

    if request.method not in SAFE_METHODS:
        _check_csrf()
        if not is_public and endpoint != LOGOUT_ENDPOINT and user.role != WRITE_ROLE:
            abort(403, description="Your account does not have permission to do that.")
    return None


def _set_security_headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.endpoint != "static":
        response.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def init_security(app) -> None:
    app.before_request(_load_user)
    app.before_request(_enforce_access)
    app.after_request(_set_security_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.context_processor(lambda: {"current_user": g.get("user")})
