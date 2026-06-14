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

"""CLI for the Claude workflow catalog: list, backup, restore, import.

    python -m facetwork.catalog.cli list
    python -m facetwork.catalog.cli list --package osm-geocoder
    python -m facetwork.catalog.cli backup catalog.json
    python -m facetwork.catalog.cli restore catalog.json
    python -m facetwork.catalog.cli import path/to/workflow.ffl --slug demo.x --publish
    python -m facetwork.catalog.cli import examples/dir/ --tags imported
    python -m facetwork.catalog.cli import-package osm-geocoder --tags osm

Connects to MongoDB via the FFL config (AFL_MONGODB_URL etc.).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def _service(config_path: str | None):
    from facetwork.catalog import CatalogService, MongoCatalogStore
    from facetwork.config import load_config
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore.from_config(load_config(config_path).mongodb)
    return CatalogService(MongoCatalogStore(store._db), store)


def _parse_depends_on(raw: str) -> list[dict]:
    """'lib.a,lib.b@2' -> [{'slug':'lib.a'}, {'slug':'lib.b','version':2}]."""
    deps: list[dict] = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if "@" in part:
            slug, ver = part.rsplit("@", 1)
            deps.append({"slug": slug, "version": int(ver)})
        else:
            deps.append({"slug": part})
    return deps


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="facetwork-catalog", description=__doc__)
    p.add_argument("--config", default=None, help="FFL config file path")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="List catalog entries (packages/libraries + workflows)")
    pl.add_argument("query", nargs="?", default="", help="Optional keyword filter")
    pl.add_argument("--kind", default=None, help="Filter by kind: workflow | library")
    pl.add_argument("--tag", default=None, help="Filter by tag")
    pl.add_argument("--package", default=None, help="List workflows belonging to this library/package")
    pl.add_argument("--published", action="store_true", help="Only entries with a published revision")
    pl.add_argument("--all", action="store_true", dest="show_all",
                    help="Flat list of every entry (including package workflows)")
    pl.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    pl.add_argument("--limit", type=int, default=0, help="Max rows (0 = no limit)")

    pb = sub.add_parser("backup", help="Export the catalog to a JSON file")
    pb.add_argument("outfile", help="Destination .json path")

    pr = sub.add_parser("restore", help="Restore the catalog from a JSON backup")
    pr.add_argument("infile", help="Backup .json path")
    pr.add_argument(
        "--no-recompile",
        action="store_true",
        help="Restore records only; do not rebuild runnable flows from FFL",
    )

    pi = sub.add_parser("import", help="Import file-based .ffl workflows into the catalog")
    pi.add_argument("path", nargs="+", help=".ffl file(s) or directory(ies)")
    pi.add_argument("--slug", default=None, help="Slug (single file only; default: file stem)")
    pi.add_argument("--kind", default="workflow", help="workflow | library")
    pi.add_argument("--title", default="")
    pi.add_argument("--description", default="")
    pi.add_argument("--tags", default="", help="Comma-separated tags")
    pi.add_argument("--entry-workflow", default=None, help="Entry workflow name if multiple")
    pi.add_argument("--depends-on", default="", help="Comma-separated lib slugs, e.g. lib.a,lib.b@2")
    pi.add_argument("--publish", action="store_true", help="Publish each imported revision")
    pi.add_argument("--summary", default="",
                    help="Markdown narrative: why the workflow exists (intent / conversation summary)")
    pi.add_argument("--summary-file", default=None, help="Read --summary from this file (long text)")

    pp = sub.add_parser(
        "import-package",
        help="Import a whole multi-file FFL package: one shared library + one entry per workflow",
    )
    pp.add_argument("name", nargs="?", default=None,
                    help="Registered facetwork example/package name (e.g. osm-geocoder)")
    pp.add_argument("--dir", default=None,
                    help="Import every .ffl under this directory instead of a registered package")
    pp.add_argument("--lib-slug", default=None,
                    help="Slug for the shared library entry (default: package name)")
    pp.add_argument("--also", default="",
                    help="Comma-separated extra package names to merge (cross-package use deps)")
    pp.add_argument("--prefix", default="", help="Prefix prepended to every workflow slug")
    pp.add_argument("--tags", default="", help="Comma-separated tags")
    pp.add_argument("--no-publish", action="store_true",
                    help="Import as drafts (default: publish valid revisions)")

    pa = sub.add_parser(
        "import-all",
        help="Import EVERY discoverable example package's FFL into the catalog "
             "(one shared library + per-workflow entries per package)",
    )
    pa.add_argument("--tags", default="", help="Comma-separated tags applied to every import")
    pa.add_argument("--only", action="append", default=[],
                    help="Restrict to these package names (repeatable)")
    pa.add_argument("--exclude", action="append", default=[],
                    help="Skip these package names (repeatable)")
    pa.add_argument("--dir", action="append", default=[],
                    help="Also import every .ffl under this directory as a package (repeatable)")
    pa.add_argument("--no-publish", action="store_true",
                    help="Import as drafts (default: publish valid revisions)")
    pa.add_argument("--list", action="store_true", dest="list_only",
                    help="List the packages that would be imported, then exit")
    return p


def _fmt_row(s: dict) -> str:
    state = "published" if s.get("published_version") else f"draft v{s.get('latest_version')}"
    invalid = "" if s.get("is_valid", True) else " INVALID"
    title = f"  {s['title']}" if s.get("title") and s["title"] != s["slug"] else ""
    tags = f"  [{', '.join(s['tags'])}]" if s.get("tags") else ""
    return f"  {s['slug']:<44} v{s.get('latest_version', '?')} {state}{invalid}{title}{tags}"


def _cmd_list(svc: Any, args: argparse.Namespace) -> int:
    rows = svc.list_all()

    def keep(s: dict) -> bool:
        if args.kind and s["kind"] != args.kind:
            return False
        if args.tag and args.tag.lower() not in {t.lower() for t in s.get("tags", [])}:
            return False
        if args.published and not s.get("published_version"):
            return False
        if args.package and s.get("package") != args.package:
            return False
        if args.query:
            hay = " ".join(
                [s["slug"], s.get("title", ""), s.get("description", ""), " ".join(s.get("tags", []))]
            ).lower()
            if args.query.lower() not in hay:
                return False
        return True

    rows = [s for s in rows if keep(s)]
    if args.limit:
        rows = rows[: args.limit]

    if args.json:
        import json as _json

        print(_json.dumps(rows, indent=2, default=str))
        return 0

    flat = bool(
        args.query or args.kind or args.tag or args.package or args.published or args.show_all
    )
    if flat:
        if not rows:
            print("No matching catalog entries.")
            return 0
        for s in rows:
            extra = (
                f"  ({s['member_count']} workflows)"
                if s["kind"] == "library" and s["member_count"]
                else ""
            )
            print(_fmt_row(s) + extra)
        print(f"\n{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}.")
        return 0

    # Default: grouped overview (packages, standalone workflows, package summary).
    libraries = [s for s in rows if s["kind"] == "library"]
    standalone = [s for s in rows if s["kind"] == "workflow" and not s["package"]]
    package_wfs = [s for s in rows if s["kind"] == "workflow" and s["package"]]

    if libraries:
        print("Packages / libraries:")
        for s in libraries:
            n = s["member_count"]
            members = f"{n} workflow{'' if n == 1 else 's'}" if n else "no members"
            print(_fmt_row(s) + f"  ({members})")

    print(f"\nStandalone workflows ({len(standalone)}):")
    for s in standalone:
        print(_fmt_row(s))

    if package_wfs:
        by_pkg: dict[str, int] = {}
        for s in package_wfs:
            by_pkg[s["package"]] = by_pkg.get(s["package"], 0) + 1
        print(f"\nPackage workflows: {len(package_wfs)} "
              f"(use 'list --package <slug>' or 'list --all' to list them)")
        for pkg, n in sorted(by_pkg.items()):
            print(f"  {pkg}: {n}")

    print(f"\n{len(rows)} total entries.")
    return 0


def _cmd_import_all(svc: Any, args: argparse.Namespace) -> int:
    """Import every discoverable example package (and any --dir) as a package."""
    from facetwork.catalog import backup
    from facetwork.catalog.entities import STATUS_PUBLISHED
    from facetwork.examples import collect_ffl_files, discover_all_examples

    repo_root = Path(__file__).resolve().parents[2]
    user_tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    publish = not args.no_publish

    pkgs = discover_all_examples(repo_root)
    if args.only:
        only = set(args.only)
        pkgs = [p for p in pkgs if p.name in only]
    if args.exclude:
        excl = set(args.exclude)
        pkgs = [p for p in pkgs if p.name not in excl]
    pkgs = sorted(pkgs, key=lambda p: p.name)

    # Work list: registered packages first, then any explicit --dir targets.
    targets: list[tuple[str, str, dict]] = [
        ("package", p.name, {"name": p.name}) for p in pkgs
    ]
    for d in args.dir:
        targets.append(("dir", Path(d).name, {"ffl_dir": d, "lib_slug": Path(d).name}))

    if args.list_only:
        print(f"{len(targets)} target(s) would be imported:")
        for kind, label, _kw in targets:
            print(f"  [{kind}] {label}")
        return 0

    pkg_by_name = {p.name: p for p in pkgs}
    ok: list[tuple[str, int, int]] = []
    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    total_wf = total_pub = 0

    for kind, label, kw in targets:
        # Skip registered packages that ship no FFL (handler-only packages).
        if kind == "package" and label in pkg_by_name and not collect_ffl_files(pkg_by_name[label]):
            skipped.append((label, "no FFL files"))
            print(f"SKIP  {label}: no FFL files")
            continue
        try:
            results = backup.import_package(
                svc, tags=(user_tags + [label]) or None, publish=publish, **kw
            )
        except Exception as e:  # keep going — one bad package shouldn't abort the rest
            failed.append((label, str(e)))
            print(f"FAIL  {label}: {e}", file=sys.stderr)
            continue
        wfs = results[1:]
        npub = sum(1 for _slug, r in wfs if getattr(r, "status", "") == STATUS_PUBLISHED)
        total_wf += len(wfs)
        total_pub += npub
        ok.append((label, len(wfs), npub))
        print(f"OK    {label}: {len(wfs)} workflows ({npub} published)")

    print("\n=== import-all summary ===")
    print(f"  packages imported: {len(ok)}")
    print(f"  workflows: {total_wf} ({total_pub} published)")
    if skipped:
        print(f"  skipped ({len(skipped)}): " + ", ".join(n for n, _ in skipped))
    if failed:
        print(f"  FAILED ({len(failed)}):")
        for n, err in failed:
            print(f"    {n}: {err}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from facetwork.catalog import backup

    try:
        svc = _service(args.config)
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}", file=sys.stderr)
        return 1

    if args.cmd == "list":
        return _cmd_list(svc, args)

    if args.cmd == "import-all":
        return _cmd_import_all(svc, args)

    if args.cmd == "backup":
        summary = backup.export_to_file(svc, args.outfile)
        print(f"Backed up {summary['entries']} entries / {summary['revisions']} revisions "
              f"-> {summary['path']}")
        return 0

    if args.cmd == "restore":
        res = backup.restore_from_file(args.infile, svc, rematerialize=not args.no_recompile)
        print(f"Restored {res['entries']} entries / {res['revisions']} revisions; "
              f"made {res['rematerialized']} revision(s) runnable "
              f"(package workflows share their library's flow).")
        for f in res["failed"]:
            print(f"  WARN {f['slug']} v{f['version']} not runnable: {'; '.join(f['warnings'])}",
                  file=sys.stderr)
        return 0

    if args.cmd == "import":
        meta: dict[str, Any] = {
            "kind": args.kind,
            "title": args.title,
            "description": args.description,
            "tags": [t.strip() for t in args.tags.split(",") if t.strip()] or None,
            "entry_workflow": args.entry_workflow,
            "depends_on": _parse_depends_on(args.depends_on) or None,
            "author": "import",
            "summary": (Path(args.summary_file).read_text() if args.summary_file else args.summary),
        }
        try:
            results = backup.import_files(
                svc, args.path, slug=args.slug, publish=args.publish, **meta
            )
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        rc = 0
        for path, res in results:
            if res.ok:
                tag = " (deduped)" if res.deduped else ""
                valid = "valid" if res.is_valid else "INVALID"
                print(f"  {res.slug} v{res.version} [{res.status}, {valid}]{tag}  <- {path}")
                if not res.is_valid:
                    rc = 1
            else:
                print(f"  FAILED {path}: {res.error}", file=sys.stderr)
                rc = 1
        return rc

    if args.cmd == "import-package":
        from facetwork.catalog.entities import STATUS_PUBLISHED

        if not args.name and not args.dir:
            print("Error: provide a package name or --dir", file=sys.stderr)
            return 2
        try:
            results = backup.import_package(
                svc,
                name=(None if args.dir else args.name),
                ffl_dir=args.dir,
                lib_slug=args.lib_slug,
                also=[a.strip() for a in args.also.split(",") if a.strip()] or None,
                prefix=args.prefix,
                tags=[t.strip() for t in args.tags.split(",") if t.strip()] or None,
                publish=not args.no_publish,
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        libname, libres = results[0]
        workflows = results[1:]
        npub = sum(1 for _, r in workflows if getattr(r, "status", "") == STATUS_PUBLISHED)
        print(f"{libname} v{libres.version} "
              f"[{'valid' if libres.is_valid else 'INVALID'}] — {len(workflows)} workflows")
        for slug, rev in workflows[:12]:
            print(f"  {slug} [{rev.status}]")
        if len(workflows) > 12:
            print(f"  ... +{len(workflows) - 12} more")
        print(f"Imported {len(workflows)} workflows ({npub} published) sharing 1 flow.")
        return 0 if libres.is_valid else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
