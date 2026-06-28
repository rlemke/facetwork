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

"""Claude workflow catalog browser routes.

Read views over the catalog (`facetwork.catalog`) plus publish (review-gate)
and run actions. The catalog requires a MongoStore-backed store (`_db`); with
any other store the pages render an "unavailable" notice rather than erroring.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ...dependencies import get_store

router = APIRouter(prefix="/catalog")


def _service(store):
    """Build a CatalogService over the store, or None when the store has no
    Mongo `_db` (e.g. a bare MemoryStore)."""
    db = getattr(store, "_db", None)
    if db is None:
        return None
    from facetwork.catalog import CatalogService, MongoCatalogStore

    return CatalogService(MongoCatalogStore(db), store)


def render_summary_md(text: str | None) -> str:
    """Render a workflow's authoring summary (a small markdown subset) to safe
    HTML. HTML in the source is escaped first (XSS-safe), then only our own tags
    are emitted: paragraphs, line breaks, `**bold**`, `*italic*`, `` `code` ``,
    `# headings`, ordered/unordered lists, and `[text](http(s)://…)` links. No
    third-party dependency. Unrecognized syntax stays as escaped text."""
    import html
    import re

    if not text or not text.strip():
        return ""

    text = html.escape(text, quote=False)  # neutralize raw HTML; keep quotes readable

    # Stash inline-code spans so their contents aren't further formatted.
    codes: list[str] = []

    def _stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)

    def inline(s: str) -> str:
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)\*", r"<em>\1</em>", s)
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            r'<a href="\2" target="_blank" rel="noopener">\1</a>',
            s,
        )
        return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)

    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            flush()
            i += 1
        elif h := re.match(r"^(#{1,6})\s+(.*)", s):
            flush()
            out.append(
                f"<h{min(len(h.group(1)) + 3, 6)}>{inline(h.group(2))}</h{min(len(h.group(1)) + 3, 6)}>"
            )
            i += 1
        elif re.match(r"^\d+\.\s+", s):
            flush()
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(inline(re.sub(r"^\s*\d+\.\s+", "", lines[i].strip())))
                i += 1
            out.append("<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>")
        elif re.match(r"^[-*]\s+", s):
            flush()
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(inline(re.sub(r"^\s*[-*]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
        else:
            para.append(s)
            i += 1
    flush()
    return "\n".join(out)


def _parse_version(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@router.post("/{slug}/publish")
def catalog_publish(request: Request, slug: str, version: str = Form(""), store=Depends(get_store)):
    """Publish (review-approve) a revision so it can run unattended."""
    svc = _service(store)
    if svc is not None:
        try:
            svc.publish(slug, _parse_version(version))
        except Exception as e:
            return RedirectResponse(f"/v3/catalog/{slug}?error={str(e)[:200]}", status_code=303)
    return RedirectResponse(f"/v3/catalog/{slug}", status_code=303)


@router.post("/{slug}/run")
def catalog_run(
    request: Request,
    slug: str,
    version: str = Form(""),
    inputs_json: str = Form("{}"),
    allow_unpublished: str = Form(""),
    store=Depends(get_store),
):
    """Pin a revision and submit a bootstrap run with the given inputs."""
    svc = _service(store)
    if svc is None:
        return RedirectResponse(f"/v3/catalog/{slug}?error=catalog+unavailable", status_code=303)
    try:
        inputs = json.loads(inputs_json) if inputs_json.strip() else {}
    except json.JSONDecodeError as e:
        return RedirectResponse(f"/v3/catalog/{slug}?error=bad+inputs+JSON:+{e}", status_code=303)
    try:
        res = svc.run(
            slug,
            version=_parse_version(version),
            inputs=inputs,
            allow_unpublished=bool(allow_unpublished),
        )
    except Exception as e:
        return RedirectResponse(f"/v3/catalog/{slug}?error={str(e)[:200]}", status_code=303)
    return RedirectResponse(f"/runners/{res['runner_id']}", status_code=303)
