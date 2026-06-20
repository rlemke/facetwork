"""Sync per-domain compose ENV from the catalog into docker-compose.full-stack.yml.

The structural parts of each ``runner-<service>:`` block (build, depends_on,
volumes, the ``<<: *anchor`` storage profiles) are hand-written templates and are
left untouched. Only the catalog-derived ENV keys are (re)written in place:

    AFL_DOMAIN_NAME, AFL_DOMAIN_REPO, AFL_REGISTRY_RUNNER_ARGS,
    AFL_DOMAIN_EXTRAS (only for domains that declare ``compose_extras``)

So editing ``domains.json`` (task_list, repo, …) and running ``fw util gen-compose``
keeps the compose env in sync — no hand-editing, no second source of truth. A
brand-new domain still needs its service block added once (copy a template); the
generator then fills its env. ``--check`` writes nothing and exits non-zero if the
file is out of sync (CI guard).

Keys are only *replaced where they already exist* in a service block — never
inserted — so the generator can't corrupt structure; a missing key is reported.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from facetwork.domains.catalog import compose_domains

_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = _REPO_ROOT / "docker-compose.full-stack.yml"

_SERVICE_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def _registry_args(spec: dict) -> str:
    args = f"--task-list {spec['task_list']}"
    if spec.get("server_group_arg"):
        args += " --server-group ${AFL_SERVER_GROUP:-default}"
    return args


def _extras_value(spec: dict) -> str | None:
    ce = spec.get("compose_extras")
    if not ce:
        return None
    var = spec.get("extras_var")
    return f"${{{var}:-{ce}}}" if var else ce


def _managed_values(name: str, spec: dict) -> dict[str, str]:
    """key -> the value text (right of 'KEY: ') the generator wants."""
    vals = {
        "AFL_DOMAIN_NAME": name,
        "AFL_DOMAIN_REPO": spec["repo"],
        "AFL_REGISTRY_RUNNER_ARGS": f'"{_registry_args(spec)}"',
    }
    ev = _extras_value(spec)
    if ev is not None:
        vals["AFL_DOMAIN_EXTRAS"] = ev
    return vals


def _service_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """service name -> (start, end) line indices (end exclusive)."""
    starts = [(i, m.group(1)) for i, line in enumerate(lines)
              if (m := _SERVICE_RE.match(line))]
    blocks = {}
    for k, (i, n) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        blocks[n] = (i, end)
    return blocks


def sync(*, write: bool) -> tuple[list[str], list[str]]:
    """Return (changes, warnings). When write=True, rewrite COMPOSE in place."""
    lines = COMPOSE.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks = _service_blocks(lines)
    changes: list[str] = []
    warnings: list[str] = []

    for name, spec in sorted(compose_domains().items()):
        svc = spec.get("service")
        if svc not in blocks:
            warnings.append(f"{name}: no compose service '{svc}' — add its block (template) first")
            continue
        start, end = blocks[svc]
        wanted = _managed_values(name, spec)
        for key, value in wanted.items():
            kre = re.compile(rf"^(\s*){re.escape(key)}:\s*(.*?)\s*$")
            found = False
            for i in range(start, end):
                m = kre.match(lines[i])
                if not m:
                    continue
                found = True
                new_line = f"{m.group(1)}{key}: {value}\n"
                if lines[i] != new_line:
                    changes.append(f"{svc}: {key}: {m.group(2)!r} -> {value!r}")
                    lines[i] = new_line
                break
            if not found:
                warnings.append(f"{svc}: key {key} not present (skipped; add it to the template)")

    if write and changes:
        COMPOSE.write_text("".join(lines), encoding="utf-8")
    return changes, warnings


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    changes, warnings = sync(write=not check)
    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if check:
        if changes:
            print(f"docker-compose.full-stack.yml is OUT OF SYNC with the catalog ({len(changes)} change(s)):")
            for c in changes:
                print(f"  {c}")
            print("Run `fw util gen-compose` to sync.")
            return 1
        print("docker-compose.full-stack.yml is in sync with the catalog.")
        return 0
    if changes:
        print(f"Synced {len(changes)} env value(s) from the catalog into docker-compose.full-stack.yml:")
        for c in changes:
            print(f"  {c}")
    else:
        print("Already in sync — no changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
