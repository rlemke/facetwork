#!/usr/bin/env python3
"""BuildMap (nuclear layer), invoked as a plain CLI.

Calls the same handler the Facetwork runner dispatches to, with the parameter
dict BuildNuclearReactorMap passes it.

Run standalone for debugging:

    python build_map.py --outdir runs/mock
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outdir", required=True, help="FW_DATA_ROOT for this run")
    ap.add_argument("--region", default="nuclear")
    ap.add_argument("--center-lat", type=float, default=25.0)
    ap.add_argument("--center-lon", type=float, default=10.0)
    ap.add_argument("--zoom", type=float, default=2.0)
    ap.add_argument("--only-layers", default="nuclear-reactors")
    ap.add_argument("--attribution-workflow", default="")
    ap.add_argument("--attribution-ffl-url", default="")
    ap.add_argument("--description", default="")
    args = ap.parse_args()

    os.environ["FW_DATA_ROOT"] = os.path.abspath(args.outdir)
    os.environ["FW_STORAGE"] = "local"

    from save_earth.handlers.maps import map_handlers

    def log(message: str, level: str = "info") -> None:
        print(f"[{level}] {message}", file=sys.stderr)

    result = map_handlers.handle_build_map(
        {
            "region": args.region,
            "center_lat": args.center_lat,
            "center_lon": args.center_lon,
            "zoom": args.zoom,
            "only_layers": args.only_layers,
            "attribution_workflow": args.attribution_workflow,
            "attribution_ffl_url": args.attribution_ffl_url,
            "description": args.description,
            "_step_log": log,
        }
    )

    # handle_build_map returns an empty html_path when no layer had cached data,
    # rather than raising. Snakemake would then report a missing output file
    # with no cause; say what actually happened instead.
    if not result.get("html_path"):
        print(
            "error: BuildMap rendered no layers — the nuclear GeoJSON was not "
            f"found under FW_DATA_ROOT={os.environ['FW_DATA_ROOT']}. "
            f"Handler result: {result}",
            file=sys.stderr,
        )
        return 1

    log(f"{result['html_path']} ({result['layer_count']} layer(s))", "success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
