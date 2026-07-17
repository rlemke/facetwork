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

"""Login / logout — session authentication for dashboard humans.

Passwords live on the user (scrypt hashes, ``User.password_hash``); a
successful login sets the signed ``fw_session`` cookie, which the auth
middleware accepts in lieu of the machine bearer token for mutating
requests. A user with no password yet sets one on first login (first-claim
on a LAN — acceptable for this deployment model, and it activates
session-only identity for everyone the moment the first password exists).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ...auth import SESSION_COOKIE, SESSION_TTL_MS, make_session
from ...dependencies import CURRENT_USER_COOKIE, get_store

router = APIRouter()

_MIN_PASSWORD_LEN = 8


def _render(request: Request, **ctx):
    ctx.setdefault("error", "")
    ctx.setdefault("email", "")
    ctx.setdefault("setup_mode", False)
    return request.app.state.templates.TemplateResponse(request, "v3/login.html", ctx)


@router.get("/login")
def login_form(request: Request):
    return _render(request)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    store=Depends(get_store),
):
    email = email.strip().lower()
    user = store.get_user(email)
    if user is None or user.is_special or user.status != "active":
        return _render(request, error="Unknown user.", email=email)

    if not user.has_password:
        # First login: set the password now (two fields must match).
        if not new_password:
            return _render(request, setup_mode=True, email=email)
        if len(new_password) < _MIN_PASSWORD_LEN:
            return _render(
                request, setup_mode=True, email=email,
                error=f"Password must be at least {_MIN_PASSWORD_LEN} characters.",
            )
        if new_password != confirm_password:
            return _render(
                request, setup_mode=True, email=email, error="Passwords do not match.",
            )
        user.set_password(new_password)
        store.save_user(user)
    elif not user.verify_password(password):
        return _render(request, error="Wrong password.", email=email)

    resp = RedirectResponse(url="/v3/workflows", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, make_session(user.email),
        max_age=SESSION_TTL_MS // 1000, httponly=True, samesite="lax",
    )
    # Keep the legacy attribution cookie aligned with the logged-in identity.
    resp.set_cookie(CURRENT_USER_COOKIE, user.email, max_age=SESSION_TTL_MS // 1000)
    return resp


@router.post("/logout")
def logout(request: Request):
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    resp.delete_cookie(CURRENT_USER_COOKIE)
    return resp
