"""CLI: register handlers (and ``--seed`` workflows) for one or more teaching examples.

Usage::

    python -m facetwork.examples [--seed] [NAME ...]

With no names, operates on every in-repo example under ``examples/``. Used by
``fw runner start --example`` and ``fw ffl seed``.
"""

from __future__ import annotations

import sys

from facetwork._pkg_discovery import run_pkg_cli
from facetwork.examples import discover_all_examples, seed_example_flows


def main(argv: list[str] | None = None) -> int:
    return run_pkg_cli(
        argv,
        family="example",
        discover_all=discover_all_examples,
        seed=seed_example_flows,
        not_found_hint="checked examples/",
    )


if __name__ == "__main__":
    sys.exit(main())
