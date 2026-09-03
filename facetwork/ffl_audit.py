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

"""Audit every domain repo against the CURRENT runtime — ``fw util ffl-audit``.

Domain packages live in their own repos and are deployed baked into an image,
so nothing forces them to keep up when a language rule or a handler contract
changes. They keep compiling, keep deploying, and fail only when someone
finally runs the workflow. This sweeps for that drift on purpose instead of
discovering it a release later.

Two checks, because the drift that actually bit this fleet was NOT in the FFL:

1. **FFL validation** — every ``.ffl`` in a repo compiled against the current
   compiler. Catches rules that shipped after the FFL was written (relative
   ``$``-scoping, the ``after`` clause, ``yield`` aggregation).

2. **Handler contracts** — obsolete runtime APIs in handler Python. Currently
   one: ``params["_step_log"]`` is a CALLBACK, ``_step_log(msg, level=…)``, and
   handlers written against the older list contract call ``.append({…})``. That
   is not cosmetic — it kills the handler at its first log line, and a domain's
   own tests do NOT catch it (they pass ``params`` without ``_step_log``, so the
   branch never runs). One domain had all six handlers dead this way with 48
   tests green.

**Choosing the library set is the whole difficulty of check 1**, and both
obvious answers are wrong:

- Validate each repo ALONE → a pure-FFL catalog over another domain
  (``fwh_osm_lz`` uses ``osm.types`` from ``fwh_osm``) reports ~94 phantom
  "namespace does not exist" errors.
- Supply EVERY other repo → short names collide across domains ("Ambiguous
  facet reference 'RetryPolicy': monitor.mixins / s2.mixins / save_earth.mixins
  / weather.mixins"), which is ~92 phantom errors on nearly every repo.

So the set is DERIVED per repo (:func:`dependencies_for`): supply only repos
providing a namespace this one *uses* but does not *define*. Self-maintaining,
and the audit prints what it supplied so the scoping is visible rather than
implicit. This matters more than it sounds — a sweep that cries wolf on the
same repo every run teaches you to skim its output, which is how a dead domain
stays undetected.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field

NS_DEF = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
NS_USE = re.compile(r"^\s*use\s+([\w.]+)", re.M)

#: Handler-contract checks: (label, regex, why it is wrong).
CONTRACT_CHECKS = [
    (
        "_step_log-as-list",
        # ⚠️ `\bstep_log` MISSED the spelling this check is named after.
        # `_` is a word character, so there is no word boundary between the
        # underscore and the `s` - `_step_log.append(` did not match, while the
        # bare `step_log.append(` did. The documented failure (six handlers dead
        # in fwh_sensor_monitoring with 48 tests green) is written `_step_log`,
        # so the check was blind to its own motivating case.
        # The lookbehind excludes `_` as well as alphanumerics, so an unrelated
        # `my_step_log.append(` on a genuine list is not flagged; the second arm
        # catches the subscript form, `params["_step_log"].append(`.
        re.compile(r"""(?:(?<![A-Za-z0-9_])_?step_log|\[\s*["']_step_log["']\s*\])"""
                   r"""\s*\.\s*append\s*\("""),
        "`_step_log` is a callback — step_log(message, level=…), not a list. "
        "`.append` raises AttributeError at the handler's first log line.",
    ),
]

DEFAULT_ROOT = os.environ.get("FWH_HANDLERS_ROOT") or str(pathlib.Path.home() / "fw_handlers")


@dataclass
class RepoResult:
    name: str
    ffl_count: int
    status: str  # "ok" | "drift" | "no-ffl"
    errors: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    contract_hits: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "drift" or bool(self.contract_hits)


# Directories that hold COPIES of a repo's own sources. Reading them makes the
# audit see the same namespace twice and report every reference to it as
# ambiguous - against two candidates with the SAME fully-qualified name, which is
# the tell. Measured: one `python -m build --wheel` in fwh_save_earth left a
# build/lib/ copy and produced 218 phantom "Ambiguous facet reference
# 'RetryPolicy': could be save_earth.mixins.RetryPolicy,
# save_earth.mixins.RetryPolicy" errors, which is enough noise to bury a real
# finding in the tier this audit exists to gate.
_COPY_DIRS = frozenset({
    ".venv", "venv", "site-packages", "build", "dist", "sdist",
    ".eggs", ".tox", "__pycache__", "node_modules", ".git",
})


