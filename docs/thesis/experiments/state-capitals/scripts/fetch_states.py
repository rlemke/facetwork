#!/usr/bin/env python3
"""Fetch the OSM state relations + capital nodes into the cache (the ONE networked step)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import capitals_lib as lib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = ap.parse_args()

    res = lib.fetch_state_data(args.cache_dir, force=args.force)
    print(
        f"[{'cached' if res['was_cached'] else 'downloaded'}] "
        f"{res['state_count']} state relations -> {res['cache_path']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
