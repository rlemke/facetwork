#!/usr/bin/env python3
"""Build-time: clone + ``pip install -e`` every fleet DATA domain so the runner
container entrypoint can SKIP the per-start ``pip install`` (the dominant cold-
start cost, ~200x worse under emulation × concurrent seed). Domains listed in
``/etc/afl-baked-domains`` are treated as baked by the entrypoint (it ignores any
bind-mount and just runs).

Reads the domain catalog (``domains.json`` in the build context). Excludes:
  * osm-geocoder — already baked earlier in the Dockerfile (with its system deps).
  * CV domains (peloton/groupphoto) — heavy torch/opencv extras, not fleet
    runners; baking them would bloat the image for no fleet benefit.
Continue-on-error: a domain that fails to clone/install is skipped (it falls
back to the entrypoint's bind-mount path), so one bad repo can't fail the build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CV_EXTRAS = {"detect", "enhance", "matte"}  # markers of heavy CV (torch/opencv) domains
ALREADY_BAKED = {"osm-geocoder"}  # baked earlier with system deps
BAKED_LIST = Path("/etc/afl-baked-domains")


def main() -> int:
    catalog = json.loads(Path("domains.json").read_text())
    domains = catalog.get("domains", catalog)
    baked: list[str] = []
    for name, d in domains.items():
        if not isinstance(d, dict):
            continue
        repo = d.get("repo")
        if not repo or name in ALREADY_BAKED:
            continue
        extras = list(d.get("extras") or [])
        if set(extras) & CV_EXTRAS:
            print(f"  skip {name}: CV domain ({extras})", flush=True)
            continue
        dest = f"/opt/{repo}"
        spec = f"{dest}[{','.join(extras)}]" if extras else dest
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", f"https://github.com/rlemke/{repo}.git", dest],
                check=True,
            )
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-e", spec], check=True
            )
            subprocess.run(
                ["sh", "-c", f"cd {dest} && git rev-parse HEAD > {dest}.commit && rm -rf .git"],
                check=True,
            )
            baked.append(name)
            print(f"  baked {name} ({repo}) @ {Path(dest + '.commit').read_text().strip()[:12]}",
                  flush=True)
        except subprocess.CalledProcessError as e:
            print(f"  WARN: could not bake {name} ({repo}): {e} — falls back to bind-mount",
                  file=sys.stderr, flush=True)

    existing = BAKED_LIST.read_text().split() if BAKED_LIST.exists() else []
    all_baked = sorted(set(existing) | set(baked))
    BAKED_LIST.write_text("\n".join(all_baked) + "\n")
    print(f"baked domains ({len(all_baked)}): {' '.join(all_baked)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
