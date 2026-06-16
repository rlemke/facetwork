"""Output file browser dashboard routes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(prefix="/output")

_DEFAULT_OUTPUT_DIR = "/Volumes/afl_data/output"

_MEDIA_TYPES = {
    ".html": "text/html", ".htm": "text/html",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".json": "application/json", ".geojson": "application/json",
    ".js": "text/javascript", ".css": "text/css",
    ".txt": "text/plain", ".csv": "text/csv", ".pbf": "application/x-protobuf",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


def _output_base() -> Path:
    """Return the configured output base directory."""
    from facetwork.config import get_config

    return Path(get_config().storage.local_output_dir or _DEFAULT_OUTPUT_DIR)


def _output_roots() -> list[Path]:
    """Candidate local output roots, in priority order.

    Handlers may write under different roots than the dashboard's configured one
    (AFL_OUTPUT_BASE / AFL_DATA_ROOT vs the config default), so the artifact
    server checks all of them. Only existing directories are returned.
    """
    roots: list[Path] = []

    def add(p) -> None:
        if not p:
            return
        try:
            rp = Path(p).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return
        if rp.is_dir() and rp not in roots:
            roots.append(rp)

    add(_output_base())
    add(os.environ.get("AFL_OUTPUT_BASE"))
    dr = os.environ.get("AFL_DATA_ROOT")
    if dr:
        add(Path(dr) / "output")
    add(Path.home() / "afl_data" / "output")
    add(_DEFAULT_OUTPUT_DIR)
    return roots


def _resolve_under_roots(subpath: str) -> Path | None:
    """Resolve ``subpath`` under the first output root that contains it,
    blocking traversal. Returns the existing file/dir, or None."""
    if not subpath:
        return None
    for base in _output_roots():
        target = (base / subpath).resolve()
        if (target == base or str(target).startswith(str(base) + os.sep)) and target.exists():
            return target
    return None


def artifact_url(value) -> str | None:
    """If ``value`` is a path to an existing HTML file under an output root,
    return the dashboard URL that serves it (with its sibling assets); else None.

    Used to turn an ``html_path`` step return into a clickable link. The serve
    route is path-based so the page's relative asset URLs (tiles, css) resolve.
    """
    if not isinstance(value, str) or not value.lower().endswith((".html", ".htm")):
        return None
    try:
        ap = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not ap.is_file():
        return None
    for base in _output_roots():
        try:
            rel = ap.relative_to(base)
        except ValueError:
            continue
        return "/output/raw/" + str(rel)
    return None


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


@router.get("/raw/{path:path}")
def output_raw(path: str):
    """Serve any output file by path, scoped to the output roots.

    The single artifact server behind every rendered HTML output: because it is
    path-based (not ``?path=``), an HTML page served at
    ``/output/raw/<dir>/index.html`` resolves its relative assets
    (``tiles/{z}/{x}/{y}.png``, css, …) correctly. Handles all runs' outputs;
    traversal is blocked and only files under a known output root are served.
    """
    resolved = _resolve_under_roots(path)
    if resolved is None or not resolved.is_file():
        return HTMLResponse("Not found", status_code=404)
    media_type = _MEDIA_TYPES.get(resolved.suffix.lower(), "application/octet-stream")
    return FileResponse(str(resolved), media_type=media_type)


@router.get("/view")
def output_view(path: str = ""):
    """Serve an output file (HTML, images, text) for inline viewing.

    Kept for the output browser's file preview; for HTML maps with relative
    assets, prefer ``/output/raw/<path>`` (path-based, so assets resolve)."""
    resolved = _safe_path(path)
    if resolved is None:
        return HTMLResponse("Path traversal not allowed", status_code=400)

    if not resolved.is_file():
        return HTMLResponse("File not found", status_code=404)

    media_type = _MEDIA_TYPES.get(resolved.suffix.lower(), "application/octet-stream")
    return FileResponse(str(resolved), media_type=media_type)
