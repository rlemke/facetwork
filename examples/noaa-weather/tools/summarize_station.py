"""Compute yearly climate summaries for one station.

Downloads (if necessary) the station CSV, parses it to the requested
year range, and computes per-year summaries (mean temperature, totals,
hot/frost days, etc.). Emits JSON on stdout and optionally writes to
``cache/noaa-weather/climate-summary/<station_id>.json``.

Usage::

    # Default year range
    python summarize_station.py USW00094728 --state NY

    # Custom range
    python summarize_station.py USW00094728 --state NY --start-year 1970 --end-year 2020

    # Cache the result as a sidecar-backed JSON artifact
    python summarize_station.py USW00094728 --state NY --write-cache
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

from _lib import climate_analysis, ghcn_download, ghcn_parse, sidecar  # noqa: E402
from _lib.storage import LocalStorage  # noqa: E402

NAMESPACE = "noaa-weather"
CACHE_TYPE = "climate-summary"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("station_id", help="GHCN station ID (e.g. USW00094728).")
    parser.add_argument("--state", default="", help="State code to tag the output with.")
    parser.add_argument("--start-year", type=int, default=1944)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the CSV even if cached.",
    )
    parser.add_argument(
        "--use-mock", action="store_true", help="Use offline mock data."
    )
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Write the summary JSON to cache/noaa-weather/climate-summary/.",
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

    res = ghcn_download.download_station_csv(
        args.station_id, force=args.force_download, use_mock=args.use_mock or None
    )
    daily = ghcn_parse.parse_ghcn_csv(res.absolute_path, args.start_year, args.end_year)
    summaries = climate_analysis.compute_yearly_summaries(
        daily, station_id=args.station_id, state=args.state
    )

    output = {
        "station_id": args.station_id,
        "state": args.state,
        "start_year": args.start_year,
        "end_year": args.end_year,
        "years_analyzed": len(summaries),
        "summaries": summaries,
        "source": {
            "cache_type": ghcn_download.STATION_CSV_CACHE_TYPE,
            "relative_path": res.relative_path,
            "sha256": res.sha256,
        },
    }

    if args.write_cache:
        _write_to_cache(output, station_id=args.station_id, csv_sha=res.sha256)

    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _write_to_cache(output: dict, *, station_id: str, csv_sha: str) -> None:
    relative_path = f"{station_id}.json"
    storage = LocalStorage()

    body = json.dumps(output, indent=2, sort_keys=True) + "\n"
    body_bytes = body.encode("utf-8")

    staging_dir = sidecar.staging_dir(NAMESPACE, CACHE_TYPE, storage)
    os.makedirs(staging_dir, exist_ok=True)
    stage_path = os.path.join(staging_dir, f"{station_id}.json.stage-{os.getpid()}")
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
            source={
                "namespace": NAMESPACE,
                "cache_type": ghcn_download.STATION_CSV_CACHE_TYPE,
                "relative_path": f"{station_id}.csv",
                "sha256": csv_sha,
            },
            tool={"name": "summarize_station", "version": "1.0"},
            extra={
                "station_id": station_id,
                "years_analyzed": output["years_analyzed"],
            },
            storage=storage,
        )
    print(f"[cache] wrote {final_path}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
