#!/usr/bin/env python3
"""Expand a named regen scope into (region, level) pairs, and guard the chain.

Used by `fw svc osm-admin-regen --set NAME`. Two jobs:

1. EXPAND. A set is either an explicit region list, or `regions_from`, which
   enumerates the object store (e.g. every US state) so a 51-way scope does not
   have to be typed out and cannot drift from what actually exists.

2. GUARD. ⚠️ BuildAdminSet downloads its source FROM THE BUCKET. Regenerating a
   tier from a stale parent writes children with a FRESH mtime over month-old
   data - and because sub-regions carry no osmosis_replication_timestamp, mtime
   is the only age signal anything has. The result would report as current while
   being a month old, which is worse than the honest staleness it replaces. So a
   set may declare `requires_fresh`, and this refuses to expand it while those
   sources are older than the threshold.

Exit codes: 0 expanded (pairs on stdout, one "region<TAB>level" per line),
1 refused by the freshness guard, 2 could not check.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import sys

DEFAULT_SETS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "osm-admin-sets.json")


def _s3():
    import boto3
    return boto3.client(
        "s3", endpoint_url=os.environ.get("FW_S3_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY", "minioadmin"))


def _objects(client, bucket: str, prefix: str = "", *, delimiter: str = "/"):
    """List .osm.pbf under `prefix`, ONE level deep by default.

    ⚠️ Never list this bucket without a delimiter. Every extract has a paired
    `<region>-updates/` directory holding thousands of small replication files,
    so a bare recursive listing walks ~100k keys and exceeded a 10-minute
    timeout here. The tiers this tool reasons about are exactly the immediate
    children of a prefix, so the delimiter costs nothing and saves everything.
    """
    kwargs = {"Bucket": bucket, "Prefix": prefix}
    if delimiter:
        kwargs["Delimiter"] = delimiter
    pages = client.get_paginator("list_objects_v2").paginate(**kwargs)
    return [o for page in pages for o in page.get("Contents", [])
            if o["Key"].endswith(".osm.pbf")]


def _prefix_of(pattern: str) -> str:
    """The fixed directory part of a glob, so we list only that subtree."""
    head = pattern.split("*")[0].split("?")[0]
    return head[: head.rfind("/") + 1] if "/" in head else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, dest="set_name")
    ap.add_argument("--bucket", default=os.environ.get("FW_OSM_EXTRACT_BUCKET", "osm-extracts"))
    ap.add_argument("--max-source-age-days", type=float, default=14.0,
                    help="refuse when a requires_fresh source is older than this")
    ap.add_argument("--ignore-stale-source", action="store_true",
                    help="expand anyway; the children will carry a fresh mtime over "
                         "stale data, so only use this deliberately")
    ap.add_argument("--list-sets", action="store_true")
    a = ap.parse_args()

    path = os.environ.get("FW_OSM_REGEN_SETS_FILE") or DEFAULT_SETS
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2
    sets = cfg.get("sets") or {}
    if a.list_sets:
        for name, spec in sets.items():
            print(f"{name}\t{spec.get('description', '')}")
        return 0
    spec = sets.get(a.set_name)
    if not spec:
        print(f"unknown set {a.set_name!r}; known: {', '.join(sorted(sets))}", file=sys.stderr)
        return 2

    try:
        client = _s3()
        # Only the subtrees this set actually references, never the whole bucket.
        wanted = {_prefix_of(p) for p in (spec.get("requires_fresh") or [])}
        rf = spec.get("regions_from")
        if rf:
            wanted.add(rf["prefix"])
        if not wanted:
            wanted = {""}
        objects = []
        for pfx in sorted(wanted):
            objects += _objects(client, a.bucket, pfx)
    except Exception as exc:  # noqa: BLE001
        print(f"could not list {a.bucket}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    now = datetime.datetime.now(datetime.UTC)
    ages = {o["Key"]: (now - o["LastModified"]).total_seconds() / 86400.0 for o in objects}

    # --- guard -------------------------------------------------------------
    def _match(key: str, pattern: str) -> bool:
        """fnmatch, but `*` must NOT cross a path separator.

        Plain fnmatch made `north-america/us/*-latest.osm.pbf` match
        `north-america/us/alabama/autauga-latest.osm.pbf` - a COUNTY, when the
        pattern names the STATE tier. The guard then checked the wrong tier, and
        would have allowed a county rebuild whenever the counties themselves
        happened to be fresh. Requiring equal depth pins the tier.
        """
        return key.count("/") == pattern.count("/") and fnmatch.fnmatch(key, pattern)

    stale: list[tuple[str, float]] = []
    for pattern in spec.get("requires_fresh") or []:
        matched = [k for k in ages if _match(k, pattern)]
        if not matched:
            print(f"requires_fresh pattern {pattern!r} matched nothing in {a.bucket}",
                  file=sys.stderr)
            return 2
        worst = max(matched, key=lambda k: ages[k])
        if ages[worst] > a.max_source_age_days:
            stale.append((worst, ages[worst]))
    if stale and not a.ignore_stale_source:
        print(f"REFUSED: set {a.set_name!r} needs fresher sources than the bucket holds.",
              file=sys.stderr)
        for key, age in stale:
            print(f"  {key}  {age:.1f} days old (limit {a.max_source_age_days:g})",
                  file=sys.stderr)
        print("\n  BuildAdminSet reads its source FROM THE BUCKET, so building on these\n"
              "  would stamp children with a fresh mtime over month-old data - and these\n"
              "  sub-regions carry no replication timestamp, so mtime is the only age\n"
              "  signal anything has. Refresh the parent tier first, or pass\n"
              "  --ignore-stale-source if you mean it.", file=sys.stderr)
        return 1

    # --- expand ------------------------------------------------------------
    # One line per submission: "<workflow>\t<inputs-json>". A set is a WORKFLOW
    # plus inputs, because the three tiers are built by three different
    # workflows that key extracts differently - see the config's
    # _three_paths_comment for what using the wrong one costs.
    workflow = spec.get("workflow")
    if not workflow:
        print(f"set {a.set_name!r} has no 'workflow'", file=sys.stderr)
        return 2
    submissions = spec.get("inputs_each") or [spec.get("inputs") or {}]
    if not submissions:
        print(f"set {a.set_name!r} expanded to nothing", file=sys.stderr)
        return 2
    # Which runner knobs may be merged in. Not every workflow accepts every one:
    # BuildPlanetExtracts has no refresh_after_days/force_refresh, and passing an
    # unknown input fails the submission.
    allowed = spec.get("merge_defaults")
    if allowed is None:
        allowed = ["bucket", "strategy", "refresh_after_days", "force_refresh"]
    for inputs in submissions:
        print(f"{workflow}\t{json.dumps(inputs)}\t{json.dumps(allowed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
