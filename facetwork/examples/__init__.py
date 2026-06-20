"""Discovery of Facetwork **teaching examples** (in-repo tutorial demos).

Examples live under ``examples/<name>/`` with a ``handlers/`` package exposing
``register_all_registry_handlers(runner)`` and/or an ``ffl/`` directory. They are
in-repo only — there is no entry-point group for teaching examples (the pluggable
production pipelines are :mod:`facetwork.domains`). Seeded workflows are stamped
``example:<name>``.
"""

from __future__ import annotations

from pathlib import Path

from facetwork._pkg_discovery import (
    Package,
    collect_ffl_files,
    discover_local,
    filter_packages,
    seed_flows,
)

# Path stamped onto FlowDefinitions seeded for a teaching example.
SEED_PATH_PREFIX = "example:"

# A teaching example surfaces as this type.
ExamplePackage = Package

__all__ = [
    "ExamplePackage",
    "SEED_PATH_PREFIX",
    "collect_ffl_files",
    "discover_local_examples",
    "discover_all_examples",
    "get_example",
    "filter_examples",
    "seed_example_flows",
]


def discover_local_examples(repo_root: Path) -> list[ExamplePackage]:
    """In-repo teaching examples under ``<repo_root>/examples/<name>/``."""
    return discover_local(repo_root, "examples")


def discover_all_examples(repo_root: Path | None = None) -> list[ExamplePackage]:
    """All teaching examples (in-repo only). ``repo_root=None`` yields none."""
    if repo_root is None:
        return []
    return sorted(discover_local_examples(repo_root), key=lambda p: p.name)


def get_example(name: str, repo_root: Path | None = None) -> ExamplePackage | None:
    for pkg in discover_all_examples(repo_root):
        if pkg.name == name:
            return pkg
    return None


def filter_examples(examples, *, include: list[str] | None = None):
    return filter_packages(examples, include=include)


def seed_example_flows(
    pkg: ExamplePackage, store, *, replace: bool = True
) -> tuple[int, int, list[str]]:
    return seed_flows(pkg, store, SEED_PATH_PREFIX, replace=replace)
