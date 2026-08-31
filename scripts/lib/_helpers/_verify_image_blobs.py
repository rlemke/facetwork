#!/usr/bin/env python3
"""Prove a pushed image tag is actually PULLABLE, not merely listed.

⚠️ A manifest that lists the right platforms is not enough. On 2026-08-31 the
index for a multi-arch tag listed amd64 and arm64 while 12 of the 24 amd64 blobs
had no data behind them, so every pull died with
``short read … unexpected EOF`` — on the registry host itself, not just remotes.

Cause: ``registry garbage-collect`` was run against a LIVE registry (the docs
require it stopped or read-only) and the registry was not restarted afterwards.
GC removed the blob data while the process kept answering ``HEAD 200`` from its
``blobdescriptor: inmemory`` cache, so buildx's pre-push existence check was told
the layers were already there and skipped uploading them.

That is why this checks with a ONE-BYTE RANGED GET rather than a HEAD: a HEAD is
answered from metadata (and, in that failure, from a stale cache), while a ranged
GET forces the registry to open the data file. Cost is bytes, not gigabytes.

Exit 0 = every blob has data; 1 = some do not; 2 = could not tell.
"""
from __future__ import annotations

import json
import sys
import urllib.request

ACCEPT = ("application/vnd.oci.image.index.v1+json, "
          "application/vnd.docker.distribution.manifest.list.v2+json, "
          "application/vnd.oci.image.manifest.v1+json, "
          "application/vnd.docker.distribution.manifest.v2+json")


def _json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"Accept": ACCEPT})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def verify(registry: str, repo: str, tag: str, want_platforms: list[str]):
    base = f"http://{registry}/v2/{repo}/"
    top = _json(base + "manifests/" + tag)
    # A single-arch push has no "manifests" list; treat the tag itself as the one.
    entries = top.get("manifests") or [{"digest": tag, "platform": {
        "architecture": top.get("architecture", "unknown")}}]
    archs, missing = [], []
    for ent in entries:
        arch = (ent.get("platform") or {}).get("architecture", "unknown")
        archs.append(arch)
        man = _json(base + "manifests/" + ent["digest"])
        for blob in [man["config"]] + list(man.get("layers") or []):
            req = urllib.request.Request(base + "blobs/" + blob["digest"],
                                         headers={"Range": "bytes=0-0"})
            try:
                urllib.request.urlopen(req, timeout=30).read()
            except Exception:
                missing.append((arch, blob["digest"][:19], blob.get("size", 0)))
    archs = sorted(set(archs))
    for want in want_platforms:
        short = want.split("/")[-1]
        if short not in archs:
            print(f"MISSING PLATFORM {want} (has: {' '.join(archs)})")
            return 1
    if missing:
        print(f"{len(missing)} blob(s) have NO DATA behind them:")
        for arch, dig, size in missing[:5]:
            print(f"    {arch:6} {dig}… {size/1e6:8.1f} MB")
        return 1
    print(f"OK {' '.join(archs)}")
    return 0


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: _verify_image_blobs.py REGISTRY REPO TAG [platform,...]")
        return 2
    plats = [p for p in (sys.argv[4].split(",") if len(sys.argv) > 4 else []) if p]
    try:
        return verify(sys.argv[1], sys.argv[2], sys.argv[3], plats)
    except Exception as exc:  # noqa: BLE001 — cannot tell is NOT a failure verdict
        print(f"could not verify ({type(exc).__name__}: {exc})")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
