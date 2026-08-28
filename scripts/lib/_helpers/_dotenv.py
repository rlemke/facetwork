"""Load the repo's .env for python `fw` commands, matching _env.sh semantics.

⚠️ A python command under scripts/lib/ is exec'd DIRECTLY by `fw`; unlike a bash
command it never sources _env.sh, so it does not see .env. That is not cosmetic
here: .env sets FW_MONGODB_URL, and without it a tool falls back to
`mongodb://localhost:27017` - which on a fleet host is a DIFFERENT MongoDB from
the fleet's. Measured 2026-08-28: `afl-mongodb` reached a container with 2
running runners, while `localhost` reached another with 0 running and a
different set of dead letters. A diagnostic that quietly reads the wrong world
is worse than one that does not run.

Like _env.sh, values already in the environment WIN - an explicit
`FW_MONGODB_URL=... fw maint ...` must still override the file.
"""
from __future__ import annotations

import os
import pathlib


def load_env(start: str | pathlib.Path | None = None) -> pathlib.Path | None:
    """Apply FW_ROOT/.env without overriding what is already set."""
    here = pathlib.Path(start or __file__).resolve()
    root = None
    for parent in [here, *here.parents]:
        if (parent / ".env").is_file() and (parent / "fw").exists():
            root = parent
            break
    if root is None:
        return None
    for raw in (root / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
    return root
