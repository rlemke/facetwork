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

"""Runner routes — list, detail, actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ...dependencies import get_store

router = APIRouter(prefix="/runners")


@router.get("")
def runner_list(state: str | None = None):
    """Redirect to the v3 Runs list."""
    url = "/v3/workflows"
    if state:
        # Map old state param to the v3 tab
        tab_map = {
            "running": "running",
            "paused": "running",
            "created": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "failed",
        }
        tab = tab_map.get(state, "running")
        url = f"/v3/workflows?tab={tab}"
    return RedirectResponse(url=url, status_code=307)


@router.get("/{runner_id}")
def runner_detail(runner_id: str):
    """Redirect to the v3 workflow detail."""
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=307)


def _build_step_log_counts(store, workflow_id: str) -> dict[str, int]:
    """Build a dict mapping step_id → log count for a workflow."""
    counts: dict[str, int] = {}
    try:
        step_logs = store.get_step_logs_by_workflow(workflow_id)
        for log in step_logs:
            counts[log.step_id] = counts.get(log.step_id, 0) + 1
    except Exception:
        pass
    return counts


# --- Action endpoints ---


@router.post("/{runner_id}/cancel")
def cancel_runner(runner_id: str, store=Depends(get_store)):
    """Cancel a runner."""
    store.update_runner_state(runner_id, "cancelled")
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)


@router.post("/{runner_id}/pause")
def pause_runner(runner_id: str, store=Depends(get_store)):
    """Pause a running runner."""
    store.update_runner_state(runner_id, "paused")
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)


@router.post("/{runner_id}/resume")
def resume_runner(runner_id: str, store=Depends(get_store)):
    """Resume a paused runner."""
    store.update_runner_state(runner_id, "running")
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)
