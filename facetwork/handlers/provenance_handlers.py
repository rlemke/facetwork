"""Built-in ``fw.provenance`` handlers — a run-level provenance manifest.

``fw.http`` already records per-artifact metadata in a ``<dest>.meta.json``
sidecar. What was missing is the run-level answer: which workflow produced these
outputs, from which inputs, with which software, and whether it ran live.

⚠️ **An input with no recorded provenance is reported, not omitted.** A manifest
that lists only the inputs it happens to understand looks complete while being
least trustworthy exactly where it matters. ``incomplete`` names them and
``complete`` is False, so a downstream reader can tell "everything is tracked"
from "everything I could see is tracked".

⚠️ **``run_mode`` is declared by the caller, never inferred.** A pipeline that
fell back to synthetic data cannot be detected from its outputs afterwards, so
the workflow has to say. A manifest asserting "live" for a mock run is worse
than no manifest, because it is evidence for a claim that is false.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
from typing import Any

from facetwork.handlers._heartbeat import heartbeating
from facetwork.runtime.storage import get_storage_backend

logger = logging.getLogger(__name__)

NAMESPACE = "fw.provenance"
SIDECAR_SUFFIX = ".meta.json"
MANIFEST_NAME = "provenance.json"
#: Hashing every output of a large run is not free; above this a size+mtime
#: identity is recorded instead, and the manifest says which was used rather
#: than implying a digest it did not compute.
_HASH_MAX_BYTES = 256 * 1024 * 1024


def _log(params: dict[str, Any]):
    cb = params.get("_step_log")
    return cb if callable(cb) else (lambda *a, **k: None)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _software() -> dict[str, Any]:
    """What produced this, at the granularity someone could reinstall."""
    from importlib.metadata import distributions

    pkgs: dict[str, str] = {}
    for d in distributions():
        try:
            name = d.metadata["Name"]
        except Exception:  # noqa: BLE001 - a broken dist must not sink the manifest
            continue
        if not name:
            continue
        if name == "facetwork" or name.startswith("fwh") or name.startswith("fwh-"):
            pkgs[name] = d.version or ""
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": dict(sorted(pkgs.items())),
    }


def _container() -> dict[str, str]:
    """Image identity when we are in a container, empty when we are not.

    Reported as empty rather than guessed: "unknown" and "not containerised"
    are different facts, and the caller can tell them apart from the run host.
    """
    out: dict[str, str] = {}
    for var in ("FW_IMAGE", "FW_IMAGE_DIGEST", "HOSTNAME"):
        v = os.environ.get(var)
        if v:
            out[var.lower()] = v
    if os.path.exists("/.dockerenv"):
        out["containerised"] = "true"
    return out


def _read_sidecar(path: str) -> dict[str, Any] | None:
    side = path + SIDECAR_SUFFIX
    try:
        with open(side, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _iter_outputs(root: str) -> list[str]:
    if os.path.isfile(root):
        return [root]
    found: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f == MANIFEST_NAME or f.endswith(SIDECAR_SUFFIX):
                continue
            found.append(os.path.join(dirpath, f))
    return sorted(found)


def handle_manifest(params: dict[str, Any]) -> dict[str, Any]:
    say = _log(params)
    outputs_root = str(params.get("outputs") or "").strip()
    if not outputs_root:
        raise ValueError("outputs is required")
    inputs = list(params.get("inputs") or [])
    run_mode = str(params.get("run_mode") or "live").strip().lower()
    if run_mode not in ("live", "mock", "mixed"):
        raise ValueError(f"run_mode must be live, mock or mixed; got {run_mode!r}")

    recorded_inputs: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for p in inputs:
        meta = _read_sidecar(str(p))
        if meta is None:
            incomplete.append(str(p))
            recorded_inputs.append({"path": str(p), "provenance": "unknown",
                                    "why": "no .meta.json sidecar"})
        else:
            recorded_inputs.append({"path": str(p), "provenance": "recorded", **meta})

    recorded_outputs: list[dict[str, Any]] = []
    found = _iter_outputs(outputs_root)
    # Hashing a run's whole output tree is the one part that can block for
    # minutes; keep the liveness signal alive across it.
    with heartbeating(params, f"hashing {len(found)} output(s)"):
      for p in found:
          try:
              size = os.path.getsize(p)
          except OSError:
              continue
          entry: dict[str, Any] = {"path": os.path.relpath(p, outputs_root)
                                   if os.path.isdir(outputs_root) else p,
                                   "size_bytes": size}
          if size <= _HASH_MAX_BYTES:
              entry["sha256"] = _sha256(p)
          else:
              entry["identity"] = "size+mtime"
              entry["mtime"] = int(os.path.getmtime(p))
          recorded_outputs.append(entry)

    extra: dict[str, Any] = {}
    raw = str(params.get("extra_json") or "").strip()
    if raw:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("extra_json must be a JSON object")
        extra = parsed

    manifest = {
        "schema": "fw.provenance/1",
        "run": {
            "workflow": params.get("_workflow") or "",
            "step_id": params.get("_step_id") or "",
            "facet": params.get("_facet_name") or "",
            "run_mode": run_mode,
        },
        "software": _software(),
        "container": _container(),
        "inputs": recorded_inputs,
        "outputs": recorded_outputs,
        "complete": not incomplete,
        "extra": extra,
    }

    dest = str(params.get("dest") or "").strip()
    if not dest:
        dest = (os.path.join(outputs_root, MANIFEST_NAME)
                if os.path.isdir(outputs_root)
                else outputs_root + "." + MANIFEST_NAME)
    body = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    backend = get_storage_backend()
    if hasattr(backend, "write_text"):
        backend.write_text(dest, body)
    else:  # pragma: no cover - local fallback
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)

    if incomplete:
        say(f"provenance: {len(incomplete)} input(s) have NO recorded provenance "
            f"— manifest marked incomplete", level="warning")
    return {
        "path": dest,
        "inputs_recorded": len(recorded_inputs) - len(incomplete),
        "outputs_recorded": len(recorded_outputs),
        "incomplete": incomplete,
        "complete": not incomplete,
    }


def handle_verify(params: dict[str, Any]) -> dict[str, Any]:
    """Does the manifest still describe what is on disk?"""
    path = str(params.get("path") or "").strip()
    if not path:
        raise ValueError("path is required")
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    root = os.path.dirname(os.path.abspath(path))

    changed: list[str] = []
    missing: list[str] = []
    checked = 0
    entries = manifest.get("outputs", [])
    with heartbeating(params, f"verifying {len(entries)} output(s)"):
      for entry in entries:
          rel = entry.get("path") or ""
          full = rel if os.path.isabs(rel) else os.path.join(root, rel)
          if not os.path.exists(full):
              missing.append(rel)
              continue
          checked += 1
          if "sha256" in entry:
              if _sha256(full) != entry["sha256"]:
                  changed.append(rel)
          else:
              if os.path.getsize(full) != entry.get("size_bytes"):
                  changed.append(rel)
    return {
        "matches": not changed and not missing,
        "checked": checked,
        "changed": changed,
        "missing": missing,
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.Manifest": handle_manifest,
    f"{NAMESPACE}.Verify": handle_verify,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint — one entrypoint, dispatching on ``_facet_name``."""
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise KeyError(f"no built-in provenance handler for {facet!r}")
    return handler(payload)


def facet_names() -> list[str]:
    return sorted(_DISPATCH)
