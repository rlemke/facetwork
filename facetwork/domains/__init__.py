"""Discovery of Facetwork **domain packages** (the pluggable production pipelines).

A domain pipeline ships as a standalone, pip-installable package (``fwh_<domain>``:
fwh_osm, fwh_anthropic, …) declaring the ``facetwork.domains`` entry point, or as an
in-repo directory under ``domains/<name>/``. When the same name appears in both, the
installed package wins. Seeded workflows are stamped ``domain:<name>``.

Teaching demos are a separate family — see :mod:`facetwork.examples`.
"""

from __future__ import annotations

from pathlib import Path

from facetwork._pkg_discovery import (
    Package,
    collect_ffl_files,
    discover_entry_point,
    discover_local,
    filter_packages,
    seed_flows,
)

ENTRY_POINT_GROUP = "facetwork.domains"
# Transitional: during the examples→domains cutover, packages that still declare
# the old group are read as domains too. Removed once every fwh_* repo re-releases.
LEGACY_ENTRY_POINT_GROUP = "facetwork.examples"

# Path stamped onto FlowDefinitions seeded for a domain (one path per domain).
SEED_PATH_PREFIX = "domain:"

# A domain package surfaces as this type; external repos import it from here.
DomainPackage = Package

__all__ = [
    "DomainPackage",
    "ENTRY_POINT_GROUP",
    "SEED_PATH_PREFIX",
    "collect_ffl_files",
    "discover_entry_point_domains",
    "discover_local_domains",
    "discover_all_domains",
    "get_domain",
    "filter_domains",
    "seed_domain_flows",
]


def discover_entry_point_domains() -> list[DomainPackage]:
    """Domains registered via the ``facetwork.domains`` entry point (plus the
    legacy ``facetwork.examples`` group during the cutover)."""
    return discover_entry_point([ENTRY_POINT_GROUP, LEGACY_ENTRY_POINT_GROUP])


def discover_local_domains(repo_root: Path) -> list[DomainPackage]:
    """In-repo domains under ``<repo_root>/domains/<name>/``."""
    return discover_local(repo_root, "domains")


def discover_all_domains(repo_root: Path | None = None) -> list[DomainPackage]:
    """Entry-point + local domains merged by name (entry-point wins)."""
    by_name: dict[str, DomainPackage] = {}
    if repo_root is not None:
        for pkg in discover_local_domains(repo_root):
            by_name[pkg.name] = pkg
    for pkg in discover_entry_point_domains():
        by_name[pkg.name] = pkg
    return sorted(by_name.values(), key=lambda p: p.name)


def get_domain(name: str, repo_root: Path | None = None) -> DomainPackage | None:
    for pkg in discover_all_domains(repo_root):
        if pkg.name == name:
            return pkg
    return None


def filter_domains(domains, *, include: list[str] | None = None):
    return filter_packages(domains, include=include)


def seed_domain_flows(
    pkg: DomainPackage, store, *, replace: bool = True
) -> tuple[int, int, list[str]]:
    return seed_flows(pkg, store, SEED_PATH_PREFIX, replace=replace)
