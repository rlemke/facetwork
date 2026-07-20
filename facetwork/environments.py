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
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
             "--report", "-", *requires],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.warning("Environment resolution failed (pip exit %d): %s",
                           proc.returncode, proc.stderr.strip()[:400])
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
    identity = {"language": manifest.get("language", ""),
                "pins": sorted(manifest.get("pins") or [])}
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
