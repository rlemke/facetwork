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

"""CLI: migrate the ``dependency_signal`` idiom to the ``after`` clause.

    python -m facetwork.migration.after_cli PATH [PATH ...] [--write]

Without ``--write`` it is a dry run: prints a unified diff of the proposed edits
plus any sites that need a human. Paths may be files or directories (scanned for
``*.ffl``).
"""

from __future__ import annotations

import argparse
import difflib
import glob
import os
import sys

from .after_migrator import migrate_source


def _collect(paths: list[str]) -> list[str]:
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "**", "*.ffl"), recursive=True)))
        elif p.endswith(".ffl"):
            files.append(p)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fw ffl migrate-after", description=__doc__)
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
            for ln, note in res.edits:
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
            for ln, note in res.manual:
                print(f"  L{ln}: {note}")

    verb = "wrote" if args.write else "would change"
    print(f"\n{verb} {changed} file(s); {manual_total} manual site(s) across {len(files)} scanned.")
    print(
        "\nNOTE: this rewrites CALL SITES only. The now-unused `dependency_signal`\n"
        "parameter stays on the facet declarations on purpose — removing it changes\n"
        "handler kwargs, so it must wait until every live runner is on an image that\n"
        "no longer passes it (docs/architecture/ffl-after-clause.md §7)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
