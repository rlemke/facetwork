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

"""Log viewer routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store

router = APIRouter(prefix="/logs")


@router.get("/{runner_id}")
def log_list(runner_id: str, request: Request, store=Depends(get_store)):
    """Show logs for a runner."""
    runner = store.get_runner(runner_id)
    logs = store.get_logs_by_runner(runner_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "logs/list.html",
        {"logs": logs, "runner": runner},
    )
