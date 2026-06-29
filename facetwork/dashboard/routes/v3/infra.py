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

"""v3 Infra page — links out to the MinIO console and the Mongo (mongo-express) UI.

These are external admin UIs that run as their own containers (MinIO console on
:9001, mongo-express on :8081). The dashboard doesn't proxy them — it just
surfaces their URLs so they're one click away from the platform nav. The URLs
are env-overridable (``FW_MINIO_CONSOLE_URL`` / ``FW_MONGO_UI_URL``); when unset
they're derived from the *request host* so the page works on any fleet host
without hardcoding a single box's IP.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

router = APIRouter(prefix="/v3")

# Default host ports for the two admin UIs (see docker-compose.full-stack.yml).
_MINIO_CONSOLE_PORT = 9001
_MONGO_UI_PORT = 8081


def _host_url(request: Request, port: int) -> str:
    """Build ``http://<request-host>:<port>`` from the incoming request.

    Uses only the hostname of the request the browser already reached the
    dashboard on, so the link resolves to the same box the user is on (any
    fleet host), never a hardcoded IP.
    """
    host = (request.url.hostname or "localhost").strip()
    return f"http://{host}:{port}"


@router.get("/infra")
def infra_page(request: Request):
    """Render the Infra page — cards linking out to MinIO + Mongo admin UIs."""
    minio_url = os.environ.get("FW_MINIO_CONSOLE_URL", "").strip() or _host_url(
        request, _MINIO_CONSOLE_PORT
    )
    mongo_url = os.environ.get("FW_MONGO_UI_URL", "").strip() or _host_url(
        request, _MONGO_UI_PORT
    )

    services = [
        {
            "key": "minio",
            "name": "MinIO Console",
            "desc": "Object store backing the durable cache + workflow outputs "
            "(s3://afl-cache). Browse buckets, inspect objects, manage policies.",
            "url": minio_url,
            "creds": "minioadmin / minioadmin",
        },
        {
            "key": "mongo",
            "name": "Mongo Express",
            "desc": "Web admin for the fleet MongoDB (database facetwork) — the "
            "runners, tasks, steps, flows, and catalog collections.",
            "url": mongo_url,
            "creds": "no auth (internal LAN tool)",
        },
    ]

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/infra/index.html",
        {
            "services": services,
            "active_nav": "infra",
        },
    )
