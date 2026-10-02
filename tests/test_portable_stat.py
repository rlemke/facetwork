"""Shell scripts must ask `stat` for an mtime GNU-syntax FIRST.

`stat -f %m FILE` is BSD syntax. On Linux, GNU `stat -f` means "filesystem": it
prints a multi-line report to stdout and only then exits non-zero, so a
`stat -f %m X || stat -c %Y X` fallback yields the report AND the mtime, never a
number. Measured: osm-watchdog crashed on every Linux run for 16 days
(2026-09-16 .. 2026-10-02) and could not alarm. `stat -c %Y X || stat -f %m X`
is clean on both, because BSD `stat -c` fails with nothing on stdout.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_STAT = re.compile(r"stat\s+-([fc])\s*['\"]?%([mY])")


def _shell_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "scripts", "fw"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / p for p in out.split("\0") if p and not p.endswith(".py")]


def test_no_script_asks_bsd_stat_first() -> None:
    bad = []
    for p in _shell_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            first = _STAT.search(line)
            if first and first.group(1) == "f":
                bad.append(f"{p.relative_to(REPO)}:{n}: {line.strip()}")
    assert not bad, "BSD-first stat (breaks on Linux):\n" + "\n".join(bad)
