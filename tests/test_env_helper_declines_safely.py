"""_env.sh must not kill a `set -e` caller when a lookup merely DECLINES.

Regression for the 2026-09-18 macmini02 outage: the catalog fallback in
_env.sh assigned from a command substitution whose python exits 1 to mean
"I could not resolve this". A failing command substitution is a failing
command, so under the caller's `set -e` a declined lookup aborted the caller
-- before printing anything. `fw fleet agent` captures both streams and shows
them only on failure, so 94 consecutive reconciles reported nothing but
"start-worker failed (exit 1)" and the host could not be triaged remotely.

The bug class is the asymmetry: the file containing the assignment does not
itself `set -e`, so any audit scoped to files that do will miss it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_SH = REPO / "scripts" / "lib" / "_helpers" / "_env.sh"

# A name that cannot resolve and is not in servers.json, so every tier declines.
UNRESOLVABLE = "mongodb://definitely-not-a-host-xyz-9f2a:27017"

_PROBE = """
set -euo pipefail
export FW_MONGODB_URL='{url}'
source scripts/lib/_helpers/_bootstrap.sh
source '{env_sh}'
echo REACHED_END
"""


def _run(env_sh: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _PROBE.format(url=UNRESOLVABLE, env_sh=env_sh)],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )


def test_declining_lookup_does_not_abort_a_set_e_caller() -> None:
    r = _run(ENV_SH)
    assert "REACHED_END" in r.stdout, (
        "sourcing _env.sh aborted a `set -e` caller when the catalog declined.\n"
        f"exit={r.returncode}\nstdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert r.returncode == 0, f"exit={r.returncode} stderr={r.stderr!r}"


def test_the_guard_is_what_makes_it_survive(tmp_path: Path) -> None:
    """Prove the assertion above can fail — a probe you cannot make go red is
    not evidence. Strip the `||` guard and the caller must die silently."""
    stripped = tmp_path / "_env_no_guard.sh"
    text = ENV_SH.read_text(encoding="utf-8")
    needle = ')" || _FW_CAT_URL=""'
    assert needle in text, "the guard this test protects is gone from _env.sh"
    stripped.write_text(text.replace(needle, ')"'), encoding="utf-8")

    r = _run(stripped)
    assert "REACHED_END" not in r.stdout, (
        "expected the unguarded form to abort the caller; it did not, so this "
        "test no longer demonstrates the defect it was written for"
    )
    assert r.returncode != 0
    # The defining symptom: it dies without explaining itself.
    assert r.stderr == "", f"expected a SILENT death, got stderr={r.stderr!r}"


def test_fallback_is_loud_when_it_lands_on_localhost() -> None:
    """Declining safely must not become declining quietly: falling back to a
    separate local database is exactly the outcome that needs to be said."""
    r = _run(ENV_SH)
    assert "LOCALHOST" in r.stderr and "not the fleet" in r.stderr, (
        f"localhost fallback was not announced; stderr={r.stderr!r}"
    )
