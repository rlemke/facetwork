"""Download per-station GHCN CSVs into the shared cache.

Outputs land at ``$AFL_CACHE_ROOT/noaa-weather/station-csv/<station_id>.csv``
with a sibling ``.meta.json`` sidecar.

Usage::

    # One station
    python fetch_station_csv.py USW00094728

    # Multiple
    python fetch_station_csv.py USW00094728 USW00014732 USW00094846

    # From a file (one station_id per line, # for comments)
    python fetch_station_csv.py --stations-file my-stations.txt

    # Force re-download even if cached
    python fetch_station_csv.py USW00094728 --force

    # Offline mode
    python fetch_station_csv.py USW00094728 --use-mock
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import ghcn_download  # noqa: E402


def _read_stations_file(path: Path) -> list[str]:
    ids: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("station_ids", nargs="*", help="GHCN station IDs to fetch.")
    parser.add_argument(
        "--stations-file",
        type=Path,
        help="File with one station_id per line (lines starting with # are comments).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the cache is current.",
    )
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help="Use offline mock data (deterministic, no network).",
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

    ids: list[str] = list(args.station_ids)
    if args.stations_file:
        ids.extend(_read_stations_file(args.stations_file))

    if not ids:
        parser.error("no station IDs provided — pass them positionally or via --stations-file")

    failures: list[str] = []
    for sid in ids:
        try:
            res = ghcn_download.download_station_csv(
                sid, force=args.force, use_mock=args.use_mock or None
            )
        except Exception as exc:
            print(f"error: {sid}: {exc}", file=sys.stderr)
            failures.append(sid)
            continue
        status = "cache" if res.was_cached else ("mock" if res.used_mock else "download")
        print(
            f"[{status}] {sid}  {res.size_bytes:,}B  sha256={res.sha256[:12]}…  "
            f"{res.absolute_path}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
