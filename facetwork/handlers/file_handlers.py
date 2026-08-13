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

"""Handlers for the built-in ``fw.file`` facets (``facetwork/ffl/file.ffl``).

These ship with the framework so a workflow can do ordinary file work without
anyone writing a handler. They are the answer to "can this be a `script {}`
block?" — it cannot: the script sandbox has no ``open`` and no ``os``/``pathlib``
import, and widening it would delete the property the sandbox exists for
(docs/guides/scripts-and-environments.md §5).

**Every path goes through** :mod:`facetwork.runtime.storage`, which dispatches on
the URI scheme. The same workflow therefore runs against a local path, ``s3://``
or ``hdfs://`` unchanged — which is the whole point on a fleet, where the data
lives in the object store rather than on the machine that launched the run. A
``pathlib`` implementation would work on a laptop and silently find nothing on a
runner.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
from typing import Any

from facetwork.runtime.storage import get_storage_backend

logger = logging.getLogger(__name__)

NAMESPACE = "fw.file"

# Reading a file into a step's return value puts it in the step store, which is
# not a blob store. Cap it, and say so in the result rather than truncating
# silently — a caller that gets half a file and no warning will treat it as the
# whole file.
_DEFAULT_MAX_READ = 8 * 1024 * 1024

# Digest is read in chunks so a multi-GB artifact never lands in memory whole.
_HASH_CHUNK = 1024 * 1024


def _log(step_log: Any, message: str, level: str = "info") -> None:
    if callable(step_log):
        step_log(message, level)


def _backend(path: str):
    return get_storage_backend(path)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def handle_list(params: dict[str, Any]) -> dict[str, Any]:
    """Enumerate paths under a directory or prefix.

    Sorted, because an unsorted listing makes a fan-out over it
    irreproducible — the same workflow would build its iterations in a
    different order on each run, and any failure would be hard to correlate.
    """
    path = params["path"]
    pattern = params.get("pattern") or "*"
    recursive = bool(params.get("recursive", False))
    files_only = bool(params.get("files_only", True))
    step_log = params.get("_step_log")

    fs = _backend(path)
    if not fs.exists(path):
        # An absent directory is an empty listing, not a crash: a fan-out over
        # nothing is a legitimate (if suspicious) outcome, and the caller can
        # assert on `count` if it expects otherwise.
        _log(step_log, f"path does not exist, listing empty: {path}", "warning")
        return {"paths": [], "count": 0}

    found: list[str] = []
    if recursive:
        for root, _dirs, files in fs.walk(path):
            for name in files:
                if fnmatch.fnmatch(name, pattern):
                    found.append(fs.join(root, name))
    else:
        for name in fs.listdir(path):
            full = fs.join(path, name)
            if files_only and fs.isdir(full):
                continue
            if fnmatch.fnmatch(name, pattern):
                found.append(full)

    found.sort()
    _log(step_log, f"{len(found)} path(s) under {path} matching {pattern!r}")
    # A REAL list, not json.dumps(...). A Json-typed return that a `foreach`
    # iterates must be the collection itself: hand back a string and the loop
    # iterates its CHARACTERS. That mistake produced 360 tasks for '[', '"',
    # '/', 'p', 'r', 'i', 'v'... from a three-file directory.
    return {"paths": found, "count": len(found)}


def handle_filter(params: dict[str, Any]) -> dict[str, Any]:
    """Narrow a path list by glob. No I/O — pure list work on basenames."""
    # Accept either a real list (what List returns) or a JSON string, since a
    # caller may have round-tripped it through something that serialises.
    raw = params.get("paths")
    paths = json.loads(raw) if isinstance(raw, str) else (raw or [])
    pattern = params.get("pattern") or "*"
    kept = [p for p in paths if fnmatch.fnmatch(os.path.basename(str(p)), pattern)]
    return {"paths": kept, "count": len(kept)}


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def handle_exists(params: dict[str, Any]) -> dict[str, Any]:
    path = params["path"]
    return {"exists": _backend(path).exists(path)}


def handle_stat(params: dict[str, Any]) -> dict[str, Any]:
    """Size / mtime / kind.

    Reports ``exists = false`` instead of raising, so a workflow can branch on
    absence with `when` rather than having to `catch` it.
    """
    path = params["path"]
    fs = _backend(path)
    if not fs.exists(path):
        return {"exists": False, "size_bytes": 0, "modified_at": 0.0, "is_dir": False}
    is_dir = fs.isdir(path)
    return {
        "exists": True,
        # A directory has no meaningful size across backends (an S3 prefix has
        # none at all), so report 0 rather than something backend-specific.
        "size_bytes": 0 if is_dir else int(fs.getsize(path)),
        "modified_at": float(fs.getmtime(path)),
        "is_dir": is_dir,
    }


def handle_hash(params: dict[str, Any]) -> dict[str, Any]:
    """Content digest, read in chunks.

    This is the key that binds an artifact to what produced it — the thing an
    mtime cannot express. See agent-spec/cache-layout.agent-spec.yaml
    (`read_protocol.reuse_decision`) for why a reuse decision needs it.
    """
    path = params["path"]
    algorithm = (params.get("algorithm") or "sha256").lower()
    try:
        digest = hashlib.new(algorithm)
    except ValueError:
        raise ValueError(f"unknown hash algorithm {algorithm!r}") from None

    fs = _backend(path)
    size = 0
    with fs.open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return {"digest": digest.hexdigest(), "algorithm": algorithm, "size_bytes": size}


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def handle_read_text(params: dict[str, Any]) -> dict[str, Any]:
    path = params["path"]
    encoding = params.get("encoding") or "utf-8"
    # An explicit 0 means "no cap"; absent means the default cap. Distinguishing
    # them matters because `int(params.get(x, D) or D)` would silently rewrite a
    # deliberate 0 into the default.
    raw_max = params.get("max_bytes", None)
    max_bytes = _DEFAULT_MAX_READ if raw_max in (None, "") else int(raw_max)
    step_log = params.get("_step_log")

    fs = _backend(path)
    size = int(fs.getsize(path))
    with fs.open(path, "rb") as fh:
        data = fh.read() if max_bytes <= 0 else fh.read(max_bytes)
    truncated = 0 < max_bytes < size
    if truncated:
        _log(step_log, f"read truncated at {max_bytes} of {size} bytes: {path}", "warning")
    return {
        "text": data.decode(encoding, errors="replace"),
        "size_bytes": size,
        "truncated": truncated,
    }


def _write_bytes(path: str, payload: bytes) -> int:
    fs = _backend(path)
    parent = fs.dirname(path)
    if parent:
        fs.makedirs(parent, exist_ok=True)
    with fs.open(path, "wb") as fh:
        fh.write(payload)
    return len(payload)


def handle_write_text(params: dict[str, Any]) -> dict[str, Any]:
    path = params["path"]
    payload = str(params.get("text", "")).encode(params.get("encoding") or "utf-8")
    return {"path": path, "size_bytes": _write_bytes(path, payload)}


def handle_read_json(params: dict[str, Any]) -> dict[str, Any]:
    path = params["path"]
    fs = _backend(path)
    with fs.open(path, "rb") as fh:
        raw = fh.read()
    # The parsed value, not a re-serialised string — same reason as List: a
    # downstream `foreach` over `data` must see the collection, not its text.
    return {"data": json.loads(raw.decode("utf-8")), "size_bytes": len(raw)}


def handle_write_json(params: dict[str, Any]) -> dict[str, Any]:
    path = params["path"]
    data = params.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    indent = int(params.get("indent") or 0)
    text = json.dumps(data, indent=indent or None, sort_keys=True)
    return {"path": path, "size_bytes": _write_bytes(path, text.encode("utf-8"))}


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def handle_copy(params: dict[str, Any]) -> dict[str, Any]:
    """Copy, streamed.

    Chunked rather than read-then-write so a multi-GB artifact does not have to
    fit in the runner's memory, and so a copy BETWEEN backends (local → s3)
    works without a special case.
    """
    source, dest = params["source"], params["dest"]
    src_fs, dst_fs = _backend(source), _backend(dest)
    parent = dst_fs.dirname(dest)
    if parent:
        dst_fs.makedirs(parent, exist_ok=True)
    total = 0
    with src_fs.open(source, "rb") as src, dst_fs.open(dest, "wb") as dst:
        while True:
            chunk = src.read(_HASH_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            dst.write(chunk)
    return {"dest": dest, "size_bytes": total}


def handle_delete(params: dict[str, Any]) -> dict[str, Any]:
    """Delete a file, or a tree with `recursive`.

    An absent path returns ``deleted = false`` rather than raising: removing
    something already gone is the outcome the caller asked for, and making it an
    error forces a `catch` around every cleanup step.
    """
    path = params["path"]
    recursive = bool(params.get("recursive", False))
    fs = _backend(path)
    if not fs.exists(path):
        return {"deleted": False}
    if fs.isdir(path):
        if not recursive:
            raise IsADirectoryError(f"{path} is a directory — pass recursive = true to delete it")
        fs.rmtree(path)
    else:
        fs.remove(path)
    return {"deleted": True}


def handle_make_dir(params: dict[str, Any]) -> dict[str, Any]:
    path = params["path"]
    _backend(path).makedirs(path, exist_ok=True)
    return {"path": path}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.List": handle_list,
    f"{NAMESPACE}.Filter": handle_filter,
    f"{NAMESPACE}.Exists": handle_exists,
    f"{NAMESPACE}.Stat": handle_stat,
    f"{NAMESPACE}.Hash": handle_hash,
    f"{NAMESPACE}.ReadText": handle_read_text,
    f"{NAMESPACE}.WriteText": handle_write_text,
    f"{NAMESPACE}.ReadJson": handle_read_json,
    f"{NAMESPACE}.WriteJson": handle_write_json,
    f"{NAMESPACE}.Copy": handle_copy,
    f"{NAMESPACE}.Delete": handle_delete,
    f"{NAMESPACE}.MakeDir": handle_make_dir,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint.

    ONE entrypoint dispatching on ``_facet_name``. Registering several facets
    against separate entrypoints in one module collides in the dispatcher's
    cache key, so this form is the one that is safe by construction.
    """
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise KeyError(f"no built-in file handler for {facet!r}")
    return handler(payload)


def facet_names() -> list[str]:
    """The facets this module serves — used by the built-in registration."""
    return sorted(_DISPATCH)
