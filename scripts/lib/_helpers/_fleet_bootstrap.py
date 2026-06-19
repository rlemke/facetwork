# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Self-bootstrap for the fleet CLIs (`fleet`, `fleet-agent`).

So a fresh server can run `fleet-agent watch` with no manual pip steps, this
checks for a proper Python venv with the tools the fleet tooling needs
(``pymongo``, plus ``cryptography`` for the encrypted secret store) and
**installs them if missing** — creating the repo ``.venv`` first if there isn't
one. Stdlib-only: it runs *before* those third-party deps exist.

Usage (in a CLI's import-guard, before importing pymongo)::

    try:
        import pymongo  # noqa: F401
    except ModuleNotFoundError:
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _fleet_bootstrap import ensure_venv_and_deps
        _py = ensure_venv_and_deps()
        if os.path.realpath(_py) != os.path.realpath(sys.executable):
            os.execv(_py, [_py, *sys.argv])   # re-exec under the venv
        import pymongo  # noqa: F401          # else deps were just installed here
"""

from __future__ import annotations

import os
import subprocess
import sys

# Tools the fleet CLIs need. pymongo: read/write fleet_config; cryptography:
# decrypt the MinIO secret store (AFL_FLEET_KEY). boto3 is NOT needed here (the
# runner containers handle S3); add via the repo extras if you want it.
REQUIRED = ["pymongo", "cryptography"]

# Minimum Python for the fleet deps (pymongo 4.x requires 3.7+; keep a margin).
MIN_PY = (3, 8)


def _py_version(py: str) -> tuple[int, int] | None:
    """(major, minor) of interpreter *py*, or None if it can't be determined."""
    out = subprocess.run(
        [py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    try:
        major, minor = out.stdout.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return None


def _repo_root() -> str:
    # This file lives at scripts/lib/_helpers/; fwroot resolves the true repo
    # root robustly (git toplevel / sentinel walk) regardless of nesting depth.
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fwroot import fw_root

    return fw_root()


def _venv_python(repo: str) -> str:
    return os.path.join(repo, ".venv", "bin", "python3")


def _has_required(py: str) -> bool:
    """True if every REQUIRED module imports under interpreter *py*."""
    check = "import " + ", ".join(REQUIRED)
    return (
        subprocess.run(
            [py, "-c", check],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def ensure_venv_and_deps() -> str:
    """Ensure a repo venv exists and has the fleet deps; return its python path.

    Creates ``<repo>/.venv`` if missing (using the current interpreter's
    ``-m venv``) and ``pip install``s anything in :data:`REQUIRED` that isn't
    importable. Idempotent and fast on the common path (deps already present →
    no install). Raises ``CalledProcessError`` if venv creation or install fails.
    """
    repo = _repo_root()
    venv_py = _venv_python(repo)

    if not os.path.exists(venv_py):
        base = sys.executable or "python3"
        bv = _py_version(base)
        if bv is not None and bv < MIN_PY:
            raise SystemExit(
                f"fleet: {base} is Python {bv[0]}.{bv[1]}, but the fleet tools need "
                f">= {MIN_PY[0]}.{MIN_PY[1]}. Install a newer python3 (e.g. `brew install python` "
                f"or your distro's python3) and re-run."
            )
        print(
            f"fleet: no repo venv — creating {os.path.join(repo, '.venv')} with {base} -m venv …",
            file=sys.stderr,
        )
        subprocess.run([base, "-m", "venv", os.path.join(repo, ".venv")], check=True)

    pv = _py_version(venv_py)
    if pv is not None and pv < MIN_PY:
        raise SystemExit(
            f"fleet: repo venv is Python {pv[0]}.{pv[1]}, but the fleet tools need "
            f">= {MIN_PY[0]}.{MIN_PY[1]}. Recreate it: `rm -rf .venv && python3 -m venv .venv`."
        )

    if not _has_required(venv_py):
        print(
            f"fleet: installing required tools ({', '.join(REQUIRED)}) into the venv …",
            file=sys.stderr,
        )
        # Best-effort pip upgrade (don't fail the bootstrap if it can't), then deps.
        subprocess.run(
            [venv_py, "-m", "pip", "install", "-q", "--upgrade", "pip"],
            check=False,
        )
        subprocess.run([venv_py, "-m", "pip", "install", "-q", *REQUIRED], check=True)

    return venv_py


def _running_in_repo_venv() -> bool:
    """True if the current interpreter IS the repo venv.

    Uses ``sys.prefix`` (a venv sets it to its own directory), NOT a realpath
    comparison of the executables: on macOS the venv's ``python3`` is a symlink
    to the same base interpreter that's on PATH, so ``realpath`` collapses them
    and a re-exec guard built on that wrongly thinks it's already in the venv —
    skipping the re-exec and running on without the deps.
    """
    venv_dir = os.path.join(_repo_root(), ".venv")
    return os.path.realpath(sys.prefix) == os.path.realpath(venv_dir)


def bootstrap_and_reexec() -> None:
    """Ensure the repo venv has the fleet deps, then re-exec under it unless we
    are already running it. Returns only when the current interpreter is the
    prepared venv (so the caller can import pymongo / cryptography directly)."""
    venv_py = ensure_venv_and_deps()
    if not _running_in_repo_venv():
        os.execv(venv_py, [venv_py, *sys.argv])
    # else: already in the venv; deps were just installed into this interpreter.
