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

"""Handlers for the built-in ``fw.archive`` facets (``facetwork/ffl/archive.ffl``).

Stdlib only (``zipfile`` / ``tarfile`` / ``gzip``), like the rest of the
built-ins.

Two things here are not what the hand-rolled versions do.

**Members that escape the destination are refused.** Measured on Python 3.12
(what runs here), ``TarFile.extractall`` writes a member named ``../x`` OUTSIDE
the destination and creates a symlink member pointing anywhere it likes — the
filtering that stops this only becomes the default in 3.14. ``ZipFile`` does
sanitise, but silently: ``../x`` is flattened to ``x`` inside *dest*, so an
archive whose layout is not what it claimed produces quietly different paths.

The six ``extractall`` sites across the domain packages are all zip, so none is
exposed to the escape today; this guard is what keeps that true when one is
later pointed at a tar. Each member's resolved destination is checked to be
inside *dest*, and tar links are skipped outright — these pipelines consume
data archives, and none legitimately contains a link.

**The format comes from the content.** Publishers mislabel: a ``.gz`` that is
really a zip, a ``.zip`` served as ``application/octet-stream``. Sniffing the
magic bytes costs one read and removes a class of "works for that source, fails
for this one" bugs.
"""

from __future__ import annotations

import fnmatch
import gzip
import io
import logging
import os
import posixpath
import tarfile
import zipfile
from typing import Any

from facetwork.runtime.storage import LocalStorageBackend, get_storage_backend

logger = logging.getLogger(__name__)

NAMESPACE = "fw.archive"

ZIP = "zip"
TAR = "tar"
GZIP = "gzip"

_CHUNK = 1024 * 1024


def _log(step_log: Any, message: str, level: str = "info") -> None:
    if callable(step_log):
        step_log(message, level)


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


def _read_bytes(path: str) -> bytes:
    fs = get_storage_backend(path)
    if not fs.exists(path):
        raise FileNotFoundError(f"archive not found: {path}")
    with fs.open(path, "rb") as fh:
        return fh.read()


def _local_path(path: str) -> str | None:
    """The path if it is on a local filesystem, else None.

    zipfile and tarfile both want a seekable file. A local archive is opened in
    place; a remote one is pulled into memory, which is the same whole-object
    model the S3 backend already uses.
    """
    fs = get_storage_backend(path)
    return path if isinstance(fs, LocalStorageBackend) else None


def detect_format(data: bytes) -> str:
    """Archive format from its magic bytes.

    Content, not extension — see the module docstring. gzip is checked after
    tar-detection is attempted on the decompressed stream, because a .tar.gz is
    both, and callers care that it is a tar.
    """
    if data[:4] == b"PK\x03\x04" or data[:4] == b"PK\x05\x06":
        return ZIP
    if data[:2] == b"\x1f\x8b":  # gzip container — tar inside, or a lone file
        try:
            head = gzip.decompress(data[: 512 * 1024])
        except (OSError, EOFError):
            head = b""
        if _looks_like_tar(head):
            return TAR
        return GZIP
    if data[:6] in (b"\xfd7zXZ\x00",) or data[:3] == b"BZh" or _looks_like_tar(data):
        return TAR
    raise ValueError("unrecognised archive format (not zip, tar, tar.gz/bz2/xz, or gzip)")


def _looks_like_tar(data: bytes) -> bool:
    # The ustar magic sits at offset 257 of the first header block.
    return len(data) >= 265 and data[257:262] in (b"ustar",)


def _open_archive(path: str):
    """Return ``(kind, handle, raw)`` for *path*.

    ``handle`` is a ZipFile or TarFile; for a plain gzip it is None and the
    caller works from *raw* (the decompressed bytes), since a lone gzip has one
    unnamed member and neither library models it.
    """
    local = _local_path(path)
    data = _read_bytes(path) if local is None else None
    probe = data[:1024] if data is not None else _read_head(path)
    kind = detect_format(probe if len(probe) >= 265 else (data or probe))

    if kind == ZIP:
        source: Any = local if local is not None else io.BytesIO(data)
        return ZIP, zipfile.ZipFile(source), None
    if kind == TAR:
        if local is not None:
            return TAR, tarfile.open(local, mode="r:*"), None
        return TAR, tarfile.open(fileobj=io.BytesIO(data), mode="r:*"), None
    raw = gzip.decompress(data if data is not None else _read_bytes(path))
    return GZIP, None, raw


def _read_head(path: str, size: int = 262144) -> bytes:
    with get_storage_backend(path).open(path, "rb") as fh:
        return fh.read(size)


def _gzip_member_name(archive: str) -> str:
    """The name a lone gzip's single member gets: the archive minus its .gz."""
    base = os.path.basename(archive)
    return base[:-3] if base.endswith(".gz") else base


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _members(kind: str, handle: Any, archive: str, files_only: bool) -> list[tuple[str, int]]:
    """``(name, size)`` for each member, in archive order."""
    if kind == ZIP:
        return [
            (i.filename, i.file_size)
            for i in handle.infolist()
            if not (files_only and i.is_dir())
        ]
    if kind == TAR:
        return [
            (m.name, m.size)
            for m in handle.getmembers()
            if not files_only or m.isfile()
        ]
    return []  # gzip: filled in by the caller, which knows the archive name


def handle_list(params: dict[str, Any]) -> dict[str, Any]:
    archive = params["archive"]
    pattern = params.get("pattern") or ""
    files_only = params.get("files_only", True)

    kind, handle, raw = _open_archive(archive)
    try:
        if kind == GZIP:
            names = [_gzip_member_name(archive)]
        else:
            names = [n for n, _ in _members(kind, handle, archive, bool(files_only))]
    finally:
        if handle is not None:
            handle.close()

    if pattern:
        names = [n for n in names if fnmatch.fnmatch(posixpath.basename(n), pattern)]
    # Sorted, so a fan-out over the result is reproducible across runs.
    names = sorted(names)
    return {"names": names, "count": len(names)}


