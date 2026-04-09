"""Output file browser dashboard routes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(prefix="/output")

_DEFAULT_OUTPUT_DIR = "/Volumes/afl_data/output"


def _output_base() -> Path:
    """Return the configured output base directory."""
    from facetwork.config import get_config

    return Path(get_config().storage.local_output_dir or _DEFAULT_OUTPUT_DIR)


def _safe_path(subpath: str) -> Path | None:
    """Resolve *subpath* under the output base, blocking traversal.

    Returns ``None`` if the resolved path escapes the base directory.
    """
    base = _output_base().resolve()
    if not subpath:
        return base
    target = (base / subpath).resolve()
    if not (target == base or str(target).startswith(str(base) + os.sep)):
        return None
    return target


def _build_breadcrumbs(subpath: str) -> list[dict]:
    """Build breadcrumb entries from a subpath string.

    Returns a list of ``{"name": ..., "path": ...}`` dicts.
    The first entry is always the root ("Output").
    """
    crumbs: list[dict] = [{"name": "Output", "path": ""}]
    if not subpath:
        return crumbs
    parts = Path(subpath).parts
    for i, part in enumerate(parts):
        crumbs.append(
            {
                "name": part,
                "path": str(Path(*parts[: i + 1])),
            }
        )
    return crumbs


def _build_tree(dir_path: Path) -> list[dict]:
    """List directory contents with metadata.

    Returns a sorted list (directories first, then files, alphabetical)
    of dicts with keys: ``name``, ``path``, ``is_dir``, ``size``, ``mtime``.
    """
    if not dir_path.is_dir():
        return []
    entries: list[dict] = []
    base = _output_base().resolve()
    try:
        children = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []
    for child in children:
        try:
            stat = child.stat()
        except OSError:
            continue
        rel = child.resolve().relative_to(base)
        entries.append(
            {
                "name": child.name,
                "path": str(rel),
                "is_dir": child.is_dir(),
                "size": stat.st_size if child.is_file() else 0,
                "mtime": stat.st_mtime,
            }
        )
    return entries


@router.get("")
def output_browser(request: Request, path: str = ""):
    """Browse the output directory tree."""
    resolved = _safe_path(path)
    if resolved is None:
        return HTMLResponse("Path traversal not allowed", status_code=400)

    if not resolved.exists():
        return HTMLResponse("Directory not found", status_code=404)

    entries = _build_tree(resolved)
    breadcrumbs = _build_breadcrumbs(path)
    base_dir = str(_output_base())

    return request.app.state.templates.TemplateResponse(
        request,
        "output/browser.html",
        {
            "entries": entries,
            "breadcrumbs": breadcrumbs,
            "base_dir": base_dir,
            "current_path": path,
            "active_tab": "output",
        },
    )


@router.get("/view")
def output_view(path: str = ""):
    """Serve an output file (HTML, images, text) for inline viewing."""
    resolved = _safe_path(path)
    if resolved is None:
        return HTMLResponse("Path traversal not allowed", status_code=400)

    if not resolved.is_file():
        return HTMLResponse("File not found", status_code=404)

    suffix = resolved.suffix.lower()
    media_types = {
        ".html": "text/html",
        ".htm": "text/html",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".json": "application/json",
        ".geojson": "application/json",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    return FileResponse(str(resolved), media_type=media_type)
