"""Download OSM PBF files from Geofabrik into the local cache.

Geofabrik rate-limits concurrent downloads from a single IP, so this tool
processes regions sequentially with a configurable delay between files.
Each file is verified against Geofabrik's published ``.md5`` before being
promoted into the cache; a local SHA-256 is also computed and stored so
later corruption can be detected independently of upstream.

Backends (``--backend`` / ``AFL_OSM_STORAGE``):

- ``local`` (default): standard POSIX filesystem cache, atomic temp+rename.
- ``hdfs``: writes into HDFS via WebHDFS. HDFS has no advisory locking, so
  the manifest assumes single-writer semantics — run the tool from one
  coordinator process when using the HDFS backend.

Usage::

    python download_pbf.py europe/germany/berlin
    python download_pbf.py --force europe/germany/berlin
    python download_pbf.py europe/germany/berlin europe/germany/brandenburg
    python download_pbf.py --all-under europe/germany
    python download_pbf.py --backend hdfs europe/germany/berlin

Regions are Geofabrik paths relative to ``https://download.geofabrik.de/``,
*without* the ``-latest.osm.pbf`` suffix.

Cache root: ``$AFL_OSM_CACHE_ROOT`` (defaults to ``/Volumes/afl_data/osm``
for local, ``/user/afl/osm`` for hdfs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.manifest import (  # noqa: E402
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)
from _lib.storage import Storage, default_backend, get_storage  # noqa: E402

CACHE_TYPE = "pbf"
GEOFABRIK_BASE = "https://download.geofabrik.de"
GEOFABRIK_INDEX_URL = f"{GEOFABRIK_BASE}/index-v1.json"
USER_AGENT = "facetwork-osm-geocoder/1.0 (OSM PBF downloader)"
DEFAULT_DELAY_SECONDS = 1.5
CHUNK_SIZE = 1024 * 1024  # 1 MiB


def region_to_paths(region: str) -> tuple[str, str]:
    """Return ``(relative_path, remote_url)`` for a Geofabrik region key."""
    region = region.strip().strip("/")
    if not region:
        raise ValueError("Empty region")
    rel = f"{region}-latest.osm.pbf"
    url = f"{GEOFABRIK_BASE}/{rel}"
    return rel, url


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    req.get_method = lambda m=method: m
    return req


def fetch_md5(url: str) -> str:
    """Fetch Geofabrik's ``.md5`` file for ``url``; return the hex digest."""
    md5_url = url + ".md5"
    with urllib.request.urlopen(_request(md5_url), timeout=30) as resp:
        body = resp.read().decode("utf-8").strip()
    parts = body.split()
    if not parts or len(parts[0]) != 32:
        raise ValueError(f"Unexpected .md5 body from {md5_url}: {body!r}")
    return parts[0].lower()


def head_last_modified(url: str) -> str | None:
    """Best-effort HEAD for upstream ``Last-Modified``; ISO-8601 UTC or ``None``."""
    try:
        with urllib.request.urlopen(_request(url, "HEAD"), timeout=30) as resp:
            lm = resp.headers.get("Last-Modified")
    except urllib.error.URLError as exc:
        print(
            f"  HEAD {url} failed ({exc}); source_timestamp will be null",
            file=sys.stderr,
        )
        return None
    if not lm:
        return None
    try:
        dt = parsedate_to_datetime(lm)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _report_progress(
    label: str, size: int, total: int, elapsed: float, *, final: bool
) -> None:
    mib = size / (1024 * 1024)
    rate = (mib / elapsed) if elapsed > 0 else 0.0
    if total:
        pct = 100.0 * size / total
        total_mib = total / (1024 * 1024)
        msg = f"  {label}: {mib:7.1f} / {total_mib:7.1f} MiB ({pct:5.1f}%) @ {rate:5.1f} MiB/s"
    else:
        msg = f"  {label}: {mib:7.1f} MiB @ {rate:5.1f} MiB/s"
    is_tty = sys.stderr.isatty()
    if final:
        print(msg, file=sys.stderr, flush=True)
    elif is_tty:
        print(msg, end="\r", file=sys.stderr, flush=True)


