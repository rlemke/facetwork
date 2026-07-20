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

"""Bake-time environment materialization (script-environments.md §4.1).

Sweeps FFL sources for ``environment`` declarations, freezes each into its
pinned manifest, and materializes the python ones as venvs under
``FW_ENV_ROOT`` (default ``/opt/fw_envs``) — so every runner started from the
image *provides* the fleet's declared environments from second one, and no
runner ever pip-installs per task or per start (the bake-all-domains lesson).

Run as the image-build step after the domain repos are baked::

    python -m facetwork.envbake --root /opt --root /app/examples

Files that fail to parse are skipped with a warning (a domain repo may carry
work-in-progress FFL); an environment that fails to MATERIALIZE fails the
build — a half-provided image would advertise nothing for it and silently
strand its tasks, which is exactly the class of quiet gap this feature exists
to remove. ``--check`` lists what would be built without building.

Foreign-language environments are listed and skipped (provided-only, by
out-of-band agents). Deterministic manifests make this idempotent: an
existing venv for a hash is left untouched.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import parse
from .emitter import JSONEmitter
from .environments import (
    env_root,
    freeze_environment,
    interpreter_for_hash,
    manifest_hash,
    materialize_environment,
)

logger = logging.getLogger(__name__)


def collect_environments(roots: list[str]) -> dict[str, dict]:
    """Sweep ``roots`` for ``*.ffl`` and collect environment declarations.

    Returns ``{qualified_name: {"manifest": …, "hash": …, "source": path}}``.
    Duplicate qualified names with the SAME manifest hash collapse; with a
    DIFFERENT hash the conflict is reported and the build fails — two flows
    disagreeing about what ``geo.PyGeo`` means is an authoring error to fix,
    not to pick a winner for.
    """
    import json

    emitter = JSONEmitter(include_locations=False)
    found: dict[str, dict] = {}
    conflicts: list[str] = []
    for root in roots:
        base = Path(root)
        if not base.exists():
            continue
        for ffl in sorted(base.rglob("*.ffl")):
            try:
                program = json.loads(emitter.emit(parse(ffl.read_text())))
            except Exception as exc:  # noqa: BLE001 - WIP FFL must not break the sweep
                logger.warning("envbake: skipping unparsable %s: %s", ffl, str(exc)[:120])
                continue
            for decl in program.get("declarations") or []:
                if not isinstance(decl, dict) or decl.get("type") != "Namespace":
                    continue
                ns = decl.get("name") or ""
                for inner in decl.get("declarations") or []:
                    if not isinstance(inner, dict) or inner.get("type") != "EnvironmentDecl":
                        continue
                    qualified = f"{ns}.{inner['name']}"
                    manifest = freeze_environment(inner)
                    h = manifest_hash(manifest)
                    prior = found.get(qualified)
                    if prior and prior["hash"] != h:
                        conflicts.append(
                            f"{qualified}: {prior['source']} ({prior['hash']}) vs {ffl} ({h})"
                        )
                        continue
                    found[qualified] = {"manifest": manifest, "hash": h, "source": str(ffl)}
    if conflicts:
        raise SystemExit(
            "envbake: conflicting environment declarations:\n  " + "\n  ".join(conflicts)
        )
    return found


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        prog="python -m facetwork.envbake",
        description="Materialize declared script environments into FW_ENV_ROOT",
    )
    ap.add_argument("--root", action="append", default=[],
                    help="Directory to sweep for *.ffl (repeatable)")
    ap.add_argument("--check", action="store_true",
                    help="List environments without materializing")
    args = ap.parse_args(argv)
    roots = args.root or ["/opt", "/app/examples"]

    envs = collect_environments(roots)
    if not envs:
        print(f"envbake: no environment declarations under {roots}")
        return 0

    failures = 0
    for qualified, info in sorted(envs.items()):
        manifest, h = info["manifest"], info["hash"]
        lang = manifest.get("language") or "?"
        pins = manifest.get("pins") or []
        if lang != "python":
            print(f"envbake: SKIP {qualified}@{h} language={lang} (provided-only)")
            continue
        if not manifest.get("resolved", True):
            print(f"envbake: FAIL {qualified}@{h} — manifest unresolved "
                  f"(loose specs and resolution unavailable): {manifest.get('requires')}")
            failures += 1
            continue
        if args.check:
            state = "present" if interpreter_for_hash(h) else "to build"
            print(f"envbake: {qualified}@{h} [{state}] pins={pins}")
            continue
        try:
            interpreter = materialize_environment(manifest, h)
            print(f"envbake: OK {qualified}@{h} -> {interpreter} ({len(pins)} pin(s))")
        except Exception as exc:  # noqa: BLE001 - report, then fail the build
            print(f"envbake: FAIL {qualified}@{h}: {exc}", file=sys.stderr)
            failures += 1
    if failures:
        print(f"envbake: {failures} environment(s) failed — failing the build "
              f"(a half-provided image silently strands env-tagged tasks)",
              file=sys.stderr)
        return 1
    print(f"envbake: {len(envs)} environment(s) processed into {env_root()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
