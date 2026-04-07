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

from fastapi import APIRouter, Depends, Request

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
