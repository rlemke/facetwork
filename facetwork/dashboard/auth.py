# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Opt-in shared-token auth for the dashboard.

The dashboard historically had NO authentication — anyone who could reach the
port could delete runs/users and, worst, register arbitrary handler modules
(which runners import → effectively fleet-wide RCE). That is acceptable on a
trusted LAN but has no off-switch when the assumption breaks.

This middleware adds one: set ``FW_DASHBOARD_TOKEN`` and every *mutating* request
(POST/PUT/PATCH/DELETE — the destructive + handler-registration surface) must
present that token. Read-only monitoring (GET/HEAD/OPTIONS) stays open so the UI
and dashboards keep working unauthenticated. When the env var is unset the
middleware is a no-op, so existing deployments are unchanged.

Token sources (checked in order): ``Authorization: Bearer <t>``, ``X-FW-Token``
header, ``fw_token`` cookie, or ``?fw_token=<t>`` query param. A valid token
arriving via query param is persisted as an httponly cookie so a browser
operator can "log in" once by visiting any page with ``?fw_token=…`` and then
use the UI's mutating actions normally.
"""

from __future__ import annotations

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_COOKIE = "fw_token"

# ── Login sessions ───────────────────────────────────────────────────────────
# A logged-in browser carries a signed session cookie: "email|expires_ms|sig"
# where sig = HMAC-SHA256 over "email|expires_ms" with the server session key.
# The key is FW_DASHBOARD_SECRET, else FW_DASHBOARD_TOKEN, else an ephemeral
# per-process secret (sessions then reset on dashboard restart — fine for a
# LAN deployment; set one of the env vars for durable sessions).
SESSION_COOKIE = "fw_session"
SESSION_TTL_MS = 30 * 24 * 3600 * 1000  # 30 days

_EPHEMERAL_KEY = os.urandom(32)


def _session_key() -> bytes:
    key = os.environ.get("FW_DASHBOARD_SECRET") or os.environ.get("FW_DASHBOARD_TOKEN") or ""
    return key.encode("utf-8") if key else _EPHEMERAL_KEY


def _sign(payload: str) -> str:
    return hmac.new(_session_key(), payload.encode("utf-8"), "sha256").hexdigest()


def make_session(email: str, *, now_ms: int | None = None) -> str:
    """Signed session cookie value for ``email`` (rejects emails with '|')."""
    if "|" in email:
        raise ValueError("email may not contain '|'")
    import time

    expires = (now_ms if now_ms is not None else int(time.time() * 1000)) + SESSION_TTL_MS
    payload = f"{email}|{expires}"
    return f"{payload}|{_sign(payload)}"


def read_session(request: Request) -> str | None:
    """The session's email when the cookie is present, validly signed, and
    unexpired — else None."""
    import time

    raw = request.cookies.get(SESSION_COOKIE, "")
    parts = raw.rsplit("|", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        email, expires_s = payload.rsplit("|", 1)
        if int(expires_s) < int(time.time() * 1000):
            return None
    except ValueError:
        return None
    return email or None


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    """Require FW_DASHBOARD_TOKEN on mutating requests, when it is configured."""

    @staticmethod
    def _configured_token() -> str:
        # Read per-request so the guard can be enabled/disabled without rebuilding
        # the app (and so tests can toggle it).
        return os.environ.get("FW_DASHBOARD_TOKEN") or ""

    @staticmethod
    def _presented_token(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip()
        header = request.headers.get("x-fw-token")
        if header:
            return header.strip()
        cookie = request.cookies.get(_COOKIE)
        if cookie:
            return cookie
        query = request.query_params.get(_COOKIE)
        if query:
            return query
        return None

    async def dispatch(self, request: Request, call_next):
        token = self._configured_token()
        if not token:
            return await call_next(request)  # auth disabled — unchanged behavior

        # The login/logout endpoints must be reachable WITHOUT credentials —
        # they are how a browser obtains its session in the first place.
        if request.url.path in ("/login", "/logout"):
            return await call_next(request)

        presented = self._presented_token(request)
        valid = presented is not None and hmac.compare_digest(presented, token)
        # A logged-in browser session is as good as the machine token: humans
        # authenticate via /login; the bearer token remains for CLI/scripts.
        if not valid and read_session(request) is not None:
            valid = True

        if request.method.upper() in _MUTATING_METHODS and not valid:
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "detail": "a valid FW_DASHBOARD_TOKEN is required for mutating requests "
                    "(Authorization: Bearer <token>, X-FW-Token header, or ?fw_token=…).",
                },
                status_code=401,
            )

        response: Response = await call_next(request)

        # Convenience: persist a query-param token as a cookie so a browser
        # operator authenticates once, then the UI's form/htmx POSTs carry it.
        query = request.query_params.get(_COOKIE)
        if query and hmac.compare_digest(query, token) and request.cookies.get(_COOKIE) != token:
            response.set_cookie(_COOKIE, token, httponly=True, samesite="strict")
        return response
