#!/usr/bin/env python3
"""Search a STAC catalog for Sentinel-2 scenes over an AOI + date window.

Usage:
    python tools/search_scenes.py --aoi MIN_LON,MIN_LAT,MAX_LON,MAX_LAT \\
        --from 2024-06-01 --to 2024-09-30 [--max-cloud 20] [--use-mock]

stdout: JSON {"count": N, "scene_ids": [...]}   (pipe into fetch_scene_index)
stderr: log lines.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s2_tools import stac  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--aoi", required=True, help="min_lon,min_lat,max_lon,max_lat")
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.add_argument("--max-cloud", type=float, default=20.0)
    p.add_argument("--collection", default="sentinel-2-l2a")
    p.add_argument("--stac-url", default="https://earth-search.aws.element84.com/v1")
    p.add_argument("--use-mock", action="store_true", help="offline deterministic mode")
    p.add_argument("--log-level", default="INFO")
    a = p.parse_args()
    logging.basicConfig(level=a.log_level, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    scenes = stac.search(a.aoi, a.date_from, a.date_to, max_cloud=a.max_cloud,
                         collection=a.collection, stac_url=a.stac_url, use_mock=a.use_mock)
    ids = [s["scene_id"] for s in scenes]
    logging.info("found %d scenes", len(ids))
    json.dump({"count": len(ids), "scene_ids": ids}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
