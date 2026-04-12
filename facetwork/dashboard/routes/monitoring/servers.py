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

"""Server status routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ....runtime.entities.server import ServerState
from ...dependencies import get_store

router = APIRouter(prefix="/servers")


@router.get("")
def server_list(request: Request, state: str | None = None, store=Depends(get_store)):
    """List all servers, optionally filtered by state."""
    if state:
        servers = store.get_servers_by_state(state)
    else:
        servers = store.get_all_servers()
    return request.app.state.templates.TemplateResponse(
        request,
        "servers/list.html",
        {"servers": servers, "filter_state": state},
    )


@router.get("/{server_id}")
def server_detail(server_id: str, request: Request, store=Depends(get_store)):
    """Show server detail."""
    server = store.get_server(server_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "servers/detail.html",
        {"server": server},
    )


@router.post("/{server_id}/quarantine", response_class=HTMLResponse)
def toggle_quarantine(server_id: str, store=Depends(get_store)):
    """Toggle a server between RUNNING and QUARANTINE.

    Quarantined servers keep heartbeating but skip task claims on each
    poll cycle — un-toggle to resume without restarting the runner.
    Returns the updated checkbox fragment for HTMX swap.
    """
    server = store.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.state == ServerState.QUARANTINE:
        server.state = ServerState.RUNNING
        quarantined = False
    elif server.state in (ServerState.RUNNING, ServerState.STARTUP):
        server.state = ServerState.QUARANTINE
        quarantined = True
    else:
        # Don't touch SHUTDOWN / ERROR servers — the flag is meaningless for them.
        raise HTTPException(
            status_code=409,
            detail=f"Cannot quarantine server in state '{server.state}'",
        )

    server.ping_time = int(time.time() * 1000)
    store.save_server(server)

    checked = "checked" if quarantined else ""
    return HTMLResponse(
        f'<input type="checkbox" {checked} '
        f'hx-post="/servers/{server_id}/quarantine" '
        f'hx-swap="outerHTML" '
        f'title="Quarantine this server (stop claiming tasks)">'
    )
