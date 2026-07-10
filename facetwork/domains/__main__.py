"""CLI: register handlers (and ``--seed`` workflows) for one or more domains.

Usage::

    python -m facetwork.domains [--seed] [NAME ...]

With no names, operates on every discovered domain. Used by ``fw runner start
--domain``, ``fw ffl seed``, and the domain runner container entrypoint.
"""

from __future__ import annotations

import sys

from facetwork._pkg_discovery import run_pkg_cli
from facetwork.domains import discover_all_domains, seed_domain_flows


def _print_namespaces(argv: list[str]) -> int:
    """``--namespaces [NAME ...]``: print the space-separated ``--topics`` globs
    scoping the named domain(s) to their OWN facet namespaces (for the domain
    runner entrypoint). No names → every discovered domain."""
    import os
    from pathlib import Path

    from facetwork._pkg_discovery import filter_packages, package_topic_globs

    names = [a for a in argv if a != "--namespaces"] or None
    repo = Path(os.environ.get("REPO_ROOT", os.getcwd()))
    pkgs = list(filter_packages(discover_all_domains(repo), include=names))
    globs = sorted({g for p in pkgs for g in package_topic_globs(p)})
    print(" ".join(globs))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--namespaces" in args:
        return _print_namespaces(args)
    # --emit-topics: after seeding, print the domain's topic globs on a parseable
    # `FW_TOPICS=...` line so the container entrypoint gets both from ONE process
    # (one interpreter startup) instead of a second `--namespaces` invocation.
    emit_topics = "--emit-topics" in args
    seed_args = [a for a in args if a != "--emit-topics"]
    rc = run_pkg_cli(
        seed_args,
        family="domain",
        discover_all=discover_all_domains,
        seed=seed_domain_flows,
        not_found_hint="checked the facetwork.domains entry points and domains/",
    )
    if emit_topics:
        import os
        from pathlib import Path

        from facetwork._pkg_discovery import filter_packages, package_topic_globs

        names = [a for a in seed_args if a != "--seed"] or None
        repo = Path(os.environ.get("REPO_ROOT", os.getcwd()))
        pkgs = list(filter_packages(discover_all_domains(repo), include=names))
        globs = sorted({g for p in pkgs for g in package_topic_globs(p)})
        print("FW_TOPICS=" + " ".join(globs))
    return rc


if __name__ == "__main__":
    sys.exit(main())
