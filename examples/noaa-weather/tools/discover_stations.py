"""Filter the GHCN catalog + inventory into a candidate station list.

Reads the cached catalog (downloading it first if stale), filters by
country / state / data coverage / required elements, and emits the
result as JSON on stdout. Logs go to stderr.

Usage::

    # All US stations with 20+ years of TMAX/TMIN/PRCP
    python discover_stations.py --country US --min-years 20

    # Top 10 NY stations
    python discover_stations.py --country US --state NY --max-stations 10

    # Custom required elements
    python discover_stations.py --country US --required TMAX --required TMIN

    # Write the filtered list to a sidecar-cached JSON artifact too
    python discover_stations.py --country US --state NY --write-cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import ghcn_download, ghcn_parse, sidecar  # noqa: E402
from _lib.storage import LocalStorage  # noqa: E402

NAMESPACE = "noaa-weather"
CACHE_TYPE = "stations-discovered"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--country", default="US", help="FIPS country code (default: US).")
    parser.add_argument(
        "--state",
        default="",
        help="2-letter US state abbreviation (empty = no state filter).",
    )
    parser.add_argument(
        "--max-stations", type=int, default=10, help="Max stations to return (default: 10)."
    )
    parser.add_argument(
        "--min-years", type=int, default=20, help="Minimum years of data (default: 20)."
    )
    parser.add_argument(
        "--required",
        action="append",
        default=None,
        help="Required element (TMAX/TMIN/PRCP/…). Repeatable. Default: TMAX TMIN PRCP.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the catalog even if the cache is current.",
    )
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help="Use offline mock catalog (deterministic fake data).",
    )
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Also write the filtered list to cache/noaa-weather/stations-discovered/.",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python logging level (default: INFO)."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    stations_text = ghcn_download.read_catalog_file(
        "stations", force=args.force_download, use_mock=args.use_mock or None
    )
    inventory_text = ghcn_download.read_catalog_file(
        "inventory", force=args.force_download, use_mock=args.use_mock or None
    )

    stations = ghcn_parse.parse_stations(stations_text)
    inventory = ghcn_parse.parse_inventory(inventory_text)
    filtered = ghcn_parse.filter_stations(
        stations,
        inventory,
        country=args.country,
        state=args.state,
        max_stations=args.max_stations,
        min_years=args.min_years,
        required_elements=args.required,
    )

    output = {
        "country": args.country,
        "state": args.state,
        "max_stations": args.max_stations,
        "min_years": args.min_years,
        "required_elements": args.required or ["TMAX", "TMIN", "PRCP"],
        "station_count": len(filtered),
        "stations": filtered,
    }

    if args.write_cache:
        _write_to_cache(output, country=args.country, state=args.state)

    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _write_to_cache(output: dict, *, country: str, state: str) -> None:
    """Persist the filtered station list as a cache artifact with sidecar."""
    relative_path = f"{country}/{state or 'ALL'}.json"
    storage = LocalStorage()

    body = json.dumps(output, indent=2, sort_keys=True) + "\n"
    body_bytes = body.encode("utf-8")

    staging_dir = sidecar.staging_dir(NAMESPACE, CACHE_TYPE, storage)
    os.makedirs(staging_dir, exist_ok=True)
    stage_name = f"{country}_{state or 'ALL'}.json.stage-{os.getpid()}"
    stage_path = os.path.join(staging_dir, stage_name)
    with open(stage_path, "wb") as f:
        f.write(body_bytes)

    final_path = sidecar.cache_path(NAMESPACE, CACHE_TYPE, relative_path, storage)
    with sidecar.entry_lock(NAMESPACE, CACHE_TYPE, relative_path, storage=storage):
        storage.finalize_from_local(stage_path, final_path)
        sidecar.write_sidecar(
            NAMESPACE,
            CACHE_TYPE,
            relative_path,
            kind="file",
            size_bytes=len(body_bytes),
            sha256=hashlib.sha256(body_bytes).hexdigest(),
            tool={"name": "discover_stations", "version": "1.0"},
            extra={
                "country": country,
                "state": state,
                "station_count": output["station_count"],
            },
            storage=storage,
        )
    print(f"[cache] wrote {final_path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
