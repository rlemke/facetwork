#!/usr/bin/env python3
"""Compare the outputs of two or more engine runs, artifact by artifact.

    python compare_outputs.py --run facetwork=runs/facetwork \
                              --run snakemake=runs/snakemake \
                              --run nextflow=runs/nextflow

Every run is compared against the FIRST one given (the reference — normally
Facetwork, since the others are reproductions of it). Exits 0 when they all
agree, 1 otherwise, so it can gate a CI check.

Two things must be normalised or every comparison fails for uninteresting
reasons:

* **The render timestamp.** map_render stamps "generated at <UTC minute>" into
  the HTML, so two runs a minute apart always differ. It is masked, and the
  masking is REPORTED so nobody mistakes this for a byte-identical result.
* **GeoJSON feature order.** Nothing in the pipeline guarantees a stable order
  across two independent Overpass responses, and order carries no meaning for a
  map. Features are compared as a set of canonical digests.

What is deliberately NOT normalised is feature content: if two runs disagree
about a reactor's tags, that is a real difference and must show up.

A caveat this tool cannot fix: live runs query Overpass at different moments, so
a genuine upstream edit between them is indistinguishable from a pipeline
difference. For a strict equality check run every engine with mock data, which
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


def compare_geojson(ref: Path, other: Path, ref_label: str, label: str) -> list[str]:
    for p in (ref, other):
        if not p.exists():
            return [f"missing GeoJSON: {p}"]

    feats_ref = json.loads(ref.read_text()).get("features", [])
    feats_oth = json.loads(other.read_text()).get("features", [])

    problems: list[str] = []
    if len(feats_ref) != len(feats_oth):
        problems.append(
            f"feature count differs: {ref_label}={len(feats_ref):,} {label}={len(feats_oth):,}"
        )

    d_ref = {_feature_digest(f) for f in feats_ref}
    d_oth = {_feature_digest(f) for f in feats_oth}
    only_ref, only_oth = d_ref - d_oth, d_oth - d_ref
    if only_ref or only_oth:
        problems.append(
            f"feature content differs: {len(only_ref)} only in {ref_label}, "
            f"{len(only_oth)} only in {label}"
        )
        # A couple of examples make an upstream edit recognisable at a glance.
        for lbl, feats, only in ((ref_label, feats_ref, only_ref), (label, feats_oth, only_oth)):
            for f in feats:
                if _feature_digest(f) in only:
                    name = (f.get("properties") or {}).get("name", "<unnamed>")
                    problems.append(f"    e.g. only in {lbl}: {name}")
                    break
    return problems


def compare_html(ref: Path, other: Path, ref_label: str, label: str) -> tuple[list[str], bool]:
    """Compare rendered maps. Returns (problems, timestamp_was_masked)."""
    for p in (ref, other):
        if not p.exists():
            return [f"missing HTML: {p}"], False

    h_ref, h_oth = ref.read_text(), other.read_text()
    masked = bool(TIMESTAMP_RE.search(h_ref) or TIMESTAMP_RE.search(h_oth))
    n_ref, n_oth = TIMESTAMP_RE.sub("<TS>", h_ref), TIMESTAMP_RE.sub("<TS>", h_oth)

    if n_ref == n_oth:
        return [], masked

    problems = [f"HTML differs: {ref_label}={len(h_ref):,}B {label}={len(h_oth):,}B"]
    for i, (a, b) in enumerate(zip(n_ref.splitlines(), n_oth.splitlines()), 1):
        if a != b:
            problems.append(f"    first difference at line {i}:")
            problems.append(f"      {ref_label}: {a[:150]}")
            problems.append(f"      {label}: {b[:150]}")
            break
    else:
        problems.append(
            f"    identical prefix; line count differs "
            f"({len(n_ref.splitlines())} vs {len(n_oth.splitlines())})"
        )
    return problems, masked


def _parse_run(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"expected label=path, got {spec!r}")
    label, path = spec.split("=", 1)
    return label, Path(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="append", type=_parse_run, default=[], metavar="LABEL=PATH",
                    help="a run to compare; the first is the reference (repeatable)")
    ap.add_argument("--region", default="nuclear")
    args = ap.parse_args()

    if len(args.run) < 2:
        ap.error("need at least two --run arguments to compare")

    (ref_label, ref_root), others = args.run[0], args.run[1:]
    ref = _cache(ref_root.resolve())
    print(f"reference: {ref_label}  {ref}\n")

    total = 0
    for label, root in others:
        oth = _cache(root.resolve())
        geo = compare_geojson(ref / "nuclear" / "reactors.geojson",
                              oth / "nuclear" / "reactors.geojson", ref_label, label)
        html, masked = compare_html(ref / "maps" / args.region / "index.html",
                                    oth / "maps" / args.region / "index.html", ref_label, label)
        print(f"{label} vs {ref_label}")
        print("  GeoJSON:  " + ("OK" if not geo else "DIFFERS"))
        for p in geo:
            print("    " + p)
        print("  HTML:     " + ("OK" if not html else "DIFFERS")
              + ("  (render timestamp masked)" if masked else ""))
        for p in html:
            print("    " + p)
        total += len(geo) + len(html)

    print("\n" + ("all outputs agree" if not total else f"{total} difference(s)"))
    return 0 if not total else 1


if __name__ == "__main__":
    sys.exit(main())
