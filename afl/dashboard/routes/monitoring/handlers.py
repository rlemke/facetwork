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

"""Handler registration routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ...dependencies import get_store

router = APIRouter(prefix="/handlers")


@router.get("")
def handler_list(q: str | None = None):
    """Redirect to v2 handlers list."""
    return RedirectResponse(url="/v2/handlers", status_code=307)


@router.get("/{facet_name:path}")
def handler_detail(facet_name: str):
    """Redirect to v2 handler detail."""
    return RedirectResponse(url=f"/v2/handlers/{facet_name}", status_code=307)


@router.post("/{facet_name:path}/delete")
def delete_handler(facet_name: str, store=Depends(get_store)):
    """Delete a handler registration and redirect to list."""
    store.delete_handler_registration(facet_name)
    return RedirectResponse(url="/v2/handlers", status_code=303)
