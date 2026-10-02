"""_osm_selfhost_env.sh fills the OSM publish/watchdog settings from this host's
re-split config when the environment does not set them.

These values used to come from a `.env` on the host that generated the split.
When the work moved hosts nothing carried them over, and the replication
publisher and the watchdog both failed on every run for 17 days.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts/lib/_helpers/_osm_selfhost_env.sh"


def _run(tmp_path: Path, pre: str = "") -> str:
    script = (
        f"FW_OSM_SELFHOST_CONFIG={tmp_path / 'c.env'}; {pre} . {HELPER}; "
        'echo "${FW_OSM_SELFHOST_WWW:-}|${FW_OSM_SELFHOST_POLYS:-}|'
        '${FW_OSM_SELFHOST_BASE_URL:-}|${FW_OSM_NIGHTLY_INDEXES-unset}"'
    )
    r = subprocess.run(
        ["bash", "-uc", script],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().replace(str(tmp_path), "T")


def _tree(tmp_path: Path) -> None:
    for d in ("osm/www", "osm/polys", "osm/indexes"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "osm/indexes/alpr.sqlite").write_bytes(b"")
    (tmp_path / "c.env").write_text(f'WWW="{tmp_path}/osm/www"\nBASE_URL="http://x.test:8088"\n')


def test_fills_every_setting_from_the_config(tmp_path: Path) -> None:
    _tree(tmp_path)
    assert _run(tmp_path) == "T/osm/www|T/osm/polys|http://x.test:8088|alpr"


def test_explicit_values_win_and_an_empty_index_list_is_a_choice(tmp_path: Path) -> None:
    _tree(tmp_path)
    out = _run(tmp_path, "FW_OSM_SELFHOST_WWW=/explicit; FW_OSM_NIGHTLY_INDEXES=;")
    assert out == "/explicit||http://x.test:8088|"


def test_no_config_leaves_everything_unset(tmp_path: Path) -> None:
    assert _run(tmp_path) == "|||unset"
