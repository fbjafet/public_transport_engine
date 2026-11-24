"""Shared test helpers (no side effects on import)."""
import re

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    match = CSRF_RE.search(html)
    assert match, "no csrf token found on " + path
    return match.group(1)