def _is_vendored(path: pathlib.Path) -> bool:
    """True for a path inside a build artifact or vendored dependency tree."""
    return any(part in _COPY_DIRS or part.endswith(".egg-info") for part in path.parts)


def ffl_files(repo: pathlib.Path) -> list[pathlib.Path]:
    """Every ``.ffl`` in a repo, excluding build artifacts and vendored trees."""
    return sorted(f for f in repo.rglob("*.ffl") if not _is_vendored(f))


def scan_namespaces(files: list[pathlib.Path]) -> tuple[set[str], set[str]]:
    """Return (defined, used) namespaces across ``files``."""
    defined: set[str] = set()
    used: set[str] = set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        defined.update(NS_DEF.findall(text))
        used.update(NS_USE.findall(text))
    return defined, used


def dependencies_for(repo: str, defines: dict[str, set[str]], uses: dict[str, set[str]]) -> list[str]:
    """Repos whose namespaces ``repo`` uses but does not define.

    Prefix-aware in both directions: ``use osm.types`` is satisfied by a repo
    defining ``osm.types`` exactly, or a deeper namespace beneath it, or a
    shallower one it nests under — FFL namespaces are hierarchical and a
    catalog repo may `use` at a different depth than the provider declares.
    """
    needed = uses.get(repo, set()) - defines.get(repo, set())
    out: set[str] = set()
    for ns in needed:
        for owner, owned in defines.items():
            if owner == repo:
                continue
            if ns in owned or any(
                o.startswith(ns + ".") or ns.startswith(o + ".") for o in owned
            ):
                out.add(owner)
    return sorted(out)


def scan_contracts(repo: pathlib.Path) -> list[str]:
    """Obsolete runtime APIs in this repo's handler Python.

    Tests are excluded: a test may legitimately construct the old shape to
    prove it is rejected, and flagging that would punish the regression test
    written to prevent exactly this drift.
    """
    hits: list[str] = []
    for py in sorted(repo.rglob("*.py")):
        parts = py.parts
        # Same exclusion set as the FFL scan: the two used to differ, so a build
        # artifact was invisible to one scan and poisoned the other.
        if _is_vendored(py):
            continue
        if any(p in ("tests", "test") for p in parts):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern, _why in CONTRACT_CHECKS:
            for n, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    rel = py.relative_to(repo)
                    hits.append(f"{label}  {rel}:{n}  {line.strip()[:80]}")
    return hits


def audit(roots: list[str], *, check_contracts: bool = True,
          python: str | None = None, cwd: str | None = None,
          repo_paths: "list[str] | None" = None) -> list[RepoResult]:
    """Validate every ``fwh_*`` repo found under ``roots``.

    ``repo_paths`` names repositories DIRECTLY instead of sweeping a parent for
    ``fwh_*`` children. CI checks a domain out on its own, with no siblings and
    often not even under an ``fwh_``-prefixed directory, so a sweep finds
    nothing -- and an audit that finds no work reports success. Only the FIRST
    entry is audited; the rest are supplied as libraries, which is what
    fwh_osm_lz needs (it uses the ``osm`` namespace and defines none of it).
    Measured 2026-09-03: 1 of 29 domains needs that.
    """
    repos: list[pathlib.Path] = []
    audit_only: set[str] = set()
    # ⚠️ resolve()d. The compiler subprocess runs with cwd set to the FACETWORK
    # root, so a relative --repo resolves against the wrong directory and every
    # .ffl reports "File not found" while the glob that found them succeeded.
    # Invisible locally, where an absolute path is natural to type; it failed on
    # the first CI run.
    for direct in [pathlib.Path(x).resolve() for x in (repo_paths or [])]:
        if direct.is_dir():
            repos.append(direct)
    if repos:
        audit_only = {repos[0].name}
    for root in roots:
        rp = pathlib.Path(root)
        if not rp.is_dir():
            continue
        repos += sorted(p for p in rp.iterdir() if p.is_dir() and p.name.startswith("fwh_"))

    files = {r.name: ffl_files(r) for r in repos}
    by_name = {r.name: r for r in repos}
    defines: dict[str, set[str]] = {}
    uses: dict[str, set[str]] = {}
    for r in repos:
        defines[r.name], uses[r.name] = scan_namespaces(files[r.name])

    py = python or sys.executable
    results: list[RepoResult] = []
    for r in repos:
        if audit_only and r.name not in audit_only:
            continue   # supplied as a library only
        own = files[r.name]
        contract_hits = scan_contracts(r) if check_contracts else []
        if not own:
            results.append(RepoResult(r.name, 0, "no-ffl", contract_hits=contract_hits))
            continue

        deps = dependencies_for(r.name, defines, uses)
        cmd = [py, "-m", "facetwork.cli", "compile", "--check"]
        for f in own:
            cmd += ["--primary", str(f)]
        own_paths = {str(f) for f in own}
        for d in deps:
            for f in files[by_name[d].name]:
                if str(f) not in own_paths:
                    cmd += ["--library", str(f)]

        # A subprocess per repo, deliberately: one repo with a pathological
        # source cannot take the whole audit down with it, and the compiler's
        # own diagnostic formatting is what gets reported.
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        out = (proc.stdout or "") + (proc.stderr or "")
        errs = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("Error:")]
        status = "ok" if proc.returncode == 0 and not errs else "drift"
        results.append(RepoResult(r.name, len(own), status, errs,
                                  [d.replace("fwh_", "") for d in deps], contract_hits))
    return results