def download_to_writer(url: str, writer, label: str) -> tuple[int, str, str]:
    """Stream ``url`` into ``writer`` (an open binary file handle).

    Computes SHA-256 and MD5 on the fly. Returns
    ``(size_bytes, sha256_hex, md5_hex)``.
    """
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    start = time.monotonic()
    last_report = start
    with urllib.request.urlopen(_request(url), timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
            now = time.monotonic()
            if now - last_report >= 2.0:
                _report_progress(label, size, total, now - start, final=False)
                last_report = now
    _report_progress(label, size, total or size, time.monotonic() - start, final=True)
    return size, sha.hexdigest(), md5.hexdigest().lower()


def already_cached(
    manifest: dict, rel_path: str, expected_md5: str, cache_file: str, storage: Storage
) -> bool:
    entry = manifest.get("entries", {}).get(rel_path)
    if not entry:
        return False
    if entry.get("source_checksum", {}).get("value") != expected_md5:
        return False
    if not storage.exists(cache_file):
        return False
    return storage.size(cache_file) == entry.get("size_bytes")


def download_region(
    region: str, *, storage: Storage, force: bool, dry_run: bool
) -> str:
    """Download a single region. Returns ``downloaded``, ``skipped``, or ``dry-run``."""
    rel_path, url = region_to_paths(region)
    cdir = cache_dir(CACHE_TYPE, storage)
    cache_file = Storage.join(cdir, rel_path)
    storage.mkdir_p(Storage.dirname(cache_file))

    print(f"[{region}] resolving Geofabrik metadata", file=sys.stderr)
    expected_md5 = fetch_md5(url)
    source_ts = head_last_modified(url)

    if not force:
        manifest = read_manifest(CACHE_TYPE, storage)
        if already_cached(manifest, rel_path, expected_md5, cache_file, storage):
            print(
                f"[{region}] up-to-date (md5 {expected_md5[:8]}…), skipping",
                file=sys.stderr,
            )
            return "skipped"

    if dry_run:
        print(f"[{region}] would download {url} -> {cache_file}", file=sys.stderr)
        return "dry-run"

    tmp_file = cache_file + ".tmp"
    storage.unlink(tmp_file)

    print(f"[{region}] downloading {url}", file=sys.stderr)
    try:
        writer = storage.open_write_binary(tmp_file)
        try:
            size, sha256_hex, md5_hex = download_to_writer(url, writer, region)
        finally:
            writer.close()
    except BaseException:
        storage.unlink(tmp_file)
        raise

    if md5_hex != expected_md5:
        storage.unlink(tmp_file)
        raise RuntimeError(
            f"MD5 mismatch for {region}: upstream={expected_md5} computed={md5_hex}"
        )

    storage.rename(tmp_file, cache_file)

    entry = {
        "relative_path": rel_path,
        "source_url": url,
        "size_bytes": size,
        "sha256": sha256_hex,
        "source_checksum": {
            "algo": "md5",
            "value": md5_hex,
            "url": url + ".md5",
        },
        "downloaded_at": utcnow_iso(),
        "source_timestamp": source_ts,
        "extra": {"region": region},
    }
    with manifest_transaction(CACHE_TYPE, storage) as manifest:
        manifest.setdefault("entries", {})[rel_path] = entry

    print(
        f"[{region}] done ({size / (1024 * 1024):.1f} MiB, sha256 {sha256_hex[:12]}…)",
        file=sys.stderr,
    )
    return "downloaded"


def _read_regions_file(path: Path) -> list[str]:
    regions: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        regions.append(line)
    return regions


def fetch_region_index() -> list[str]:
    """Fetch Geofabrik's ``index-v1.json`` and return all PBF region paths."""
    print(f"fetching Geofabrik index from {GEOFABRIK_INDEX_URL}", file=sys.stderr)
    with urllib.request.urlopen(_request(GEOFABRIK_INDEX_URL), timeout=60) as resp:
        data = json.load(resp)
    features = data.get("features", []) or []
    prefix = GEOFABRIK_BASE + "/"
    suffix = "-latest.osm.pbf"
    seen: set[str] = set()
    regions: list[str] = []
    for feat in features:
        urls = (feat.get("properties") or {}).get("urls") or {}
        pbf_url = urls.get("pbf")
        if not pbf_url:
            continue
        if not pbf_url.startswith(prefix) or not pbf_url.endswith(suffix):
            continue
        path = pbf_url[len(prefix):-len(suffix)]
        if path in seen:
            continue
        seen.add(path)
        regions.append(path)
    regions.sort()
    return regions


def filter_regions(
    all_regions: list[str], *, under: str | None, leaves_only: bool
) -> list[str]:
    """Filter ``all_regions`` by prefix and optionally drop parent regions."""
    under = (under or "").strip().strip("/")
    if under:
        pref = under + "/"
        selected = [r for r in all_regions if r == under or r.startswith(pref)]
    else:
        selected = list(all_regions)

    if not leaves_only:
        return selected

    selected_set = set(selected)
    non_leaves: set[str] = set()
    for r in selected:
        parts = r.split("/")
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            if ancestor in selected_set:
                non_leaves.add(ancestor)
    return [r for r in selected if r not in non_leaves]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download OSM PBF files from Geofabrik into the local cache.",
    )
    parser.add_argument(
        "regions",
        nargs="*",
        help="Geofabrik region keys, e.g. europe/germany/berlin",
    )
    parser.add_argument(
        "--regions-file",
        type=Path,
        help="Read regions from a file (one per line; '#' comments allowed).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download every region listed in the Geofabrik index. "
        "Combine with --include-parents to also fetch continent/country-level PBFs.",
    )
    parser.add_argument(
        "--all-under",
        metavar="PREFIX",
        help="Download every region nested under PREFIX, e.g. europe/germany.",
    )
    parser.add_argument(
        "--include-parents",
        action="store_true",
        help="When using --all / --all-under, include parent regions alongside "
        "their descendants. Default is leaves-only (parent PBFs already "
        "contain their children, so downloading both is wasteful).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the resolved region list to stdout and exit; no downloads.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the manifest reports an up-to-date cached copy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without fetching the PBF body.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Seconds to sleep between downloads (default: {DEFAULT_DELAY_SECONDS}).",
    )
    parser.add_argument(
        "--backend",
        choices=("local", "hdfs"),
        default=default_backend(),
        help="Storage backend for the cache "
        "(default: $AFL_OSM_STORAGE or 'local'). HDFS assumes single-writer "
        "semantics (no advisory locking).",
    )
    args = parser.parse_args()

    try:
        storage = get_storage(args.backend)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"storage backend: {storage.name}", file=sys.stderr)

    regions: list[str] = list(args.regions)
    if args.regions_file:
        regions.extend(_read_regions_file(args.regions_file))

    if args.all or args.all_under is not None:
        index = fetch_region_index()
        resolved = filter_regions(
            index,
            under=args.all_under,
            leaves_only=not args.include_parents,
        )
        print(
            f"Geofabrik index: {len(index)} total regions, "
            f"{len(resolved)} selected after filtering",
            file=sys.stderr,
        )
        regions.extend(resolved)

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

    results = {"downloaded": 0, "skipped": 0, "dry-run": 0, "failed": 0}
    failures: list[tuple[str, str]] = []

    for i, region in enumerate(regions):
        if i > 0 and not args.dry_run:
            time.sleep(args.delay)
        try:
            outcome = download_region(
                region, storage=storage, force=args.force, dry_run=args.dry_run
            )
            results[outcome] += 1
        except Exception as exc:  # noqa: BLE001
            results["failed"] += 1
            failures.append((region, str(exc)))
            print(f"[{region}] FAILED: {exc}", file=sys.stderr)

    print(
        f"\nSummary: {results['downloaded']} downloaded, "
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
