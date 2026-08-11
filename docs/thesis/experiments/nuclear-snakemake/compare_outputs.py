#!/usr/bin/env python3
"""Compare a Snakemake run's output against a Facetwork run's, artifact by artifact.

    python compare_outputs.py --facetwork <FW_DATA_ROOT> --snakemake runs/snakemake

Exits 0 when the two agree, 1 when they differ, so it can gate a CI check.

Two things have to be normalised or every comparison fails for uninteresting
reasons:

* **The render timestamp.** map_render stamps "generated at <UTC minute>" into
  the HTML, so two runs a minute apart always differ. It is masked, and the
  masking is REPORTED so nobody mistakes this for a byte-identical result.
* **GeoJSON feature order.** Nothing in the pipeline guarantees a stable order
  across two independent Overpass responses, and order carries no meaning for a
  map. Features are compared as a multiset keyed by a canonical digest.

What is deliberately NOT normalised is feature content: if the two runs
disagree about a reactor's tags, that is a real difference and must show up.

A caveat this tool cannot fix: two LIVE runs query Overpass at different
moments, so a genuine upstream edit between them is indistinguishable from a
pipeline difference. For a strict equality check run both with use_mock, which
is deterministic; use live runs for timing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

NAMESPACE = "save-earth"
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")


def _cache(root: Path) -> Path:
    return root / "cache" / NAMESPACE


def _feature_digest(feature: dict) -> str:
    """Order-independent, canonical digest of one GeoJSON feature."""
    return hashlib.sha256(
        json.dumps(feature, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compare_geojson(a: Path, b: Path) -> list[str]:
    problems: list[str] = []
    for p in (a, b):
        if not p.exists():
            return [f"missing GeoJSON: {p}"]

    fa, fb = json.loads(a.read_text()), json.loads(b.read_text())
    feats_a = fa.get("features", [])
    feats_b = fb.get("features", [])

    if len(feats_a) != len(feats_b):
        problems.append(f"feature count differs: facetwork={len(feats_a):,} snakemake={len(feats_b):,}")

    digests_a = {_feature_digest(f) for f in feats_a}
    digests_b = {_feature_digest(f) for f in feats_b}
    only_a, only_b = digests_a - digests_b, digests_b - digests_a
    if only_a or only_b:
        problems.append(
            f"feature content differs: {len(only_a)} only in facetwork, "
            f"{len(only_b)} only in snakemake"
        )
        # A couple of examples make an upstream edit recognisable at a glance.
        for label, feats, only in (("facetwork", feats_a, only_a), ("snakemake", feats_b, only_b)):
            for f in feats:
                if _feature_digest(f) in only:
                    name = (f.get("properties") or {}).get("name", "<unnamed>")
                    problems.append(f"    e.g. only in {label}: {name}")
                    break
    return problems


def compare_html(a: Path, b: Path) -> tuple[list[str], bool]:
    """Compare rendered maps. Returns (problems, timestamp_was_masked)."""
    for p in (a, b):
        if not p.exists():
            return [f"missing HTML: {p}"], False

    ha, hb = a.read_text(), b.read_text()
    masked = bool(TIMESTAMP_RE.search(ha) or TIMESTAMP_RE.search(hb))
    na, nb = TIMESTAMP_RE.sub("<TS>", ha), TIMESTAMP_RE.sub("<TS>", hb)

    if na == nb:
        return [], masked

    problems = [f"HTML differs: facetwork={len(ha):,}B snakemake={len(hb):,}B"]
    # Point at the first differing line rather than dumping two files.
    for i, (la, lb) in enumerate(zip(na.splitlines(), nb.splitlines()), 1):
        if la != lb:
            problems.append(f"    first difference at line {i}:")
            problems.append(f"      facetwork: {la[:160]}")
            problems.append(f"      snakemake: {lb[:160]}")
            break
    else:
        problems.append(
            f"    identical prefix; line count differs "
            f"({len(na.splitlines())} vs {len(nb.splitlines())})"
        )
    return problems, masked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--facetwork", type=Path, required=True,
                    help="FW_DATA_ROOT the Facetwork run wrote to")
    ap.add_argument("--snakemake", type=Path, required=True,
                    help="outdir the Snakemake run wrote to")
    ap.add_argument("--region", default="nuclear")
    args = ap.parse_args()

    fw, sm = _cache(args.facetwork.resolve()), _cache(args.snakemake.resolve())

    print(f"facetwork: {fw}")
    print(f"snakemake: {sm}\n")

    problems = compare_geojson(fw / "nuclear" / "reactors.geojson",
                               sm / "nuclear" / "reactors.geojson")
    print("GeoJSON:  " + ("OK" if not problems else "DIFFERS"))
    for p in problems:
        print("  " + p)

    html_problems, masked = compare_html(fw / "maps" / args.region / "index.html",
                                         sm / "maps" / args.region / "index.html")
    print("HTML:     " + ("OK" if not html_problems else "DIFFERS")
          + ("  (render timestamp masked)" if masked else ""))
    for p in html_problems:
        print("  " + p)

    total = problems + html_problems
    print("\n" + ("outputs agree" if not total else f"{len(total)} difference(s)"))
    return 0 if not total else 1


if __name__ == "__main__":
    sys.exit(main())
