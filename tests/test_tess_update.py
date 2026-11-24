"""Tests for `fw svc tess-update` — find newly released TESS sectors and submit them.

Every test runs against a SYNTHETIC MAST listing and bucket: the real ones move
every few weeks, so a test against them would pass or fail with the calendar.
"""

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_tess_update", ROOT / "scripts" / "lib" / "svc" / "_tess_update.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


U = _load()


def _page(*sectors, stamp="tess2026000000000"):
    rows = [
        f'<a href="/missions/tess/catalogs/tce/{stamp}-s{s:04d}-s{s:04d}_dvr-tcestats.csv">x</a>'
        for s in sectors
    ]
    # a multi-sector table must never be read as a sector of its own
    rows.append('<a href="/missions/tess/catalogs/tce/tess2018-s0001-s0096_dvr-tcestats.csv">m</a>')
    return "\n".join(rows)


@pytest.fixture
def mast(monkeypatch):
    """Synthetic MAST: `world["page"]` is the listing, `world["lc"]` the sectors with light curves."""
    world = {"page": _page(1, 2), "lc": {1, 2}}

    def fetch(url):
        if url == U.TCE_PAGE:
            return world["page"]
        for s in range(1, 200):
            if url == U.BUCKET.format(s):
                return f"<KeyCount>{1 if s in world['lc'] else 0}</KeyCount>"
        raise AssertionError(url)

    monkeypatch.setattr(U, "fetch", fetch)
    return world


@pytest.fixture
def submitted(monkeypatch):
    calls = []

    def run(cmd, capture_output, text):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, "runner 6268ec0b-38b6-40b5-bc98-9817e7f1656a\n", ""
        )

    monkeypatch.setattr(U.subprocess, "run", run)
    return calls


def _args(tmp_path, **kw):
    base = {
        "state": tmp_path / "state.json",
        "work_dir": "s3://b/tess",
        "fw": "fw",
        "ffl": "tess.ffl",
        "sectors": [],
        "first": 1,
        "through": 0,
        "note": "seeded",
        "dry": False,
    }
    return argparse.Namespace(**{**base, **kw})


def test_an_empty_listing_is_an_error_not_no_sectors(mast):
    mast["page"] = "<html>a page that changed shape</html>"
    with pytest.raises(RuntimeError):
        U.tce_tables()


def test_multisector_tables_are_not_sectors(mast):
    assert set(U.tce_tables()) == {1, 2}


def test_run_refuses_without_seeded_state(tmp_path, mast, submitted):
    assert U.cmd_run(_args(tmp_path)) == 1
    assert submitted == []


def test_only_new_released_sectors_are_submitted(tmp_path, mast, submitted):
    U.cmd_seed(_args(tmp_path, through=2))
    mast["page"] = _page(1, 2, 3, 4)
    mast["lc"] = {1, 2, 3}  # sector 4 has a TCE table but no light curves yet
    assert U.cmd_run(_args(tmp_path)) == 0
    workflows = [c[c.index("--workflow") + 1] for c in submitted]
    assert workflows == ["tess.workflows.SearchSectors", "tess.workflows.FalseAlarms"]
    assert all(json.loads(c[c.index("--inputs") + 1])["sectors"] == [3] for c in submitted)
    state = json.loads((tmp_path / "state.json").read_text())
    assert "3" in state["sectors"] and "4" not in state["sectors"]
    assert state["sectors"]["3"]["runs"][0]["ids"] == ["6268ec0b-38b6-40b5-bc98-9817e7f1656a"]
    # the next tick submits nothing new
    submitted.clear()
    assert U.cmd_run(_args(tmp_path)) == 0
    assert submitted == []


def test_a_failed_submission_is_not_recorded(tmp_path, mast, monkeypatch):
    U.cmd_seed(_args(tmp_path, through=1))
    monkeypatch.setattr(
        U.subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, "", "no mongo")
    )
    with pytest.raises(RuntimeError):
        U.cmd_run(_args(tmp_path))
    assert "2" not in json.loads((tmp_path / "state.json").read_text())["sectors"]


def test_reprocessing_is_reported_and_not_rerun(tmp_path, mast, submitted, capsys):
    U.cmd_seed(_args(tmp_path, through=2))
    mast["page"] = _page(1, 2, stamp="tess2027999999999")
    assert U.cmd_run(_args(tmp_path)) == 0
    assert submitted == []
    assert "reprocessed" in capsys.readouterr().out
    state = json.loads((tmp_path / "state.json").read_text())
    assert [r["sector"] for r in state["reprocessed"]] == [1, 2]


def _state(tmp_path, last_run_days, last_new_days):
    now = U.now()
    st = {
        "version": 1,
        "sectors": {"1": {}},
        "reprocessed": [],
        "last_run": U.iso(now - dt.timedelta(days=last_run_days)),
        "last_new_sector_at": U.iso(now - dt.timedelta(days=last_new_days)),
    }
    (tmp_path / "state.json").write_text(json.dumps(st))


def test_check_passes_on_a_host_with_no_state(tmp_path):
    assert U.cmd_check(_args(tmp_path)) == 0


def test_check_healthy(tmp_path):
    _state(tmp_path, 0, 10)
    assert U.cmd_check(_args(tmp_path)) == 0


def test_check_alarms_when_the_timer_stopped(tmp_path):
    _state(tmp_path, U.MAX_DAYS_SINCE_RUN + 1, 10)
    assert U.cmd_check(_args(tmp_path)) == 1


def test_check_alarms_when_sectors_stopped_arriving(tmp_path):
    _state(tmp_path, 0, U.MAX_DAYS_WITHOUT_NEW + 1)
    assert U.cmd_check(_args(tmp_path)) == 1


def test_check_cannot_verify_a_broken_state(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"sectors": {}}))
    assert U.cmd_check(_args(tmp_path)) == 2
