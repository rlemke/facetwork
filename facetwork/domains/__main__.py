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


def main(argv: list[str] | None = None) -> int:
    return run_pkg_cli(
        argv,
        family="domain",
        discover_all=discover_all_domains,
        seed=seed_domain_flows,
        not_found_hint="checked the facetwork.domains entry points and domains/",
    )


if __name__ == "__main__":
    sys.exit(main())
