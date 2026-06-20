"""Shared discovery + seeding engine for Facetwork package families.

Two families wrap this neutral engine:

* **domains** (:mod:`facetwork.domains`) — pluggable production pipelines, discovered
  from the ``facetwork.domains`` entry-point group and an in-repo ``domains/`` dir,
  seeded under the ``domain:`` path prefix.
* **examples** (:mod:`facetwork.examples`) — in-repo teaching demos under ``examples/``,
  seeded under the ``example:`` prefix (no entry-point packages).

Everything name-bearing (group name, local subdir, seed prefix) is a parameter here;
the two wrapper modules supply theirs. ``Package`` is the uniform shape; the wrappers
re-export it as ``DomainPackage`` / ``ExamplePackage``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Package:
    """A discoverable Facetwork package (a domain pipeline or a teaching example).

    Both installed entry-point packages and in-repo directories surface as this
    type so callers treat them uniformly.
    """

    name: str
    ffl_dir: Path | None
    register_handlers: Callable[[Any], None]
    runner_env: dict[str, str] = field(default_factory=dict)
    source: str = "entry_point"  # "entry_point" | "local"
    handlers_path: Path | None = None  # set for "local"; needed for sys.path
    extra_ffl_dirs: list[Path] = field(default_factory=list)
    # Extra directories to walk for *.ffl, in addition to ffl_dir and the
    # default root scan. Used by entry-point packages that ship FFL fixtures
    # outside their installed src/ tree (e.g. tests/real/ffl/ at the repo root).


def _load_entry_point(ep: importlib.metadata.EntryPoint) -> Package | None:
    try:
        obj = ep.load()
    except Exception as e:
        logger.warning("Failed to load entry point %s: %s", ep.name, e)
        return None
    # Duck-typed: a package object from any wrapper (DomainPackage/ExamplePackage)
    # is a Package alias, so isinstance accepts it. This also tolerates a package
    # still declaring the legacy group during the transition.
    if not isinstance(obj, Package):
        logger.warning(
            "Entry point %s did not resolve to a Package (got %s); skipping",
            ep.name,
            type(obj).__name__,
        )
        return None
    return obj


def discover_entry_point(groups: Iterable[str]) -> list[Package]:
    """Return packages registered under any of ``groups`` (deduped by name).

    Accepting multiple groups is what makes the domains/examples cutover safe: a
    host with a package still on the legacy group is still surfaced.
    """
    by_name: dict[str, Package] = {}
    for group in groups:
        for ep in importlib.metadata.entry_points(group=group):
            if ep.name in by_name:
                continue
            pkg = _load_entry_point(ep)
            if pkg is not None:
                by_name[ep.name] = pkg
    return list(by_name.values())


def _parse_runner_env(path: Path) -> dict[str, str]:
    """Parse a shell-style KEY=VALUE file (strips a trailing ``# comment``)."""
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.lstrip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            split_at = value.find(" #")
            if split_at != -1:
                value = value[:split_at]
            value = value.rstrip()
        if key:
            out[key] = value
    return out


def _make_local_register(pkg_dir: Path) -> Callable[[Any], None]:
    """Closure that imports ``handlers`` from ``pkg_dir`` and runs its
    ``register_all_registry_handlers(runner)``. Re-imports cleanly so multiple
    local packages can share the package name ``handlers`` without collision."""

    def register(runner: Any) -> None:
        path_str = str(pkg_dir)
        added = False
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
            added = True
        try:
            for key in [k for k in sys.modules if k == "handlers" or k.startswith("handlers.")]:
                del sys.modules[key]
            module = importlib.import_module("handlers")
            module.register_all_registry_handlers(runner)
        finally:
            if added and path_str in sys.path:
                sys.path.remove(path_str)

    return register


def discover_local(repo_root: Path, subdir: str) -> list[Package]:
    """Scan ``<repo_root>/<subdir>/<name>/`` for in-repo package directories.

    A directory qualifies if it has ``handlers/__init__.py`` or an ``ffl/`` dir.
    """
    base = repo_root / subdir
    if not base.is_dir():
        return []

    found: list[Package] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        handlers_init = entry / "handlers" / "__init__.py"
        ffl_dir = entry / "ffl"
        has_handlers = handlers_init.is_file()
        has_ffl = ffl_dir.is_dir()
        if not has_handlers and not has_ffl:
            continue

        runner_env_path = entry / "runner.env"
        runner_env = _parse_runner_env(runner_env_path) if runner_env_path.is_file() else {}
        register = _make_local_register(entry) if has_handlers else (lambda _r: None)

        found.append(
            Package(
                name=entry.name,
                ffl_dir=ffl_dir if has_ffl else None,
                register_handlers=register,
                runner_env=runner_env,
                source="local",
                handlers_path=entry if has_handlers else None,
            )
        )
    return found


def filter_packages(
    packages: Iterable[Package], *, include: list[str] | None = None
) -> Iterator[Package]:
    """Yield only packages whose names are in ``include`` (no filter if None)."""
    if include is None:
        yield from packages
        return
    wanted = set(include)
    for pkg in packages:
        if pkg.name in wanted:
            yield pkg


def _is_excluded_test_fixture(path: Path) -> bool:
    """Skip files under ``tests/`` unless they're under ``tests/real/``."""
    parts = path.parts
    if "tests" not in parts:
        return False
    i = parts.index("tests")
    return not (i + 1 < len(parts) and parts[i + 1] == "real")


def collect_ffl_files(pkg: Package) -> list[Path]:
    """Return all .ffl files for ``pkg``, deduped and sorted.

    Walks: (1) ``pkg.ffl_dir`` (rglob), (2) ``*/ffl/*.ffl`` under the package root
    (the per-domain convention where FFL lives alongside handlers), (3) each
    ``pkg.extra_ffl_dirs``. Test fixtures are excluded except under ``tests/real/``.
    """
    files: list[Path] = []

    if pkg.handlers_path is not None:
        root = pkg.handlers_path
    elif pkg.ffl_dir is not None:
        root = pkg.ffl_dir.parent
    else:
        root = None

    if pkg.ffl_dir is not None and pkg.ffl_dir.is_dir():
        files.extend(sorted(pkg.ffl_dir.rglob("*.ffl")))

    if root is not None:
        real_root = root.resolve()
        for f in sorted(root.rglob("ffl/*.ffl")):
            try:
                f.resolve().relative_to(real_root)
            except ValueError:
                continue
            files.append(f)

    for extra in pkg.extra_ffl_dirs:
        if extra.is_dir():
            files.extend(sorted(extra.rglob("*.ffl")))

    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        real_f = f.resolve()
        if _is_excluded_test_fixture(real_f):
            continue
        if real_f in seen:
            continue
        seen.add(real_f)
        unique.append(f)
    return unique


# ---------------------------------------------------------------------------
# Seeding (FlowDefinition + WorkflowDefinition)
# ---------------------------------------------------------------------------


def _collect_workflow_names(node: dict, prefix: str = "") -> list[str]:
    """Collect fully-qualified workflow names from a compiled FFL program dict
    (handles both the nested and the flat ``declarations`` emitter shapes)."""
    names: list[str] = []
    for w in node.get("workflows", []):
        names.append(f"{prefix}{w['name']}" if prefix else w["name"])
    for decl in node.get("declarations", []):
        if decl.get("type") == "WorkflowDecl":
            names.append(f"{prefix}{decl['name']}" if prefix else decl["name"])
        elif decl.get("type") == "Namespace":
            names.extend(_collect_workflow_names(decl, f"{prefix}{decl['name']}."))
    for ns in node.get("namespaces", []):
        names.extend(_collect_workflow_names(ns, f"{prefix}{ns['name']}."))
    return names


def _compile_ffl_files(files: list[Path]) -> tuple[dict, str, list[str]]:
    """Parse + merge + emit a list of ``.ffl`` files.

    Returns ``(program_dict, combined_source, warnings)``. Parse errors raise;
    validation problems become warnings (a package may legitimately reference
    facets defined by another).
    """
    from facetwork.ast import Program
    from facetwork.emitter import JSONEmitter
    from facetwork.parser import FFLParser
    from facetwork.validator import validate

    parser = FFLParser()
    programs = []
    parts: list[str] = []
    for path in files:
        text = Path(path).read_text()
        parts.append(text)
        programs.append(parser.parse(text, filename=str(path)))
    merged = Program.merge(programs)

    warnings: list[str] = []
    result = validate(merged)
    if not result.is_valid:
        warnings.append(f"{len(result.errors)} validation warning(s)")

    program_dict = json.loads(JSONEmitter(include_locations=False).emit(merged))
    return program_dict, "\n".join(parts), warnings


def seed_flows(
    pkg: Package, store: Any, seed_prefix: str, *, replace: bool = True
) -> tuple[int, int, list[str]]:
    """Compile ``pkg``'s FFL and upsert a FlowDefinition + its WorkflowDefinitions.

    Each package is seeded under ``<seed_prefix><name>``; when ``replace`` is set
    (default) flows/workflows previously seeded under that path are reconciled in
    place (reusing UUIDs), so re-running — e.g. every runner restart — is
    idempotent. Returns ``(flows_seeded, workflows_seeded, warnings)``.

    Stable UUIDs across re-seeds matter for liveness: an in-flight
    ``fw:execute:<Workflow>`` bootstrap task references a flow_id + workflow_id;
    regenerating them on a restart re-seed would orphan it (thesis §5.6).
    """
    from facetwork.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from facetwork.runtime.types import generate_id

    files = collect_ffl_files(pkg)
    if not files:
        return 0, 0, []

    program_dict, combined_source, warnings = _compile_ffl_files(files)
    workflow_names = _collect_workflow_names(program_dict)
    if not workflow_names:
        return 0, 0, warnings

    seed_path = f"{seed_prefix}{pkg.name}"

    flow_id: str = ""
    wf_ids: dict[str, str] = {}
    db = getattr(store, "_db", None)
    if db is not None:
        existing_flow = db.flows.find_one({"name.path": seed_path}, {"uuid": 1})
        if existing_flow:
            flow_id = existing_flow["uuid"]
            for doc in db.workflows.find(
                {"flow_id": flow_id}, {"uuid": 1, "name": 1, "_id": 0}
            ):
                n = doc.get("name")
                if isinstance(n, dict):
                    n = n.get("name", "")
                if n:
                    wf_ids[n] = doc["uuid"]
            stale = [n for n in wf_ids if n not in workflow_names]
            if stale:
                db.workflows.delete_many({"flow_id": flow_id, "name": {"$in": stale}})
                for n in stale:
                    wf_ids.pop(n, None)

    if not flow_id:
        flow_id = generate_id()

    now_ms = int(time.time() * 1000)
    store.save_flow(
        FlowDefinition(
            uuid=flow_id,
            name=FlowIdentity(name=pkg.name, path=seed_path, uuid=flow_id),
            compiled_sources=[SourceText(name="source.ffl", content=combined_source)],
            compiled_ast=program_dict,
        )
    )
    for qname in workflow_names:
        wf_id = wf_ids.get(qname) or generate_id()
        store.save_workflow(
            WorkflowDefinition(
                uuid=wf_id,
                name=qname,
                namespace_id=seed_path,
                facet_id=wf_id,
                flow_id=flow_id,
                starting_step="",
                version="1.0",
                date=now_ms,
            )
        )
    return 1, len(workflow_names), warnings


# ---------------------------------------------------------------------------
# Shared register/seed CLI (used by `python -m facetwork.domains|examples`)
# ---------------------------------------------------------------------------


def run_pkg_cli(
    argv: list[str] | None,
    *,
    family: str,
    discover_all: Callable[[Path], list[Package]],
    seed: Callable[[Package, Any], tuple[int, int, list[str]]],
    not_found_hint: str,
) -> int:
    """Register handlers (and with ``--seed``, seed workflows) for a package family.

    ``family`` is the human label ("domain"/"example"); ``discover_all(repo_root)``
    returns the family's packages; ``seed(pkg, store)`` seeds one. Mirrors the old
    ``facetwork.examples.__main__`` exactly, parametrized on the family.
    """
    import os
    import sys

    from facetwork.config import MongoDBConfig
    from facetwork.runtime import Evaluator, RegistryRunner, Telemetry
    from facetwork.runtime.mongo_store import MongoStore

    argv = list(sys.argv[1:] if argv is None else argv)
    do_seed = "--seed" in argv
    selected = [a for a in argv if a != "--seed"] or None

    repo_root = Path(os.environ.get("REPO_ROOT", os.getcwd()))
    packages = list(filter_packages(discover_all(repo_root), include=selected))

    if not packages:
        print(f"No {family}s found ({not_found_hint}).", file=sys.stderr)
        return 1

    cfg = MongoDBConfig.from_env()
    store = MongoStore(cfg.url, database_name=cfg.database)
    runner = RegistryRunner(store, Evaluator(store, Telemetry()))

    print("Registering handlers (upsert)...")
    print()
    total = 0
    for pkg in packages:
        if pkg.handlers_path is None and pkg.source == "local":
            print(f"  {pkg.name}: SKIP (no handlers/)")
            continue
        before = store._db.handler_registrations.count_documents({})
        try:
            pkg.register_handlers(runner)
        except Exception as e:
            print(f"  {pkg.name}: ERROR — {e}")
            continue
        count = store._db.handler_registrations.count_documents({}) - before
        total += count
        print(f"  {pkg.name}: {count} handlers registered  [{pkg.source}]")

    print()
    print(f"Total: {total} handlers registered")

    if do_seed:
        print()
        print(f"Seeding {family} workflows (FlowDefinition + WorkflowDefinition)...")
        print()
        seeded_flows = 0
        seeded_workflows = 0
        for pkg in packages:
            try:
                n_flows, n_workflows, warnings = seed(pkg, store)
            except Exception as e:
                print(f"  {pkg.name}: seed ERROR — {e}")
                continue
            if n_workflows == 0:
                print(f"  {pkg.name}: no workflows to seed")
                continue
            seeded_flows += n_flows
            seeded_workflows += n_workflows
            line = f"  {pkg.name}: {n_workflows} workflow(s) seeded"
            if warnings:
                line += f"  [{', '.join(warnings)}]"
            print(line)
        print()
        print(f"Seeded {seeded_flows} flow(s) with {seeded_workflows} workflow(s)")

    store.close()
    return 0
