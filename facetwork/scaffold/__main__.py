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

"""CLI for the handler-gap scaffolder.

    # scaffold a named facet's contract + handler + test stubs (for review)
    python -m facetwork.scaffold osm.Filters.FilterGeoJSONByTagContains \\
        --params "input_path:String,tag_key:String,substring:String" \\
        --returns "result:OSMFilteredFeatures" --out scaffold/

    # detect which event facets a composed workflow needs but the fleet lacks
    python -m facetwork.scaffold --detect-gaps --ffl wf.ffl --entry MyWorkflow \\
        --registered "osm.ops.CacheRegion,osm.viz.RenderMap"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import (
    FacetSpec,
    detect_missing_facets,
    parse_pairs,
    review_checklist,
    scaffold_facet,
    scaffold_handler,
    scaffold_test,
)


def _detect(args: argparse.Namespace) -> int:
    import json

    from facetwork.ast import Program
    from facetwork.emitter import JSONEmitter
    from facetwork.parser import FFLParser

    sources = [Path(args.ffl).read_text()]
    for extra in args.also_ffl or []:
        sources.append(Path(extra).read_text())
    parser = FFLParser()
    merged = Program.merge([parser.parse(s) for s in sources])
    program = json.loads(JSONEmitter(include_locations=False).emit(merged))

    reg = args.registered or ""
    if reg.startswith("@"):
        registered = set(Path(reg[1:]).read_text().split())
    else:
        registered = {r.strip() for r in reg.split(",") if r.strip()}

    missing = detect_missing_facets(program, args.entry, registered)
    if not missing:
        print("No handler gaps: every event facet the entry reaches is registered.")
        return 0
    print(f"Handler gaps for '{args.entry}' ({len(missing)}):")
    for m in missing:
        print(
            f"  - {m}  (needs a handler — scaffold with: "
            f"python -m facetwork.scaffold {m} --params '...' --returns '...')"
        )
    return 0


def _scaffold(args: argparse.Namespace) -> int:
    spec = FacetSpec(
        qualified_name=args.facet,
        params=parse_pairs(args.params or ""),
        returns=parse_pairs(args.returns or ""),
        doc=args.doc or "",
    )
    files = {
        f"{spec.short_name}.ffl": scaffold_facet(spec),
        f"{spec.module_stem}.py": scaffold_handler(spec),
        f"test_{spec.module_stem}.py": scaffold_test(spec),
        "REVIEW.md": review_checklist(spec),
    }
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (out / name).write_text(content)
        print(f"Scaffolded {spec.qualified_name} -> {out}/ ({', '.join(files)})")
        print("These are review stubs — implement, review, register, then deploy (see REVIEW.md).")
    else:
        for name, content in files.items():
            print(f"\n===== {name} =====\n{content}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scaffold-handler", description=__doc__)
    p.add_argument("facet", nargs="?", help="Qualified facet name, e.g. osm.Filters.NewFilter")
    p.add_argument("--params", default="", help='Comma list "name:Type,name2:Type2"')
    p.add_argument("--returns", default="", help='Comma list "field:Type,field2:Type2"')
    p.add_argument("--doc", default="", help="One-line description for the facet")
    p.add_argument("--out", default=None, help="Write stubs to this dir (else print)")
    p.add_argument("--detect-gaps", action="store_true", help="List handler gaps for a workflow")
    p.add_argument("--ffl", default=None, help="(detect) FFL file with the composed workflow")
    p.add_argument("--also-ffl", action="append", help="(detect) extra FFL files to merge (deps)")
    p.add_argument("--entry", default=None, help="(detect) entry workflow name")
    p.add_argument(
        "--registered", default="", help="(detect) registered facets: comma list or @file"
    )
    args = p.parse_args(argv)

    if args.detect_gaps:
        if not args.ffl or not args.entry:
            print("Error: --detect-gaps needs --ffl and --entry", file=sys.stderr)
            return 2
        return _detect(args)
    if not args.facet:
        p.error("provide a facet name to scaffold, or use --detect-gaps")
    return _scaffold(args)


if __name__ == "__main__":
    sys.exit(main())
