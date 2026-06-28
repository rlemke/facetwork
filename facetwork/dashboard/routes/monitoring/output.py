"""Output file browser dashboard routes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, Response

router = APIRouter(prefix="/output")

_DEFAULT_OUTPUT_DIR = "/Volumes/afl_data/output"

_MEDIA_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".geojson": "application/json",
    ".js": "text/javascript",
    ".css": "text/css",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".pbf": "application/x-protobuf",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
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
    # Where the host's output dir is bind-mounted into a containerized dashboard
    # (so it can serve outputs a host-run runner wrote). See docker-compose.
    add(os.environ.get("AFL_HOST_OUTPUT_DIR"))
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


def _s3_output_bases() -> list[str]:
    """``s3://…`` output prefixes to check when a path isn't on local disk.

    Configure explicitly with ``AFL_S3_OUTPUT_BASE``; otherwise derived from
    ``AFL_OUTPUT_BASE`` / ``AFL_DATA_ROOT`` (when they are s3 URIs) and
    ``AFL_S3_BUCKET``.
    """
    bases: list[str] = []

    def add(u) -> None:
        if not isinstance(u, str):
            return
        for part in u.split(","):  # allow a comma-separated list of prefixes
            p = part.strip().rstrip("/")
            if p.startswith("s3://") and p not in bases:
                bases.append(p)

    add(os.environ.get("AFL_S3_OUTPUT_BASE"))
    add(os.environ.get("AFL_OUTPUT_BASE"))
    dr = os.environ.get("AFL_DATA_ROOT")
    if dr and dr.startswith("s3://"):
        add(dr.rstrip("/") + "/output")
    bucket = os.environ.get("AFL_S3_BUCKET")
    if bucket:
        add(f"s3://{bucket}/output")
    # Bucket root of every base collected above, so the raw route can reconstruct
    # output keys that are NOT under .../output/ — e.g. the OSM pipeline writes
    # maps under s3://<bucket>/osm-output/osm-transform/…. artifact_url() links
    # those by their bucket-relative key, which only resolves against the root.
    # Derived from the bases (not AFL_S3_BUCKET) so it works on the dashboard,
    # which sets AFL_S3_OUTPUT_BASE but not necessarily AFL_S3_BUCKET.
    for b in list(bases):
        add("s3://" + b[len("s3://") :].split("/", 1)[0])
    return bases


def _resolve_s3(subpath: str) -> str | None:
    """Return the s3 URI for ``subpath`` under the first output base that has
    it, or None. Best-effort: needs boto3 + s3 config, else returns None."""
    if not subpath:
        return None
    bases = _s3_output_bases()
    if not bases:
        return None
    try:
        from facetwork.runtime.storage import get_storage_backend
    except Exception:
        return None
    for base in bases:
        uri = base + "/" + subpath.lstrip("/")
        try:
            if get_storage_backend(uri).isfile(uri):
                return uri
        except Exception:
            continue
    return None


def _read_s3(uri: str) -> bytes | None:
    try:
        from facetwork.runtime.storage import get_storage_backend

        with get_storage_backend(uri).open(uri, "rb") as f:
            return f.read()
    except Exception:
        return None


def artifact_url(value) -> str | None:
    """If ``value`` is a path to an existing HTML file under an output root,
    return the dashboard URL that serves it (with its sibling assets); else None.

    Used to turn an ``html_path`` step return into a clickable link. The serve
    route is path-based so the page's relative asset URLs (tiles, css) resolve.
    Handles the host↔container split: a runner stores an absolute *host* path,
    but a containerized dashboard sees the same tree under a different mount, so
    we also match by the path tail after the last ``/output/`` segment.
    """
    if not isinstance(value, str) or not value.lower().endswith((".html", ".htm")):
        return None

    # 0a) Already a public web URL (e.g. a GitHub Pages page published by
    #     census.Publish.PublishWebBundle) — it's directly clickable, link as-is.
    low = value.lower()
    if low.startswith(("http://", "https://")):
        return value

    # 0) Object-store URI (s3://bucket/key): the fleet writes durable HTML
    #    outputs straight to the store, e.g.
    #    s3://afl-cache/osm-output/osm-transform/<map>.html — which has no
    #    "/output/" segment, so the marker logic below never matches. Link it via
    #    the raw route by its bucket-relative key; the route's S3 fallback
    #    (_resolve_s3, now incl. the bucket root) reconstructs + serves it. Verify
    #    it exists before linking so a dead path shows no button.
    if value.startswith("s3://"):
        # Prefer the tail after the last "/output/" segment, resolved against a
        # configured s3 base — so s3://bucket/output/<tail> links as
        # /output/raw/<tail>, not the doubled /output/raw/output/<tail>. Fall
        # back to the bucket-relative key for object-store URIs that have NO
        # "/output/" segment (e.g. the OSM pipeline's
        # s3://afl-cache/osm-output/osm-transform/<map>.html).
        norm = value.replace("\\", "/")
        marker = "/output/"
        idx = norm.rfind(marker)
        if idx != -1:
            rel = norm[idx + len(marker) :]
            if rel and _resolve_s3(rel) is not None:
                return "/output/raw/" + rel
        rest = value[len("s3://") :]
        key = rest.split("/", 1)[1] if "/" in rest else ""
        return "/output/raw/" + key if (key and _resolve_s3(key)) else None

    # 1) Exact: the path is under a current root and exists (local dev).
    try:
        ap = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        ap = None
    if ap and ap.is_file():
        for base in _output_roots():
            try:
                return "/output/raw/" + str(ap.relative_to(base))
            except ValueError:
                continue

    # 2) Cross-mount: derive the tail after the last "/output/" and locate it
    #    under a current root (e.g. a host path served via a bind mount).
    norm = value.replace("\\", "/")
    marker = "/output/"
    idx = norm.rfind(marker)
    if idx != -1:
        rel = norm[idx + len(marker) :]
        if rel and (_resolve_under_roots(rel) is not None or _resolve_s3(rel) is not None):
            return "/output/raw/" + rel
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
    if resolved is not None and resolved.is_file():
        media_type = _MEDIA_TYPES.get(resolved.suffix.lower(), "application/octet-stream")
        return FileResponse(str(resolved), media_type=media_type)

    # Fall back to the S3/MinIO output store (fleet runs write there).
    s3_uri = _resolve_s3(path)
    if s3_uri:
        data = _read_s3(s3_uri)
        if data is not None:
            suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
            media_type = _MEDIA_TYPES.get(suffix, "application/octet-stream")
            return Response(content=data, media_type=media_type)

    return HTMLResponse("Not found", status_code=404)


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
