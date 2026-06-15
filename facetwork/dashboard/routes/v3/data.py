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

"""v3 Data pages — Output file browser + PostGIS summary."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/v3")


@router.get("/output")
def output_v3(request: Request, path: str = ""):
    """Redesigned output file browser (reuses the v2 tree helpers)."""
    from ..monitoring.output import (
        _build_breadcrumbs,
        _build_tree,
        _output_base,
        _safe_path,
    )

    resolved = _safe_path(path)
    if resolved is None:
        return HTMLResponse("Path traversal not allowed", status_code=400)

    missing = not resolved.exists()
    entries = _build_tree(resolved) if not missing else []
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/output/browser.html",
        {
            "entries": entries,
            "breadcrumbs": _build_breadcrumbs(path),
            "base_dir": str(_output_base()),
            "current_path": path,
            "missing": missing,
            "active_nav": "output",
        },
    )


@router.get("/postgis")
def postgis_v3(request: Request):
    """Redesigned PostGIS summary (regions, element counts, db size)."""
    from ...viewdata import _get_postgis_summary

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/postgis/summary.html",
        {"data": _get_postgis_summary(), "active_nav": "postgis"},
    )
