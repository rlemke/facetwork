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

"""Admin (v3) — Users and Teams management + the "acting as" selector.

Native v3-shell pages; the POST handlers redirect back into /v3 so the user
never leaves the v3 UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ....runtime.entities import TeamDefinition, User
from ....runtime.types import generate_id
from ...dependencies import CURRENT_USER_COOKIE, get_current_user, get_store

router = APIRouter(prefix="/v3")


def _tmpl(request: Request, name: str, ctx: dict):
    return request.app.state.templates.TemplateResponse(request, name, ctx)


# ── Acting-as identity ────────────────────────────────────────────────────────


@router.post("/users/act-as")
def act_as(email: str = Form(...), next: str = Form("/v3/users")):
    """Set the current 'acting as' user cookie (no password)."""
    resp = RedirectResponse(url=next or "/v3/users", status_code=303)
    if email:
        resp.set_cookie(CURRENT_USER_COOKIE, email, max_age=60 * 60 * 24 * 365, samesite="lax")
    else:
        resp.delete_cookie(CURRENT_USER_COOKIE)
    return resp


# ── Users ──────────────────────────────────────────────────────────────────────


@router.get("/users")
def users_v3(
    request: Request,
    show_deleted: bool = False,
    store=Depends(get_store),
    current_user: User = Depends(get_current_user),
):
    users = list(store.list_users(include_deleted=show_deleted))
    return _tmpl(
        request,
        "v3/admin/users.html",
        {
            "users": users,
            "teams": list(store.list_teams()),
            "show_deleted": show_deleted,
            "current_user": current_user,
            "active_nav": "users",
        },
    )


@router.get("/users/new")
def user_new_v3(request: Request, store=Depends(get_store)):
    return _tmpl(
        request,
        "v3/admin/user_form.html",
        {"user": None, "teams": list(store.list_teams()), "active_nav": "users"},
    )


@router.get("/users/{email}/edit")
def user_edit_v3(email: str, request: Request, store=Depends(get_store)):
    user = store.get_user(email)
    return _tmpl(
        request,
        "v3/admin/user_form.html",
        {"user": user, "teams": list(store.list_teams()), "active_nav": "users"},
    )


@router.post("/users")
def user_save_v3(
    request: Request,
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    phone: str = Form(""),
    title: str = Form(""),
    teams: list[str] = Form(default=[]),
    default_team: str = Form(""),
    store=Depends(get_store),
):
    """Create or update a user (upsert by email). Preserves kind/status."""
    existing = store.get_user(email)
    user = User(
        email=email.strip(),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone.strip(),
        title=title.strip(),
        teams=[t for t in teams if t],
        default_team=default_team.strip(),
        kind=existing.kind if existing else "human",
        status=existing.status if existing else "active",
        avatar=existing.avatar if existing else "",
        created_at=existing.created_at if existing else 0,
    )
    store.save_user(user)
    return RedirectResponse(url="/v3/users", status_code=303)


@router.post("/users/{email}/delete")
def user_delete_v3(email: str, store=Depends(get_store)):
    """Soft delete — marks the user deleted but keeps their runs' authorship."""
    store.soft_delete_user(email)
    return RedirectResponse(url="/v3/users", status_code=303)


@router.post("/users/{email}/force-delete")
def user_force_delete_v3(email: str, delete_work: str = Form(""), store=Depends(get_store)):
    """Permanently delete — reassigns references to the 'deleted' user;
    optionally removes the user's runs/flows."""
    store.force_delete_user(email, delete_work=bool(delete_work))
    return RedirectResponse(url="/v3/users", status_code=303)


# ── Teams ────────────────────────────────────────────────────────────────────


@router.get("/teams")
def teams_v3(request: Request, store=Depends(get_store)):
    teams = list(store.list_teams())
    counts = {t.name: len(list(store.get_team_members(t.name))) for t in teams}
    return _tmpl(
        request,
        "v3/admin/teams.html",
        {"teams": teams, "member_counts": counts, "active_nav": "teams"},
    )


@router.get("/teams/new")
def team_new_v3(request: Request, store=Depends(get_store)):
    return _tmpl(
        request,
        "v3/admin/team_form.html",
        {"team": None, "members": set(), "users": list(store.list_users()), "active_nav": "teams"},
    )


@router.get("/teams/{uuid}/edit")
def team_edit_v3(uuid: str, request: Request, store=Depends(get_store)):
    team = store.get_team(uuid)
    members = {u.email for u in store.get_team_members(team.name)} if team else set()
    return _tmpl(
        request,
        "v3/admin/team_form.html",
        {
            "team": team,
            "members": members,
            "users": list(store.list_users()),
            "active_nav": "teams",
        },
    )


@router.post("/teams")
def team_save_v3(
    request: Request,
    uuid: str = Form(""),
    name: str = Form(...),
    description: str = Form(""),
    leader_email: str = Form(""),
    members: list[str] = Form(default=[]),
    store=Depends(get_store),
    current_user: User = Depends(get_current_user),
):
    """Create or update a team and sync its membership onto the users."""
    existing = store.get_team(uuid) if uuid else None
    team = TeamDefinition(
        uuid=existing.uuid if existing else generate_id(),
        name=name.strip(),
        description=description.strip(),
        leader_email=leader_email.strip(),
        created_by=existing.created_by if existing else current_user.email,
        created_at=existing.created_at if existing else 0,
    )
    store.save_team(team)
    store.set_team_members(team.name, [m for m in members if m])
    return RedirectResponse(url="/v3/teams", status_code=303)


@router.post("/teams/{uuid}/delete")
def team_delete_v3(uuid: str, store=Depends(get_store)):
    """Delete a team and scrub its name from all users' membership."""
    store.delete_team(uuid)
    return RedirectResponse(url="/v3/teams", status_code=303)
