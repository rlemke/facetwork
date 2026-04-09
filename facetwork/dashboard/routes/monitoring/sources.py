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

"""Published sources dashboard routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ...dependencies import get_store

router = APIRouter(prefix="/sources")


@router.get("")
def source_list(request: Request, q: str | None = None, store=Depends(get_store)):
    """List all published sources, optionally filtered by namespace name search."""
    sources = store.list_published_sources()
    if q:
        sources = [s for s in sources if q.lower() in s.namespace_name.lower()]
    return request.app.state.templates.TemplateResponse(
        request,
        "sources/list.html",
        {"sources": sources, "search_query": q},
    )


@router.get("/{namespace_name:path}")
def source_detail(namespace_name: str, request: Request, store=Depends(get_store)):
    """Show published source detail."""
    source = store.get_source_by_namespace(namespace_name)
    return request.app.state.templates.TemplateResponse(
        request,
        "sources/detail.html",
        {"source": source},
    )


@router.post("/{namespace_name:path}/delete")
def delete_source(namespace_name: str, store=Depends(get_store)):
    """Delete a published source and redirect to list."""
    store.delete_published_source(namespace_name)
    return RedirectResponse(url="/sources", status_code=303)
