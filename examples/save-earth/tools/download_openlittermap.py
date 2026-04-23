"""Download OpenLitterMap geotagged litter observations.

Outputs land at
``$AFL_CACHE_ROOT/save-earth/openlittermap/points.geojson`` with a
sidebar ``.meta.json`` sidecar per the cache-layout spec.

Usage::

    python download_openlittermap.py
    python download_openlittermap.py --force
    python download_openlittermap.py --url <new-endpoint>
    python download_openlittermap.py --bbox 24.4,49.4,-125.0,-66.9   # US
    python download_openlittermap.py --use-mock
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import openlittermap  # noqa: E402


def _parse_bbox(s: str) -> tuple[float, float, float, float]:
    try:
        parts = [float(p) for p in s.split(",")]
    except ValueError as exc:
        raise SystemExit(f"error: --bbox needs 4 comma-separated numbers: {exc}")
    if len(parts) != 4:
        raise SystemExit("error: --bbox needs exactly 4 values (min_lat,max_lat,min_lon,max_lon)")
    return tuple(parts)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=openlittermap.DEFAULT_URL,
        help=f"Upstream GeoJSON URL (default: {openlittermap.DEFAULT_URL}).",
    )
    parser.add_argument("--force", action="store_true", help="Re-download even if cached.")
    parser.add_argument(
        "--bbox",
        default=None,
        help="Trim cached features to this bbox: 'min_lat,max_lat,min_lon,max_lon'.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=openlittermap.DEFAULT_MAX_AGE_HOURS,
    )
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help="Opt in to deterministic mock data (no network).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    bbox = _parse_bbox(args.bbox) if args.bbox else None

    try:
        res = openlittermap.download(
            url=args.url,
            force=args.force,
            max_age_hours=args.max_age_hours,
            bbox=bbox,
            use_mock=args.use_mock,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    status = "cache" if res.was_cached else ("mock" if res.used_mock else "download")
    print(
        f"[{status}] openlittermap/{res.relative_path}  "
        f"{res.feature_count:,} features  {res.size_bytes:,}B  "
        f"sha256={res.sha256[:12]}…  {res.absolute_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
