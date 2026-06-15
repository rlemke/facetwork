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

"""v3 Library side — Catalog (reusable workflows/libraries) + Flows (compiled FFL)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store

router = APIRouter(prefix="/v3")


@router.get("/catalog")
def catalog_v3(request: Request, q: str = "", tab: str = "all", store=Depends(get_store)):
    """Redesigned Catalog — search + workflows/libraries split."""
    from ..execution.catalog import _service

    svc = _service(store)
    rows = []
    if svc is not None:
        rows = svc.search(q) if q else svc.list_all()

    workflows = [r for r in rows if r.get("kind") == "workflow"]
    libraries = [r for r in rows if r.get("kind") == "library"]
    counts = {"all": len(rows), "workflows": len(workflows), "libraries": len(libraries)}
    if tab == "workflows":
        shown = workflows
    elif tab == "libraries":
        shown = libraries
    else:
        shown = rows

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/catalog/list.html",
        {
            "entries": shown,
            "counts": counts,
            "tab": tab,
            "q": q,
            "unavailable": svc is None,
            "active_nav": "catalog",
        },
    )


@router.get("/flows")
def flows_v3(request: Request, q: str = "", store=Depends(get_store)):
    """Redesigned Flows — compiled FFL programs (namespaces/facets/workflows)."""
    flows = list(store.get_all_flows())
    # Drop auto-generated CLI submissions (path=cli:submit); keep seeded/authored.
    flows = [f for f in flows if getattr(f.name, "path", "") != "cli:submit"]
    if q:
        flows = [f for f in flows if q.lower() in f.name.name.lower()]
    flows.sort(key=lambda f: f.name.name.lower())

    rows = [
        {
            "uuid": f.uuid,
            "name": f.name.name,
            "path": getattr(f.name, "path", "") or "",
            "workflows": len(f.workflows or []),
            "facets": len(f.facets or []),
            "namespaces": len(f.namespaces or []),
        }
        for f in flows
    ]

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/flows/list.html",
        {"flows": rows, "q": q, "total": len(rows), "active_nav": "library"},
    )
