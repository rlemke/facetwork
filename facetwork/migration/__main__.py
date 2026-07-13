# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI: migrate legacy FFL to the relative $-scoping model.

    python -m facetwork.migration PATH [PATH ...] [--write]

Without --write it is a dry run: prints a unified diff of proposed edits and
any sites that need manual handling (sibling-block `andThen when`). With --write
the edits are applied in place. Paths may be files or directories (scanned for
``*.ffl``).
"""

from __future__ import annotations

import argparse
import difflib
import glob
import os
import sys

from . import migrate_source


def _collect(paths: list[str]) -> list[str]:
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "**", "*.ffl"), recursive=True)))
        elif p.endswith(".ffl"):
            files.append(p)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fw ffl migrate-scoping", description=__doc__)
    ap.add_argument("paths", nargs="+", help="FFL files or directories")
    ap.add_argument("--write", action="store_true", help="apply edits in place (default: dry run)")
    args = ap.parse_args(argv)

    files = _collect(args.paths)
    if not files:
        print("No .ffl files found", file=sys.stderr)
        return 1

    changed = 0
    manual_total = 0
    for f in files:
        src = open(f).read()
        try:
            res = migrate_source(src)
        except Exception as e:  # parse errors etc. — report, don't abort the run
            print(f"!! {f}: could not migrate ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        if res.changed:
            changed += 1
            print(f"\n=== {f}  ({len(res.edits)} edit(s)) ===")
            for ln, _col, note in res.edits:
                print(f"  L{ln}: {note}")
            if not args.write:
                for line in difflib.unified_diff(
                    src.splitlines(),
                    res.source.splitlines(),
                    lineterm="",
                    n=1,
                    fromfile=f,
                    tofile=f + " (migrated)",
                ):
                    print("  " + line)
            else:
                with open(f, "w") as fh:
                    fh.write(res.source)
        if res.manual:
            manual_total += len(res.manual)
            print(f"\n-- {f}: {len(res.manual)} site(s) need MANUAL handling --")
            for ln, _col, note in res.manual:
                print(f"  L{ln}: {note}")

    verb = "wrote" if args.write else "would change"
    print(f"\n{verb} {changed} file(s); {manual_total} manual site(s) across {len(files)} scanned.")
    if manual_total and not args.write:
        print(
            "Manual sites are sibling-block `andThen when` — reattach the when to the "
            "step it gates and rewrite its refs to $."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