# ---------------------------------------------------------------------------
# Reading one member
# ---------------------------------------------------------------------------


def _select_member(names: list[str], member: str, pattern: str, archive: str) -> str:
    if member:
        if member not in names:
            raise KeyError(
                f"member {member!r} not in {archive} — it holds: {', '.join(sorted(names)[:10])}"
            )
        return member
    if pattern:
        matches = sorted(
            n for n in names if fnmatch.fnmatch(n, pattern) or fnmatch.fnmatch(posixpath.basename(n), pattern)
        )
        if not matches:
            raise KeyError(
                f"no member of {archive} matches {pattern!r} — it holds: "
                f"{', '.join(sorted(names)[:10])}"
            )
        return matches[0]
    if len(names) == 1:
        return names[0]
    raise ValueError(
        f"{archive} holds {len(names)} members — pass `member` or `pattern` to say which"
    )


def handle_read_member(params: dict[str, Any]) -> dict[str, Any]:
    archive = params["archive"]
    member = params.get("member") or ""
    pattern = params.get("pattern") or ""
    max_bytes = int(params.get("max_bytes") or 0)
    encoding = params.get("encoding") or "utf-8"

    kind, handle, raw = _open_archive(archive)
    try:
        if kind == GZIP:
            name, data = _gzip_member_name(archive), raw
        else:
            names = [n for n, _ in _members(kind, handle, archive, files_only=True)]
            name = _select_member(names, member, pattern, archive)
            if kind == ZIP:
                data = handle.read(name)
            else:
                extracted = handle.extractfile(name)
                data = extracted.read() if extracted is not None else b""
    finally:
        if handle is not None:
            handle.close()

    size = len(data)
    truncated = 0 < max_bytes < size
    if truncated:
        data = data[:max_bytes]
    return {
        "text": data.decode(encoding, errors="replace"),
        "name": name,
        # The MEMBER's size, not the size of what was read — otherwise a
        # truncated read reports itself as the whole thing.
        "size_bytes": size,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _safe_target(dest: str, name: str, strip: int, fs: Any) -> str | None:
    """Where *name* may be written under *dest*, or None if it must be refused.

    The check ``extractall`` does not do: an absolute member path, or one that
    climbs out with ``..``, writes outside the destination the caller named.
    """
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if strip:
        parts = parts[strip:]
    if not parts:
        return None
    if any(p == ".." for p in parts) or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    return fs.join(dest, *parts)


def handle_extract(params: dict[str, Any]) -> dict[str, Any]:
    archive = params["archive"]
    dest = params["dest"]
    pattern = params.get("pattern") or ""
    strip = int(params.get("strip_components") or 0)
    overwrite = params.get("overwrite", True)
    max_files = int(params.get("max_files") or 0)
    max_bytes = int(params.get("max_bytes") or 0)
    step_log = params.get("_step_log")

    fs = get_storage_backend(dest)
    fs.makedirs(dest, exist_ok=True)

    kind, handle, raw = _open_archive(archive)
    written: list[str] = []
    total = 0
    refused = 0
    try:
        if kind == GZIP:
            entries: list[tuple[str, int]] = [(_gzip_member_name(archive), len(raw))]
        else:
            entries = _members(kind, handle, archive, files_only=True)

        for name, size in entries:
            if pattern and not (
                fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(posixpath.basename(name), pattern)
            ):
                continue
            if kind == TAR and not handle.getmember(name).isfile():
                # Symlinks, hardlinks, devices: a data archive has no business
                # containing them, and a link is the other way out of `dest`.
                refused += 1
                continue

            target = _safe_target(dest, name, strip, fs)
            if target is None:
                refused += 1
                _log(step_log, f"refused member escaping dest: {name!r}", "warning")
                continue
            if max_files and len(written) >= max_files:
                raise ValueError(
                    f"{archive} holds more than max_files={max_files} matching members"
                )
            if max_bytes and total + size > max_bytes:
                raise ValueError(
                    f"extracting {archive} would exceed max_bytes={max_bytes}"
                )
            if not overwrite and fs.exists(target):
                continue

            parent = fs.dirname(target)
            if parent:
                fs.makedirs(parent, exist_ok=True)
            _write_member(kind, handle, raw, name, target, fs)
            written.append(target)
            total += size
    finally:
        if handle is not None:
            handle.close()

    if refused:
        _log(step_log, f"refused {refused} unsafe member(s) — see the log above", "warning")
    _log(step_log, f"extracted {len(written)} member(s), {total:,} bytes, from a {kind} archive")
    return {
        "paths": sorted(written),
        "count": len(written),
        "bytes": total,
        "format": kind,
    }


def _write_member(kind: str, handle: Any, raw: bytes, name: str, target: str, fs: Any) -> None:
    if kind == GZIP:
        with fs.open(target, "wb") as out:
            out.write(raw)
        return
    source = handle.open(name) if kind == ZIP else handle.extractfile(name)
    if source is None:
        return
    try:
        with fs.open(target, "wb") as out:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        source.close()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.Extract": handle_extract,
    f"{NAMESPACE}.List": handle_list,
    f"{NAMESPACE}.ReadMember": handle_read_member,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint — one entrypoint, dispatching on ``_facet_name``."""
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise KeyError(f"no built-in archive handler for {facet!r}")
    return handler(payload)


def facet_names() -> list[str]:
    return sorted(_DISPATCH)


__all__ = ["detect_format", "facet_names", "handle"]
