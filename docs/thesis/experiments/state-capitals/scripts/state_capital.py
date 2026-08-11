#!/usr/bin/env python3
"""Resolve ONE state's capital from the cache. Never touches the network.

This is the fan-out body: one invocation per state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import capitals_lib as lib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--state", required=True, help="ISO 3166-2 code, e.g. US-CA")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    try:
        result = lib.resolve_capital(args.cache_dir, args.state)
    except lib.CacheMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    path = lib.write_state_result(args.out_dir, result)
    print(f"{result['state']}: {result['capital']} -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
