#!/usr/bin/env python3
"""DownloadNuclearReactors, invoked as a plain CLI.

Calls the SAME handler the Facetwork runner dispatches to, with the same
parameter dict. Nothing about the download is reimplemented here — if it were,
the comparison would measure two different pieces of code rather than two
orchestrators.

Run standalone for debugging:

    python download_reactors.py --outdir runs/mock --use-mock
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outdir", required=True, help="FW_DATA_ROOT for this run")
    ap.add_argument("--use-mock", action="store_true", help="Deterministic offline data")
    ap.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = ap.parse_args()

    # Set BEFORE importing the handler, so every path the storage layer resolves
    # lands in this experiment's directory instead of the shared Facetwork
    # cache. That isolation is also what keeps the timing honest: a warm shared
    # cache would let whichever engine ran second "win".
    os.environ["FW_DATA_ROOT"] = os.path.abspath(args.outdir)
    os.environ["FW_STORAGE"] = "local"

    from save_earth.handlers.sources import source_handlers

    def log(message: str, level: str = "info") -> None:
        print(f"[{level}] {message}", file=sys.stderr)

    result = source_handlers.handle_download_nuclear_reactors(
        {"force": args.force, "use_mock": args.use_mock, "_step_log": log}
    )

    # Fail loudly rather than leaving Snakemake to report a missing output file
    # with no explanation: the OSM nuclear set is never empty, so zero features
    # is a real failure, not an empty-but-valid result.
    if not int(result.get("feature_count", 0)):
        print(
            "error: DownloadNuclearReactors returned 0 features — refusing to "
            f"build a map from an empty dataset. Handler result: {result}",
            file=sys.stderr,
        )
        return 1

    log(
        f"{result['feature_count']:,} reactor/plant features "
        f"({'cached' if result.get('was_cached') else 'fetched'})",
        "success",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
