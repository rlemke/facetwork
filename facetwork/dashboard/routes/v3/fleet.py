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

"""v3 Fleet page — per-host rollout/health view.

Joins the central fleet config + per-host agent reconcile records (the
``fleet``/``fleet-agent`` controller) with live runner heartbeats, so the
common fleet faults are visible at a glance instead of hand-queried:
image drift, an agent that reconciled but whose runners aren't registering,
and runners with no agent (won't auto-update on a rollout).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store

router = APIRouter(prefix="/v3")

_ALIVE_MS = 120_000  # heartbeat freshness window


def _norm(host) -> str:
    """fleet-agent records use 'server2.local'; runner heartbeats use 'server2'."""
    return str(host or "?").removesuffix(".local")


def _fleet_data(store) -> dict:
    # Reuse the v2 controller reader so config/agent parsing never diverges.
    from ...viewdata import _fleet_controller_data

    ctrl = _fleet_controller_data(store) or {}
    cfg = ctrl.get("config") or {}
    desired = cfg.get("version")

    now = int(time.time() * 1000)
    live: dict[str, int] = {}
    freshest: dict[str, float] = {}
    try:
        for s in store._db.servers.find({}):
            ping = s.get("ping_time", 0) or 0
            if now - ping >= _ALIVE_MS:
                continue
            h = _norm(s.get("server_name"))
            live[h] = live.get(h, 0) + 1
            age = (now - ping) / 1000
            if h not in freshest or age < freshest[h]:
                freshest[h] = age
    except Exception:
        pass

    hosts: dict[str, dict] = {}
    for a in ctrl.get("agents", []):
        h = _norm(a.get("host"))
        hosts[h] = {
            "host": h,
            "version": a.get("applied_version"),
            "image": a.get("applied_image") or "—",
            "applied_at": a.get("applied_at") or "—",
            "uptodate": bool(a.get("uptodate")),
            "has_agent": True,
        }
    for h, n in live.items():
        d = hosts.setdefault(
            h,
            {
                "host": h,
                "version": None,
                "image": "—",
                "applied_at": "—",
                "uptodate": False,
                "has_agent": False,
            },
        )
        d["live"] = n
        d["last_ping_s"] = round(freshest.get(h, 0))

    rows = []
    for d in hosts.values():
        d.setdefault("live", 0)
        d.setdefault("last_ping_s", None)
        warns = []
        if d["has_agent"] and d["version"] is not None and not d["uptodate"]:
            warns.append("image drift — not on the config version")
        if d["has_agent"] and d["live"] == 0:
            warns.append("agent reconciled but no live runners registering")
        if not d["has_agent"] and d["live"] > 0:
            warns.append("running with no fleet-agent — won't auto-update on a rollout")
        d["warns"] = warns
        d["ok"] = d["live"] > 0 and not warns
        rows.append(d)
    rows.sort(key=lambda x: x["host"])

    return {
        "config": cfg,
        "services": cfg.get("services", []),
        "roles": cfg.get("roles", []),
        "hosts": rows,
        "desired_version": desired,
        "summary": {
            "hosts": len(rows),
            "live": sum(d["live"] for d in rows),
            "uptodate": sum(1 for d in rows if d["uptodate"]),
            "warnings": sum(1 for d in rows if d["warns"]),
        },
        "has_controller": bool(cfg),
    }


@router.get("/fleet")
def fleet_v3(request: Request, store=Depends(get_store)):
    """Redesigned Fleet page — per-host rollout + health."""
    ctx = _fleet_data(store)
    ctx["active_nav"] = "fleet"
    return request.app.state.templates.TemplateResponse(request, "v3/fleet/overview.html", ctx)
