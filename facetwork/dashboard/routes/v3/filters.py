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

"""Global filter page — persists Flow / Workflow filter selections in a cookie."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ...dependencies import get_store
from ...viewfilters import FILTERS_COOKIE, build_options, read_filters

router = APIRouter(prefix="/v3")

_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # one year


@router.get("/filters")
def filters_page(request: Request, store=Depends(get_store)):
    """Render the two-section global filter form, pre-filled from the cookie."""
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/filters.html",
        {
            "f": read_filters(request),
            "opts": build_options(store),
            "active_nav": "filters",
        },
    )


@router.post("/filters")
def filters_save(
    request: Request,
    # flows section
    flow_teams: list[str] = Form(default=[]),
    flow_author: str = Form(""),
    flow_tag: str = Form(""),
    flow_created_from: str = Form(""),
    flow_created_to: str = Form(""),
    # workflows section
    wf_teams: list[str] = Form(default=[]),
    wf_workflows: list[str] = Form(default=[]),
    wf_user: str = Form(""),
    wf_state: str = Form("any"),
    wf_purpose: str = Form("any"),
    wf_run_from: str = Form(""),
    wf_run_to: str = Form(""),
    next: str = Form("/v3/filters"),
):
    """Persist the selections into the filter cookie and return to ``next``."""
    payload = {
        "flows": {
            "teams": [t for t in flow_teams if t],
            "author": flow_author.strip(),
            "tag": flow_tag.strip(),
            "created_from": flow_created_from.strip(),
            "created_to": flow_created_to.strip(),
        },
        "workflows": {
            "teams": [t for t in wf_teams if t],
            "workflows": [w for w in wf_workflows if w],
            "user": wf_user.strip(),
            "state": wf_state.strip() or "any",
            "purpose": wf_purpose.strip() or "any",
            "run_from": wf_run_from.strip(),
            "run_to": wf_run_to.strip(),
        },
    }
    resp = RedirectResponse(url=next or "/v3/filters", status_code=303)
    resp.set_cookie(
        FILTERS_COOKIE,
        json.dumps(payload, separators=(",", ":")),
        max_age=_COOKIE_MAX_AGE,
        samesite="lax",
    )
    return resp


@router.post("/filters/clear")
def filters_clear(next: str = Form("/v3/filters")):
    """Clear all global filters."""
    resp = RedirectResponse(url=next or "/v3/filters", status_code=303)
    resp.delete_cookie(FILTERS_COOKIE)
    return resp