def _report(results: list[RepoResult], *, quiet: bool) -> None:
    print(f"{'repo':<26} {'ffl':>4}  {'status':<7} deps supplied")
    print("-" * 72)
    for r in results:
        mark = {"ok": "ok", "drift": "DRIFT", "no-ffl": "-"}[r.status]
        if r.contract_hits and mark == "ok":
            mark = "CONTRACT"
        extra = f"   ({len(r.errors)} errors)" if r.errors else ""
        if r.contract_hits:
            extra += f"   [{len(r.contract_hits)} obsolete API]"
        print(f"{r.name:<26} {r.ffl_count:>4}  {mark:<7} {', '.join(r.deps)}{extra}")

    failures = [r for r in results if r.failed]
    print()
    if not failures:
        print("ALL CLEAN — every domain validates against the current compiler "
              "and uses no obsolete handler APIs.")
        return
    for r in failures:
        if r.errors:
            print(f"=== {r.name} — {len(r.errors)} FFL error(s)")
            for e in r.errors[: (3 if quiet else 8)]:
                print("   ", e[len("Error:"):].strip()[:150])
            if len(r.errors) > (3 if quiet else 8):
                print(f"    … {len(r.errors) - (3 if quiet else 8)} more")
        for hit in r.contract_hits:
            print(f"=== {r.name} — obsolete handler API")
            print("   ", hit)
        print()
    for label, _pat, why in CONTRACT_CHECKS:
        if any(label in h for r in results for h in r.contract_hits):
            print(f"note ({label}): {why}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fw util ffl-audit",
        description="Validate every domain repo's FFL and handler contracts "
                    "against the current runtime.",
    )
    p.add_argument("--root", action="append", default=[],
                   help=f"directory holding fwh_* repos (repeatable; default {DEFAULT_ROOT}). "
                        "Use --root /opt inside a runner container to audit the BAKED "
                        "domains, which is what the fleet actually executes.")
    p.add_argument("--repo", action="append", default=[],
                   help="audit THIS repository directly instead of sweeping a parent "
                        "for fwh_* children. Use in CI, where a domain is checked out "
                        "alone: a sweep finds no fwh_* dirs, and an audit that finds no "
                        "work reports success.")
    p.add_argument("--lib-repo", action="append", default=[],
                   help="supply another checkout as a LIBRARY (repeatable). Needed only "
                        "where a domain uses a namespace it does not define - 1 of 29 "
                        "(fwh_osm_lz needs fwh_osm).")
    p.add_argument("--no-contracts", action="store_true",
                   help="skip the obsolete-handler-API scan (FFL validation only)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("-q", "--quiet", action="store_true", help="fewer error lines per repo")
    a = p.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if a.repo:
        roots, paths = a.root, list(a.repo) + list(a.lib_repo)
    else:
        roots, paths = (a.root or [DEFAULT_ROOT]), None
    results = audit(roots, check_contracts=not a.no_contracts, cwd=str(repo_root),
                    repo_paths=paths)

    if not results:
        where = ", ".join(a.repo) if a.repo else ", ".join(roots)
        print(f"no repo to audit at: {where}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps([{
            "repo": r.name, "ffl": r.ffl_count, "status": r.status,
            "deps": r.deps, "errors": r.errors, "contract_hits": r.contract_hits,
        } for r in results], indent=2))
    else:
        _report(results, quiet=a.quiet)

    # Non-zero on any drift so this is usable as a CI guard.
    return 1 if any(r.failed for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
