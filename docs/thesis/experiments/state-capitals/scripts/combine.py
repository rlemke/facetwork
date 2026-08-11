#!/usr/bin/env python3
"""Fan-in: merge the per-state results into one table."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import capitals_lib as lib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--states-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    res = lib.combine(args.states_dir, args.out)
    print(f"{res['count']} capitals -> {res['out_path']} (+ .csv)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
