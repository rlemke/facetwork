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

import base64
import json
import subprocess
import sys
from pathlib import Path

CV_EXTRAS = {"detect", "enhance", "matte"}  # markers of heavy CV (torch/opencv) domains
# Optional buildx secret (`--secret id=gh_token,...`), used ONLY to retry a clone
# that failed anonymously. A private domain repo is otherwise indistinguishable
# from a deleted one: `git clone` returns 128 either way, this script's
# continue-on-error skips it, and the build SUCCEEDS without it. That is how
# fwh_unimatch shipped missing from an image whose rollout reported success.
GH_TOKEN_FILE = Path("/run/secrets/gh_token")
ALREADY_BAKED = {"osm-geocoder"}  # baked earlier with system deps
BAKED_LIST = Path("/etc/afl-baked-domains")


def _read_token() -> str | None:
    """The build secret, if one was mounted. Never logged."""
    try:
        tok = GH_TOKEN_FILE.read_text().strip()
    except OSError:
        return None
    return tok or None


def _clone(repo: str, dest: str, token: str | None) -> None:
    """Clone ``repo``, retrying with the token only if anonymous access fails.

    Anonymous first so a public repo never sees the credential, and so behaviour
    is identical on build hosts that have no token.

    The token travels as an ``http.extraHeader`` set with ``git -c`` rather than
    embedded in the URL: ``-c`` values are NOT written to ``.git/config``, so the
    credential cannot survive into a layer even if a later step fails before the
    ``rm -rf .git`` below.
    """
    url = f"https://github.com/rlemke/{repo}.git"
    try:
        subprocess.run(["git", "clone", "--depth", "1", url, dest],
                       check=True, capture_output=True, text=True)
        return
    except subprocess.CalledProcessError:
        if not token:
            raise
    # Leave nothing half-cloned behind for the retry to trip over.
    subprocess.run(["rm", "-rf", dest], check=False)
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    subprocess.run(
        ["git", "-c", f"http.extraHeader=Authorization: Basic {auth}",
         "clone", "--depth", "1", url, dest],
        check=True, capture_output=True, text=True,
    )
    print(f"  (used the build secret for {repo})", flush=True)


def main() -> int:
    catalog = json.loads(Path("domains.json").read_text())
    domains = catalog.get("domains", catalog)
    token = _read_token()
    print(f"  build secret for private repos: {'present' if token else 'absent'}", flush=True)
    baked: list[str] = []
    failed: list[str] = []
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
        # `repo` is a NAME, not a URL -- it is interpolated into both a clone URL
        # and a filesystem path. A full URL here yields
        # https://github.com/rlemke/https://github.com/... and a /opt/https:/...
        # path, failing as rc=128, which reads as "repo missing or private" and
        # sent the first investigation looking at GitHub permissions. Say what is
        # actually wrong. (road-safety shipped like this and nobody could see it.)
        if "/" in repo or ":" in repo:
            print(
                f"  WARN: could not bake {name}: domains.json `repo` must be a bare "
                f"repository NAME, got {repo!r} — falls back to bind-mount",
                file=sys.stderr, flush=True,
            )
            failed.append(name)
            continue
        dest = f"/opt/{repo}"
        spec = f"{dest}[{','.join(extras)}]" if extras else dest
        try:
            _clone(repo, dest, token)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-e", spec], check=True
            )
            subprocess.run(
                ["sh", "-c", f"cd {dest} && git rev-parse HEAD > {dest}.commit && rm -rf .git"],
                check=True,
            )
            baked.append(name)
            print(
                f"  baked {name} ({repo}) @ {Path(dest + '.commit').read_text().strip()[:12]}",
                flush=True,
            )
        except subprocess.CalledProcessError as e:
            failed.append(name)
            hint = "" if token else " (no build secret mounted — private repo?)"
            print(
                f"  WARN: could not bake {name} ({repo}): rc={e.returncode}{hint}"
                " — falls back to bind-mount",
                file=sys.stderr,
                flush=True,
            )

    existing = BAKED_LIST.read_text().split() if BAKED_LIST.exists() else []
    all_baked = sorted(set(existing) | set(baked))
    BAKED_LIST.write_text("\n".join(all_baked) + "\n")
    print(f"baked domains ({len(all_baked)}): {' '.join(all_baked)}", flush=True)
    # Record the misses IN THE IMAGE. Continue-on-error is deliberate -- one bad
    # repo must not fail a 30-domain build -- but "skipped" was previously visible
    # only as a WARN buried in build output, so an image could ship without a
    # domain and its rollout still report success. Written here, `fw fleet status`
    # and anyone in a container can ask what is actually missing.
    if failed:
        Path("/etc/afl-bake-failures").write_text("\n".join(sorted(failed)) + "\n")
        print(
            f"NOT BAKED ({len(failed)}): {' '.join(sorted(failed))} — these domains "
            "are absent from this image and NO fleet runner can claim their work",
            file=sys.stderr,
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
