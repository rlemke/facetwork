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

"""Handlers for the built-in ``fw.http`` facets (``facetwork/ffl/http.ffl``).

**Stdlib only** — ``urllib.request``, not ``requests``. The compiler and runtime
depend on lark and nothing else, and a framework facet must not change that.

The point of this module is not "download a file"; 14 of 23 domains already do
that. It is to make the *reuse decision* sound by default, because the audit that
prompted it found the hand-rolled versions getting that wrong in ways nothing
reports: a cache key recorded and never compared, a moving ``latest`` target
frozen at first fetch, and nineteen call sites skipping on existence alone.

So a cached artifact is reused only when it is provably still current:

* **integrity** — the stored size matches the sidecar, so a truncated download is
  never served as complete, and the artifact came from the same URL;
* **freshness** — within ``max_age_hours``, which the *domain* sets because only
  the domain knows how often its source moves;
* **upstream** — a conditional GET carrying ``If-None-Match`` /
  ``If-Modified-Since``. A **304** is upstream stating that what we hold is still
  current: the strongest and cheapest answer available. For publishers that ship
  a checksum beside the artifact (Geofabrik's PBFs), ``checksum_url`` re-fetches
  and compares that instead.

Every fetch writes ``<dest>.meta.json`` recording the digest, the source URL, the
upstream validators and ``fetched_at`` — the provenance a *derived* step needs to
notice later that its input changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from facetwork.handlers._heartbeat import heartbeats
from facetwork.runtime.storage import LocalStorageBackend, get_storage_backend

logger = logging.getLogger(__name__)

NAMESPACE = "fw.http"
SIDECAR_SUFFIX = ".meta.json"

# The ALGORITHM half of the cache key. Bump it when the sidecar's shape or the
# reuse rules change: a cache written by an older fetcher must not be reused by a
# newer one that would have decided differently.
TOOL_VERSION = "1.0"

_CHUNK = 1024 * 1024
_DEFAULT_UA = "facetwork-fw.http/1.0 (+https://github.com/rlemke/facetwork)"


def _log(step_log: Any, message: str, level: str = "info") -> None:
    if callable(step_log):
        step_log(message, level)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(UTC) - stamp).total_seconds() / 3600.0


def _request(url: str, ua: str, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": ua or _DEFAULT_UA})
    for key, value in (headers or {}).items():
        if value:
            req.add_header(key, value)
    return req


def _header(resp: Any, name: str) -> str:
    headers = getattr(resp, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return ""
    return headers.get(name) or ""


# ---------------------------------------------------------------------------
# Sidecar — the provenance that makes a later reuse decision possible
# ---------------------------------------------------------------------------


def _sidecar_path(dest: str) -> str:
    return dest + SIDECAR_SUFFIX


def _read_sidecar(dest: str) -> dict | None:
    fs = get_storage_backend(dest)
    path = _sidecar_path(dest)
    if not fs.exists(path):
        return None
    try:
        with fs.open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        # An unreadable sidecar means we cannot PROVE the artifact is current,
        # and trusting it anyway is exactly the failure this module exists to
        # prevent. Treat it as absent and re-fetch.
        logger.warning("unreadable sidecar for %s — refetching", dest)
        return None
    return data if isinstance(data, dict) else None


def _write_sidecar(dest: str, payload: dict) -> None:
    fs = get_storage_backend(dest)
    with fs.open(_sidecar_path(dest), "wb") as fh:
        fh.write(json.dumps(payload, indent=1, sort_keys=True).encode("utf-8"))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _stream_to(dest: str, response: Any) -> tuple[int, str]:
    """Stream *response* to *dest*, returning ``(size, sha256)``.

    An interrupted download must never leave something at the final path that
    the next run would mistake for a finished one. The two backend families need
    different handling to get that:

    * **local** — write to ``<dest>.part`` and ``os.replace`` on success, which
      is atomic within a filesystem;
    * **object stores** — the S3/HDFS write streams buffer and upload on
      ``close()``, and discard the buffer if the writer body raises, so a
      partial object never appears. Staging there would only mean moving the
      bytes twice.

    (The object-store path buffers the whole body in memory — see
    ``_S3WriteStream``. A multi-GB artifact belongs on a local ``dest``, staged
    and uploaded by the caller.)
    """
    fs = get_storage_backend(dest)
    parent = fs.dirname(dest)
    if parent:
        fs.makedirs(parent, exist_ok=True)

    local = isinstance(fs, LocalStorageBackend)
    target = f"{dest}.part" if local else dest
    digest = hashlib.sha256()
    size = 0
    try:
        with fs.open(target, "wb") as out:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        if local and fs.exists(target):
            fs.remove(target)
        raise
    if local:
        os.replace(target, dest)
    return size, digest.hexdigest()


def _published_checksum(url: str, ua: str, timeout: int) -> str | None:
    """The digest from a publisher's checksum file, or None if unreachable.

    ``<hash>  <filename>`` is near-universal for .md5/.sha256 sidecars, so take
    the first whitespace-separated token. None means "could not ask" — which is
    NOT the same as "changed", and the caller must not conflate them.
    """
    try:
        with urllib.request.urlopen(_request(url, ua), timeout=timeout) as resp:
            text = resp.read(4096).decode("utf-8", errors="replace").strip()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return text.split()[0] if text else None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _integrity_ok(fs: Any, dest: str, side: dict, url: str, expected: str, step_log: Any) -> bool:
    """Whether the cached artifact is intact and is the one being asked for.

    These are the checks that need no network. Failing any of them means the
    cache cannot be reused whatever upstream would say.
    """
    stored = int(side.get("size_bytes", -1))
    actual = int(fs.getsize(dest))
    if stored != actual:
        _log(step_log, f"cache size {actual} != recorded {stored} — refetching", "warning")
        return False
    if side.get("tool_version") != TOOL_VERSION:
        _log(step_log, "cache written by a different fetcher version — refetching")
        return False
    if side.get("url") != url:
        # Same dest, different source: what is there is not what was asked for.
        _log(step_log, "cache came from a different URL — refetching")
        return False
    if expected and str(side.get("sha256", "")).lower() != expected:
        _log(step_log, "cache does not match expected_sha256 — refetching", "warning")
        return False
    return True


@heartbeats("fetching")
def handle_fetch(params: dict[str, Any]) -> dict[str, Any]:
    url = params["url"]
    dest = params["dest"]
    force = bool(params.get("force", False))
    max_age = float(params.get("max_age_hours") or 0.0)
    checksum_url = params.get("checksum_url") or ""
    expected = (params.get("expected_sha256") or "").lower()
    timeout = int(params.get("timeout_seconds") or 300)
    ua = params.get("user_agent") or _DEFAULT_UA
    step_log = params.get("_step_log")

    fs = get_storage_backend(dest)
    side = None if force else _read_sidecar(dest)
    if side is not None and not (
        fs.exists(dest) and _integrity_ok(fs, dest, side, url, expected, step_log)
    ):
        side = None

    if side is not None and checksum_url:
        # UPSTREAM check, strongest form first.
        published = _published_checksum(checksum_url, ua, timeout)
        if published is None:
            # "Could not ask" is not "changed" — fall through to the conditional
            # GET rather than re-downloading a PBF because of a blip.
            _log(step_log, "checksum file unreachable — falling back to conditional GET", "warning")
        elif published.lower() == str(side.get("published_checksum", "")).lower():
            _log(step_log, "published checksum unchanged — cache is current", "success")
            return _cached_result(dest, side, revalidated=True)
        else:
            _log(step_log, "published checksum changed upstream — refetching")
            side = None

    if side is not None:
        age = _age_hours(side.get("fetched_at"))
        if max_age > 0 and age is not None and age < max_age:
            _log(step_log, f"cache is {age:.1f}h old, within {max_age:g}h — reusing")
            return _cached_result(dest, side, revalidated=False)
        # Ask upstream instead of guessing. One round trip, usually no body.
        validators = {
            "If-None-Match": side.get("etag") or "",
            "If-Modified-Since": side.get("last_modified") or "",
        }
        try:
            with urllib.request.urlopen(_request(url, ua, validators), timeout=timeout) as resp:
                return _store(dest, resp, url, step_log, expected, checksum_url, ua, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 304:
                raise
            _log(step_log, "upstream 304 Not Modified — cache is current", "success")
            side["fetched_at"] = _now_iso()  # revalidated now; restart the age clock
            _write_sidecar(dest, side)
            return _cached_result(dest, side, revalidated=True)

    with urllib.request.urlopen(_request(url, ua), timeout=timeout) as resp:
        return _store(dest, resp, url, step_log, expected, checksum_url, ua, timeout)


def _store(
    dest: str,
    resp: Any,
    url: str,
    step_log: Any,
    expected: str = "",
    checksum_url: str = "",
    ua: str = "",
    timeout: int = 300,
) -> dict[str, Any]:
    size, sha = _stream_to(dest, resp)
    if expected and sha.lower() != expected:
        # Refuse to publish a body that is not what was asked for, and remove
        # it — leaving it would let the next run reuse the wrong bytes.
        fs = get_storage_backend(dest)
        if fs.exists(dest):
            fs.remove(dest)
        raise ValueError(f"sha256 mismatch for {url}: got {sha}, expected {expected}")

    sidecar = {
        "url": url,
        "sha256": sha,
        "size_bytes": size,
        "fetched_at": _now_iso(),
        "etag": _header(resp, "ETag"),
        "last_modified": _header(resp, "Last-Modified"),
        "content_type": _header(resp, "Content-Type"),
        # Recorded on EVERY store, not only when this call came in through the
        # checksum path — otherwise the next run has nothing to compare against
        # and re-downloads forever.
        "published_checksum": (_published_checksum(checksum_url, ua, timeout) or "")
        if checksum_url
        else "",
        "tool_version": TOOL_VERSION,
    }
    _write_sidecar(dest, sidecar)
    _log(step_log, f"fetched {size:,} bytes from {url}", "success")
    return {
        "path": dest,
        "size_bytes": size,
        "sha256": sha,
        "was_cached": False,
        "revalidated": False,
        "status": int(getattr(resp, "status", 200) or 200),
        "etag": sidecar["etag"],
        "fetched_at": sidecar["fetched_at"],
    }


def _cached_result(dest: str, side: dict, revalidated: bool) -> dict[str, Any]:
    return {
        "path": dest,
        "size_bytes": int(side.get("size_bytes", 0)),
        "sha256": side.get("sha256", ""),
        "was_cached": True,
        "revalidated": revalidated,
        "status": 304 if revalidated else 200,
        "etag": side.get("etag", ""),
        "fetched_at": side.get("fetched_at", ""),
    }


# ---------------------------------------------------------------------------
# Small responses
# ---------------------------------------------------------------------------


def handle_get(params: dict[str, Any]) -> dict[str, Any]:
    url = params["url"]
    max_bytes = int(params.get("max_bytes") or 1_048_576)
    timeout = int(params.get("timeout_seconds") or 60)
    ua = params.get("user_agent") or _DEFAULT_UA
    with urllib.request.urlopen(_request(url, ua), timeout=timeout) as resp:
        # One byte past the cap, so truncation is REPORTED rather than guessed
        # at — a caller handed a silently clipped body treats it as the whole
        # response.
        raw = resp.read(max_bytes + 1) if max_bytes > 0 else resp.read()
        status = int(getattr(resp, "status", 200) or 200)
    truncated = max_bytes > 0 and len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    return {
        "body": raw.decode("utf-8", errors="replace"),
        "status": status,
        "size_bytes": len(raw),
        "truncated": truncated,
    }


def handle_get_json(params: dict[str, Any]) -> dict[str, Any]:
    cap = int(params.get("max_bytes") or 8_388_608)
    out = handle_get({**params, "max_bytes": cap})
    if out["truncated"]:
        # Truncated JSON does not parse, and a JSONDecodeError three layers down
        # says nothing about what actually happened.
        raise ValueError(
            f"response exceeded max_bytes ({cap}) — raise it, or use fw.http.Fetch "
            "to put the body in storage instead of in a step value"
        )
    return {
        # Parsed, not re-serialised: a downstream `foreach` must receive the
        # collection, not its text (the fw.file.List bug, in a new place).
        "data": json.loads(out["body"]),
        "status": out["status"],
        "size_bytes": out["size_bytes"],
    }


def handle_provenance(params: dict[str, Any]) -> dict[str, Any]:
    side = _read_sidecar(params["path"])
    if side is None:
        return {
            "exists": False,
            "url": "",
            "sha256": "",
            "size_bytes": 0,
            "fetched_at": "",
            "etag": "",
        }
    return {
        "exists": True,
        "url": side.get("url", ""),
        "sha256": side.get("sha256", ""),
        "size_bytes": int(side.get("size_bytes", 0)),
        "fetched_at": side.get("fetched_at", ""),
        "etag": side.get("etag", ""),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.Fetch": handle_fetch,
    f"{NAMESPACE}.Get": handle_get,
    f"{NAMESPACE}.GetJson": handle_get_json,
    f"{NAMESPACE}.Provenance": handle_provenance,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint — one entrypoint, dispatching on ``_facet_name``.

    One per module, not one per facet: the dispatcher's cache key does not
    include the entrypoint, so several facets registered against the same module
    with different entrypoints collide (#32).
    """
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise KeyError(f"no built-in http handler for {facet!r}")
    return handler(payload)


def facet_names() -> list[str]:
    return sorted(_DISPATCH)


__all__ = ["TOOL_VERSION", "facet_names", "handle"]
