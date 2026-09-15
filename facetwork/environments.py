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

"""Environment manifest freezing (docs/architecture/script-environments.md §5).

An ``environment`` declaration's ``requires`` list is a *request*
(``shapely>=2.0``); a run must record an *answer*. At publish time each
declaration is resolved into a frozen **manifest** and content-addressed by a
**manifest hash** — the hash is what script tasks carry and what runners
advertise, so re-running a workflow later uses the versions it was published
with, not whatever the index serves that day (the catalog's hermetic
pinned-dep model applied to script dependencies).

Resolution for ``language = "python"`` uses ``pip install --dry-run --report``
(no installation, resolver only). Specs that are already exact (``==``) pass
through without touching the network. Foreign languages carry their
``requires`` opaquely — their environments are provided by out-of-band agents
and facetwork does not resolve them. Resolution can be disabled with
``FW_ENV_RESOLVE=off`` (loose specs then freeze as written, flagged
``resolved: false``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

# Spec is exact if every clause is an == pin (no ranges, no bare names).
_EXACT_SPEC = re.compile(r"^[A-Za-z0-9._-]+(\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.*+!_-]+$")


def resolution_enabled() -> bool:
    """False when FW_ENV_RESOLVE is off — freeze specs as written."""
    return os.environ.get("FW_ENV_RESOLVE", "on").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _resolve_python_requires(requires: list[str]) -> tuple[list[str], bool]:
    """Resolve loose python specs to exact pins.

    Returns ``(pins, resolved)``. Already-exact spec lists pass through with
    ``resolved=True`` and no network. Otherwise ``pip install --dry-run
    --report`` performs full resolution without installing; on any failure the
    specs freeze as written with ``resolved=False`` (a later re-publish can
    resolve them — an unresolved manifest still hashes deterministically).
    """
    if not requires:
        return [], True
    if all(_EXACT_SPEC.match(s.strip()) for s in requires):
        return [s.strip() for s in requires], True
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--quiet",
                "--report",
                "-",
                *requires,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.warning(
                "Environment resolution failed (pip exit %d): %s",
                proc.returncode,
                proc.stderr.strip()[:400],
            )
            return [s.strip() for s in requires], False
        report = json.loads(proc.stdout)
        pins = sorted(
            f"{item['metadata']['name'].lower()}=={item['metadata']['version']}"
            for item in report.get("install", [])
        )
        return pins, True
    except Exception as exc:  # noqa: BLE001 - freezing must not break publish
        logger.warning("Environment resolution errored: %s", exc)
        return [s.strip() for s in requires], False


def freeze_environment(decl: dict) -> dict:
    """Build the frozen manifest for an EnvironmentDecl dict.

    The manifest is canonical (sorted keys, sorted pins) so its hash is
    deterministic across hosts and re-emissions.
    """
    language = (decl.get("language") or "").lower()
    requires = list(decl.get("requires") or [])
    if language == "python" and resolution_enabled():
        pins, resolved = _resolve_python_requires(requires)
    else:
        pins, resolved = [s.strip() for s in requires], language != "python"
    return {
        "language": language,
        "requires": requires,
        "pins": pins,
        "resolved": resolved,
    }


def manifest_hash(manifest: dict) -> str:
    """Content-address a manifest: sha256 of its canonical JSON identity.

    Only fields that change what actually runs participate (language + pins);
    the loose ``requires`` are provenance, not identity.
    """
    identity = {
        "language": manifest.get("language", ""),
        "pins": sorted(manifest.get("pins") or []),
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def annotate_program(program_dict: dict) -> dict[str, str]:
    """Freeze every EnvironmentDecl in a compiled program dict, in place.

    Adds ``manifest`` and ``manifest_hash`` to each EnvironmentDecl and
    returns ``{qualified_name: manifest_hash}`` (also keyed by short name
    within its namespace for local references). Idempotent: declarations
    already carrying a manifest are left untouched, so a re-annotation of a
    stored compiled_ast never re-resolves.
    """
    hashes: dict[str, str] = {}

    def _walk(decls: list, ns: str) -> None:
        for d in decls:
            if not isinstance(d, dict):
                continue
            dtype = d.get("type")
            if dtype == "Namespace":
                _walk(d.get("declarations") or [], d.get("name") or "")
            elif dtype == "EnvironmentDecl":
                if "manifest_hash" not in d:
                    manifest = freeze_environment(d)
                    d["manifest"] = manifest
                    d["manifest_hash"] = manifest_hash(manifest)
                qualified = f"{ns}.{d['name']}" if ns else d["name"]
                hashes[qualified] = d["manifest_hash"]

    _walk(program_dict.get("declarations") or [], "")
    return hashes


def env_root() -> str:
    """Directory of materialized environment venvs (FW_ENV_ROOT)."""
    return os.environ.get("FW_ENV_ROOT", "/opt/fw_envs")


def interpreter_for_hash(manifest_hash_: str) -> str | None:
    """Path to the interpreter of a materialized environment, or None.

    A python environment is *provided* on this host when
    ``$FW_ENV_ROOT/<hash>/bin/python`` exists (pre-baked at image build or
    lazily materialized).
    """
    path = os.path.join(env_root(), manifest_hash_, "bin", "python")
    return path if os.path.exists(path) else None


#: Written inside a materialized venv once its declared packages have been
#: IMPORTED successfully on this host. Its absence means "not verified here",
#: never "broken" — an env baked by an older image simply gets verified on first
#: discovery.
VERIFIED_MARKER = ".fw-verified"


def _top_level_modules(pin: str) -> str:
    """Distribution name from a pin (``numpy==2.5.3``, ``pkg[extra]>=1``)."""
    name = re.split(r"[<>=!~\[;\s]", pin.strip(), 1)[0].strip()
    return name


def smoke_import(interpreter: str, pins: list[str], timeout: int = 300) -> tuple[bool, str]:
    """Import every DECLARED package of an environment in its own interpreter.

    ⚠️ This exists because "the environment is provided here" was a claim about
    files on disk, not about whether this host can RUN them, and the two come
    apart on real hardware. macmini01 is an Intel Core 2 Duo (2009) with no
    SSE4.2/POPCNT, so it does not meet x86-64-v2: `pip install numpy` there
    SUCCEEDS — the x86_64 wheel is perfectly valid — and only `import numpy`
    fails. Without this check the host materializes the venv, advertises the
    hash honestly by the old definition, claims the task, and fails at dispatch.

    Only the DECLARED pins are imported, not every installed distribution.
    That is deliberate on both sides: importing a declared package exercises its
    transitive dependencies anyway (a broken numpy breaks `import pandas`), while
    walking every distribution would import optional backends that legitimately
    raise and would turn this into a source of false failures.

    Distribution name != import name (``Pillow`` -> ``PIL``), so the module names
    come from each distribution's own ``top_level.txt`` where it has one.
    """
    probe = r'''
import importlib, sys
import importlib.metadata as md

failed = []
for dist_name in sys.argv[1:]:
    # Installed at all? A pin that pip did not actually place is a broken
    # environment, and without this the ModuleNotFoundError below would be
    # swallowed as "a shim that is not importable on its own" and PASS.
    try:
        dist = md.distribution(dist_name)
    except Exception:
        failed.append(f"{dist_name}: declared in the manifest but not installed")
        continue
    mods = []
    try:
        top = dist.read_text("top_level.txt") or ""
        mods = [m.strip() for m in top.split() if m.strip() and not m.startswith("_")]
    except Exception:
        pass
    if not mods:
        mods = [dist_name.replace("-", "_")]
    for m in mods:
        try:
            importlib.import_module(m)
        except ModuleNotFoundError:
            # A top_level entry that is not importable on its own is common
            # (namespace shims, stubs). Not evidence the environment is broken.
            continue
        except Exception as exc:
            failed.append(f"{dist_name}:{m}: {type(exc).__name__}: {exc}")
            break
if failed:
    print("; ".join(failed)[:800], file=sys.stderr)
    sys.exit(1)
'''
    names = [_top_level_modules(p) for p in pins]
    names = [n for n in names if n]
    if not names:
        return True, ""
    try:
        proc = subprocess.run([interpreter, "-c", probe, *names],
                              capture_output=True, text=True, timeout=timeout)
    except Exception as exc:                                   # noqa: BLE001
        return False, f"could not run the probe: {type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "").strip()[:800]


def _mark_verified(target: str) -> None:
    """Record that this venv's packages imported here. Best effort: a read-only
    env root just means the check runs again next start, never a false claim."""
    try:
        with open(os.path.join(target, VERIFIED_MARKER), "w", encoding="utf-8") as fh:
            fh.write(f"{time.time():.0f} {sys.executable}\n")
    except OSError:
        pass


def discover_provided_environments(manifests: dict | None = None) -> list[str]:
    """Manifest hashes materialized AND verified importable on this host.

    ⚠️ Verification is persisted (``VERIFIED_MARKER``), because this function is
    a filesystem scan: without a marker, an environment whose smoke import failed
    would be re-advertised on the very next runner start. An env with no marker
    (baked by an older image) is verified lazily here and then marked, so hosts
    converge without a rebuild.

    ``manifests`` maps hash -> manifest so the pins are known. When it is absent
    the pins are read from the venv's own recorded manifest if one was written;
    an env whose pins cannot be determined is advertised unchanged rather than
    dropped — declining work over a missing bookkeeping file would be worse than
    the hole this closes.
    """
    root = env_root()
    try:
        candidates = sorted(
            d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "bin", "python"))
        )
    except OSError:
        return []

    provided = []
    for h in candidates:
        target = os.path.join(root, h)
        if os.path.exists(os.path.join(target, VERIFIED_MARKER)):
            provided.append(h)
            continue
        pins = []
        m = (manifests or {}).get(h) or _recorded_manifest(target)
        if m:
            pins = list(m.get("pins") or [])
        if not pins:
            provided.append(h)                     # unknown pins: unchanged behaviour
            continue
        ok, err = smoke_import(os.path.join(target, "bin", "python"), pins)
        if ok:
            _mark_verified(target)
            provided.append(h)
        else:
            logger.warning(
                "environment %s is installed here but its packages do not import "
                "on this host — NOT advertising it: %s", h, err)
    return provided


def _recorded_manifest(target: str) -> dict | None:
    """The manifest written beside a materialized venv, when present."""
    try:
        with open(os.path.join(target, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def materialize_environment(manifest: dict, hash_: str) -> str:
    """Create the venv for a frozen python manifest; return its interpreter.

    Idempotent: an existing venv is returned as-is. Installation uses the
    manifest's exact pins. A wheelhouse (FW_ENV_WHEELHOUSE, e.g. a synced
    MinIO bucket path) is preferred when set so fleet hosts don't hammer the
    index. Raises on failure — callers advertise the environment only after
    this returns.
    """
    interpreter = interpreter_for_hash(hash_)
    if interpreter:
        return interpreter
    if (manifest.get("language") or "").lower() != "python":
        raise ValueError(f"Cannot materialize non-python environment ({manifest.get('language')})")
    root = env_root()
    target = os.path.join(root, hash_)
    os.makedirs(root, exist_ok=True)
    import venv

    venv.create(target, with_pip=True, clear=True)
    interpreter = os.path.join(target, "bin", "python")
    pins = list(manifest.get("pins") or [])
    if pins:
        cmd = [interpreter, "-m", "pip", "install", "--quiet"]
        wheelhouse = os.environ.get("FW_ENV_WHEELHOUSE", "")
        if wheelhouse:
            cmd += ["--no-index", "--find-links", wheelhouse]
        cmd += pins
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Environment {hash_} materialization failed: {proc.stderr.strip()[:500]}"
            )

    # Record the pins beside the venv so a later discovery can verify it without
    # being handed the manifest again (image bakes and lazy materialization take
    # different routes into this function).
    try:
        with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
    except OSError:
        pass

    # ⚠️ INSTALLED IS NOT RUNNABLE. pip succeeding proves the wheels matched this
    # platform tag, not that this CPU can execute them — measured on a 2009 Core
    # 2 Duo where `pip install numpy` succeeds and `import numpy` raises on the
    # x86-64-v2 baseline. Verify before returning, because the caller advertises
    # the hash on the strength of this returning.
    ok, err = smoke_import(interpreter, pins)
    if not ok:
        # Remove the venv rather than leave it on disk: discovery is a filesystem
        # scan, so a failed env left behind is advertised on the next start and
        # the check silently undoes itself.
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(
            f"Environment {hash_} installed but its packages do not import on this "
            f"host: {err}"
        )
    _mark_verified(target)
    return interpreter


def environment_for_decl(program_dict: dict, env_ref: str, ns: str) -> dict | None:
    """Resolve an ``in environment`` reference to its (annotated) decl dict.

    Mirrors the validator's resolution order: qualified name as written,
    else local to ``ns``. (Imported-namespace resolution needs the ``use``
    graph, which the compiled dict preserves per Namespace — checked last.)
    """
    envs: dict[str, dict] = {}
    uses_by_ns: dict[str, list[str]] = {}
    for d in program_dict.get("declarations") or []:
        if isinstance(d, dict) and d.get("type") == "Namespace":
            nsname = d.get("name") or ""
            uses_by_ns[nsname] = list(d.get("uses") or [])
            for inner in d.get("declarations") or []:
                if isinstance(inner, dict) and inner.get("type") == "EnvironmentDecl":
                    envs[f"{nsname}.{inner['name']}"] = inner
    if "." in env_ref and env_ref in envs:
        return envs[env_ref]
    if ns:
        local = envs.get(f"{ns}.{env_ref}")
        if local is not None:
            return local
        for used in uses_by_ns.get(ns, []):
            imported = envs.get(f"{used}.{env_ref}")
            if imported is not None:
                return imported
    return None
