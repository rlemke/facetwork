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

"""v3 Step detail — state, params/returns, logs, recovery actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store
from ...helpers import categorize_step_state

router = APIRouter(prefix="/v3")

_CAT_COLOR = {
    "complete": "var(--st-complete)", "error": "var(--st-error)",
    "running": "var(--st-running)", "other": "var(--muted)",
}


@router.get("/steps/{step_id}")
def step_detail_v3(step_id: str, request: Request, store=Depends(get_store)):
    """Redesigned step detail — same data + action endpoints as /steps/{id}."""
    from ..execution.steps import (
        _get_heartbeat_ts,
        _is_mixin_sub_step,
        _resolve_step_names,
    )
    from ..monitoring.output import artifact_url

    step = store.get_step(step_id)
    names = _resolve_step_names(step, store) if step else {}
    step_logs = list(store.get_step_logs_by_step(step_id)) if step else []
    cat = categorize_step_state(step.state) if step else "other"

    # Detect viewable HTML artifacts among the step's returns (e.g. html_path)
    # and link them to the dashboard's artifact server.
    artifacts: dict[str, str] = {}
    if step and getattr(step, "attributes", None) and step.attributes.returns:
        for name, attr in step.attributes.returns.items():
            val = attr.get("value") if isinstance(attr, dict) else getattr(attr, "value", None)
            url = artifact_url(val)
            if url:
                artifacts[name] = url

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/steps/detail.html",
        {
            "step": step,
            "names": names,
            "step_logs": step_logs,
            "heartbeat_ts": _get_heartbeat_ts(step, store) if step else None,
            "is_mixin_sub_step": _is_mixin_sub_step(step) if step else False,
            "cat": cat,
            "pill": _CAT_COLOR.get(cat, "var(--muted)"),
            "artifacts": artifacts,
            "artifact_url": next(iter(artifacts.values()), None),
            "active_nav": "runs",
        },
    )
