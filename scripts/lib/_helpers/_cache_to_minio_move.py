#!/usr/bin/env python3
"""One-off: MOVE the local OSM/etc cache tree into the bundled MinIO bucket.

Driven from the HOST (not through Docker's virtiofs bind mount, which is
unreliable for deep recursion over the external APFS disk and caused short-read
"Content-Length" failures). Reads each file from the host filesystem, uploads it
to s3://afl-cache/cache/<relpath>, verifies the object size, then deletes the
local source -- a true streaming move. Idempotent and restart-safe: a file
already present in the bucket with a matching size is treated as moved and its
local copy is removed without re-uploading.

    /Volumes/afl_data/cache/<X>  ->  s3://afl-cache/cache/<X>
"""

import os
import sys
import time

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

SRC_ROOT = os.environ.get("SRC_ROOT", "/Volumes/afl_data/cache")
KEY_PREFIX = "cache"  # FW_DATA_ROOT/cache/...  (handlers root durable artifacts here)
BUCKET = os.environ.get("FW_S3_BUCKET", "afl-cache")
ENDPOINT = os.environ.get("FW_S3_ENDPOINT", "http://localhost:9000")
ACCESS = os.environ.get("FW_S3_ACCESS_KEY", "minioadmin")
SECRET = os.environ.get("FW_S3_SECRET_KEY", "minioadmin")
MAX_RETRIES = 5

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS,
    aws_secret_access_key=SECRET,
    region_name=os.environ.get("FW_S3_REGION", "us-east-1"),
    config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
)
# On a USB spinning disk, concurrent part reads interleave seeks and can
# short-read -> MinIO rejects the part with IncompleteBody. Set
# XFER_MAX_CONCURRENCY=1 to serialize reads for the flaky large files.
_conc = int(os.environ.get("XFER_MAX_CONCURRENCY", "4") or 4)
_chunk_mb = int(os.environ.get("XFER_CHUNK_MB", "64") or 64)
xfer = TransferConfig(
    multipart_threshold=_chunk_mb * 1024 * 1024,
    multipart_chunksize=_chunk_mb * 1024 * 1024,
    max_concurrency=_conc,
    use_threads=_conc > 1,
)


def head_size(key):
    try:
        return s3.head_object(Bucket=BUCKET, Key=key)["ContentLength"]
    except Exception:
        return None


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def remove_quiet(path):
    """Delete source; a missing file just means it was already moved."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def main():
    # Enumerate everything still on the host (the reliable side).
    raw = []
    for dirpath, _dirs, names in os.walk(SRC_ROOT):
        for n in names:
            if n == ".DS_Store":
                continue
            raw.append(os.path.join(dirpath, n))
    # Smallest-first: move the cheap files immediately and defer the giant
    # regenerable .geojsonseq dumps to last, so a late "skip the giants"
    # decision wastes almost nothing. Optional SKIP_LARGER_THAN_GB leaves
    # files above the cap in place (for the skip-intermediates scope).
    skip_gb = float(os.environ.get("SKIP_LARGER_THAN_GB", "0") or 0)
    # Skip any source path containing this substring (e.g. "osm/geojson/" to
    # leave the regenerable whole-region geojsonseq dumps on the external disk).
    skip_substr = os.environ.get("SKIP_PATH_SUBSTR", "") or ""
    sized = []
    for f in raw:
        try:
            sized.append((os.path.getsize(f), f))
        except OSError:
            sized.append((0, f))
    sized.sort(key=lambda t: t[0])
    deferred = 0
    files = []
    for sz, f in sized:
        if skip_gb and sz > skip_gb * 1e9:
            deferred += 1
            continue
        if skip_substr and skip_substr in f:
            deferred += 1
            continue
        files.append(f)
    total = len(files)

    def _kept(s, f):
        if skip_gb and s > skip_gb * 1e9:
            return False
        if skip_substr and skip_substr in f:
            return False
        return True

    total_bytes = sum(s for s, f in sized if _kept(s, f))
    log(
        f"=== MOVE START: {total} files, {total_bytes / 1e9:.1f} GB under {SRC_ROOT} "
        f"(smallest-first; deferred={deferred}"
        f"{', skip_substr=' + skip_substr if skip_substr else ''}"
        f"{', skip>' + str(skip_gb) + 'GB' if skip_gb else ''}) ==="
    )

    moved = skipped = failed = 0
    moved_bytes = 0
    failures = []
    for i, path in enumerate(files, 1):
        if not os.path.exists(path):
            continue
        rel = os.path.relpath(path, SRC_ROOT)
        key = f"{KEY_PREFIX}/{rel}"
        try:
            size = os.path.getsize(path)
        except OSError as e:
            log(f"!! stat failed {path}: {e}")
            failed += 1
            failures.append((path, f"stat: {e}"))
            continue

        # Already moved? (restart / pass-1 leftovers)
        existing = head_size(key)
        if existing == size:
            remove_quiet(path)
            skipped += 1
            moved_bytes += size
            if i % 500 == 0:
                log(f"  [{i}/{total}] skip(existing)+rm {rel}")
            continue

        ok = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                s3.upload_file(path, BUCKET, key, Config=xfer)
                if head_size(key) != size:
                    raise OSError("post-upload size mismatch")
                ok = True
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    log(f"!! FAILED {rel} after {attempt} tries: {e}")
                    failures.append((path, str(e)))
                else:
                    time.sleep(2 * attempt)
        if not ok:
            failed += 1
            continue

        remove_quiet(path)  # true move: drop source only after verified upload
        moved += 1
        moved_bytes += size
        if moved % 200 == 0 or size > 1 * 1024**3:
            log(
                f"  [{i}/{total}] moved {moved_bytes / 1e9:.1f}/{total_bytes / 1e9:.1f} GB :: {rel}"
            )

    log(
        f"=== MOVE DONE: moved={moved} skipped_existing={skipped} failed={failed} "
        f"bytes={moved_bytes / 1e9:.1f} GB ==="
    )
    if failures:
        log(f"=== {len(failures)} FAILURES (first 20) ===")
        for p, e in failures[:20]:
            log(f"  FAIL {p}: {e}")
        sys.exit(2)
    log("=== ALL MOVED CLEAN ===")


if __name__ == "__main__":
    main()
