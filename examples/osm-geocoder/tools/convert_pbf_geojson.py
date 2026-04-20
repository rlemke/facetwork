"""Convert cached OSM PBF files to GeoJSON using ``osmium export``.

Reads from the ``pbf`` cache and writes to the ``geojson`` cache. The
geojson manifest records the source PBF's SHA-256 (and related metadata),
so re-running the tool skips regions whose source PBF has not changed
since the last conversion. Pass ``--force`` to reconvert unconditionally.

Conversions can run in parallel (``--jobs N``); each worker spawns an
``osmium`` subprocess. The manifest update is serialized — both across
processes (advisory file lock) and across threads within this process
(in-process lock) — so concurrent writers never corrupt the index.

Usage::

    python convert_pbf_geojson.py europe/germany/berlin
    python convert_pbf_geojson.py --all --jobs 4
    python convert_pbf_geojson.py --all-under europe/germany --format geojson

Requires the ``osmium`` command-line tool (``osmium-tool``) on ``PATH``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.manifest import (  # noqa: E402
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)

SOURCE_CACHE_TYPE = "pbf"
OUTPUT_CACHE_TYPE = "geojson"
DEFAULT_JOBS = 2
DEFAULT_FORMAT = "geojsonseq"
FORMAT_EXT = {"geojson": "geojson", "geojsonseq": "geojsonseq"}
CHUNK_SIZE = 1024 * 1024

# In-process lock for manifest updates. flock alone doesn't reliably
# serialize concurrent threads inside the same process on all platforms.
_MANIFEST_LOCK = threading.Lock()


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return cache_dir(SOURCE_CACHE_TYPE) / pbf_rel_path(region)


def geojson_rel_path(region: str, fmt: str) -> str:
    return f"{region}-latest.{FORMAT_EXT[fmt]}"


def geojson_abs_path(region: str, fmt: str) -> Path:
    return cache_dir(OUTPUT_CACHE_TYPE) / geojson_rel_path(region, fmt)


def _osmium_version(osmium_bin: str) -> str:
    try:
        result = subprocess.run(
            [osmium_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (result.stdout or "").splitlines()
        return first_line[0].strip() if first_line else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _sha256_file(path: Path) -> tuple[int, str]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            size += len(chunk)
    return size, sha.hexdigest()


def _read_regions_file(path: Path) -> list[str]:
    regions: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        regions.append(line)
    return regions


def regions_from_pbf_manifest(under: str | None = None) -> list[str]:
    """Return all regions currently present in the pbf manifest, optionally filtered by prefix."""
    manifest = read_manifest(SOURCE_CACHE_TYPE)
    suffix = "-latest.osm.pbf"
    regions: list[str] = []
    for rel in manifest.get("entries", {}):
        if not rel.endswith(suffix):
            continue
        region = rel[: -len(suffix)]
        regions.append(region)
    if under:
        under = under.strip().strip("/")
        pref = under + "/"
        regions = [r for r in regions if r == under or r.startswith(pref)]
    regions.sort()
    return regions


def is_up_to_date(
    region: str, fmt: str, pbf_entry: dict, out_abs: Path
) -> bool:
    """Check whether an existing GeoJSON is still valid for the current PBF."""
    geo_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    out_rel = geojson_rel_path(region, fmt)
    existing = geo_manifest.get("entries", {}).get(out_rel)
    if not existing:
        return False
    if existing.get("format") != fmt:
        return False
    if existing.get("source", {}).get("sha256") != pbf_entry.get("sha256"):
        return False
    if not out_abs.exists():
        return False
    return out_abs.stat().st_size == existing.get("size_bytes")


def convert_region(
    region: str,
    *,
    fmt: str,
    force: bool,
    dry_run: bool,
    osmium_bin: str,
) -> str:
    """Convert one region. Returns ``converted``, ``skipped``, or ``dry-run``."""
    pbf_manifest = read_manifest(SOURCE_CACHE_TYPE)
    pbf_rel = pbf_rel_path(region)
    pbf_entry = pbf_manifest.get("entries", {}).get(pbf_rel)
    if not pbf_entry:
        raise RuntimeError(
            f"no pbf manifest entry for {region!r}; run download-pbf first"
        )

    src_pbf = pbf_abs_path(region)
    if not src_pbf.exists():
        raise RuntimeError(f"pbf file missing on disk: {src_pbf}")

    out_abs = geojson_abs_path(region, fmt)
    out_rel = geojson_rel_path(region, fmt)

    if not force and is_up_to_date(region, fmt, pbf_entry, out_abs):
        print(f"[{region}] up-to-date, skipping", file=sys.stderr)
        return "skipped"

    if dry_run:
        print(f"[{region}] would convert {src_pbf} -> {out_abs}", file=sys.stderr)
        return "dry-run"

    out_abs.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_abs.with_name(out_abs.name + ".tmp")
    tmp.unlink(missing_ok=True)

    print(f"[{region}] converting -> {out_abs.name}", file=sys.stderr)
    cmd = [
        osmium_bin,
        "export",
        "-f",
        fmt,
        "-o",
        str(tmp),
        "--overwrite",
        str(src_pbf),
    ]
    start = time.monotonic()
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"osmium export failed: {stderr or exc}") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    elapsed = time.monotonic() - start

    size, sha256_hex = _sha256_file(tmp)
    os.replace(tmp, out_abs)

    entry = {
        "relative_path": out_rel,
        "format": fmt,
        "size_bytes": size,
        "sha256": sha256_hex,
        "generated_at": utcnow_iso(),
        "duration_seconds": round(elapsed, 2),
        "source": {
            "cache_type": SOURCE_CACHE_TYPE,
            "relative_path": pbf_rel,
            "sha256": pbf_entry.get("sha256"),
            "size_bytes": pbf_entry.get("size_bytes"),
            "source_checksum": pbf_entry.get("source_checksum"),
            "source_timestamp": pbf_entry.get("source_timestamp"),
            "downloaded_at": pbf_entry.get("downloaded_at"),
        },
        "tool": {
            "command": "osmium export",
            "osmium_version": _osmium_version(osmium_bin),
        },
        "extra": {"region": region},
    }
    with _MANIFEST_LOCK, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
        manifest.setdefault("entries", {})[out_rel] = entry

    print(
        f"[{region}] done ({size / (1024 * 1024):.1f} MiB in {elapsed:.1f}s)",
        file=sys.stderr,
    )
    return "converted"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert cached OSM PBF files to GeoJSON using osmium export.",
    )
    parser.add_argument(
        "regions",
        nargs="*",
        help="Region keys to convert, e.g. europe/germany/berlin. "
        "Must already be present in the pbf cache.",
    )
    parser.add_argument(
        "--regions-file",
        type=Path,
        help="Read regions from a file (one per line; '#' comments allowed).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Convert every region currently in the pbf manifest.",
    )
    parser.add_argument(
        "--all-under",
        metavar="PREFIX",
        help="Convert every cached PBF whose region path starts with PREFIX.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the resolved region list to stdout and exit; no conversions.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even when the existing GeoJSON matches the source PBF's SHA-256.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be converted without running osmium.",
    )
    parser.add_argument(
        "--format",
        choices=sorted(FORMAT_EXT.keys()),
        default=DEFAULT_FORMAT,
        help=f"Output format (default: {DEFAULT_FORMAT}). "
        "'geojson' is a FeatureCollection; 'geojsonseq' is one feature per line (streamable).",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Number of concurrent conversions (default: {DEFAULT_JOBS}). "
        "Each job spawns an osmium subprocess.",
    )
    parser.add_argument(
        "--osmium",
        default="osmium",
        help="Path to the osmium binary (default: 'osmium' on PATH).",
    )
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be >= 1")

    osmium_bin = args.osmium
    if shutil.which(osmium_bin) is None and not Path(osmium_bin).is_file():
        print(
            f"error: osmium binary not found ({osmium_bin!r}). "
            "Install osmium-tool (e.g. 'brew install osmium-tool' or "
            "'apt install osmium-tool').",
            file=sys.stderr,
        )
        return 2

    regions: list[str] = list(args.regions)
    if args.regions_file:
        regions.extend(_read_regions_file(args.regions_file))

    if args.all or args.all_under is not None:
        from_manifest = regions_from_pbf_manifest(under=args.all_under)
        print(
            f"pbf manifest: {len(from_manifest)} region(s) selected after filtering",
            file=sys.stderr,
        )
        regions.extend(from_manifest)

    seen: set[str] = set()
    deduped: list[str] = []
    for r in regions:
        if r and r not in seen:
            seen.add(r)
            deduped.append(r)
    regions = deduped

    if not regions:
        parser.error(
            "no regions provided (pass as args, or use --regions-file, "
            "--all, or --all-under)"
        )

    if args.list:
        for r in regions:
            print(r)
        print(f"{len(regions)} region(s)", file=sys.stderr)
        return 0

    results = {"converted": 0, "skipped": 0, "dry-run": 0, "failed": 0}
    failures: list[tuple[str, str]] = []

    def _run(region: str) -> tuple[str, str | None]:
        try:
            outcome = convert_region(
                region,
                fmt=args.format,
                force=args.force,
                dry_run=args.dry_run,
                osmium_bin=osmium_bin,
            )
            return outcome, None
        except Exception as exc:  # noqa: BLE001
            return "failed", str(exc)

    if args.jobs == 1 or len(regions) == 1:
        for region in regions:
            outcome, err = _run(region)
            if err:
                failures.append((region, err))
                print(f"[{region}] FAILED: {err}", file=sys.stderr)
            results[outcome] += 1
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(_run, r): r for r in regions}
            for fut in concurrent.futures.as_completed(futures):
                region = futures[fut]
                outcome, err = fut.result()
                if err:
                    failures.append((region, err))
                    print(f"[{region}] FAILED: {err}", file=sys.stderr)
                results[outcome] += 1

    print(
        f"\nSummary: {results['converted']} converted, "
        f"{results['skipped']} skipped, "
        f"{results['dry-run']} dry-run, "
        f"{results['failed']} failed",
        file=sys.stderr,
    )
    if failures:
        for region, msg in failures:
            print(f"  - {region}: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
