"""`fw fleet status` must flag a host that runs work while ops sweeps skip it.

`joined: false` in servers.json gates DEPLOY TARGETING and name resolution
(_remote.sh, _env.sh, runner/start) -- not participation. A host set false still
runs its agent, applies fleet_config, starts runners and claims work, while every
pull, installer and rollout skips it.

Measured 2026-09-18: atopnuc02 was applying v217 with 2/2 runners up while no
host in the fleet could ssh to it, and it silently missed two rounds of fixes.
Neither fact is wrong alone; only the pair is, which is why nothing reported it.

The flag is gated on LIVE RUNNERS rather than on having a fleet-agent record,
because a retired host keeps its record (macmini01: applied hours ago, 0 runners)
and flagging every retirement forever trains you to skip the warning.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "scripts" / "lib" / "fleet" / "config"


def _load():
    # The script has no .py extension, so importlib infers no loader for it --
    # name one explicitly rather than asserting on a spec that cannot exist.
    loader = importlib.machinery.SourceFileLoader("_fleet_config", str(MOD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # The script runs argparse only under __main__, so importing is side-effect free.
    spec.loader.exec_module(mod)
    return mod


def _write_catalog(tmp_path: Path, servers: list[dict]) -> Path:
    f = tmp_path / "servers.json"
    f.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return f


def test_reads_the_not_joined_set(tmp_path, monkeypatch) -> None:
    mod = _load()
    _write_catalog(tmp_path, [
        {"name": "in01.local", "aliases": ["in01"]},                  # absent = joined
        {"name": "out02.local", "aliases": ["out02"], "joined": False},
        {"name": "yes03.local", "aliases": ["yes03"], "joined": True},
    ])
    monkeypatch.setenv("FW_SERVERS_FILE", str(tmp_path / "servers.json"))
    got = mod._catalogued_not_joined()
    assert "out02" in got, got
    assert "in01" not in got and "yes03" not in got, got


def test_missing_catalog_is_not_fatal(tmp_path, monkeypatch) -> None:
    """A status command that dies because a catalog moved is worse than one
    that omits an annotation."""
    mod = _load()
    monkeypatch.setenv("FW_SERVERS_FILE", str(tmp_path / "does-not-exist.json"))
    assert mod._catalogued_not_joined() == set()


def test_malformed_catalog_is_not_fatal(tmp_path, monkeypatch) -> None:
    mod = _load()
    bad = tmp_path / "servers.json"
    bad.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setenv("FW_SERVERS_FILE", str(bad))
    assert mod._catalogued_not_joined() == set()


def test_the_flag_is_gated_on_live_runners_not_on_an_agent_record() -> None:
    """The retirement case must NOT be flagged, or the warning becomes noise.

    Asserted against the source rather than by driving Mongo: the condition is
    the whole point of the fix, and a test that accepts `ag is not None` would
    pass while reintroducing the false alarm.
    """
    src = MOD.read_text(encoding="utf-8")
    assert "if n > 0 and host in _not_joined:" in src, (
        "the UNMANAGED flag must be gated on live runners (n > 0); gating on the "
        "existence of a fleet-agent record flags every retired host forever"
    )
    assert "UNMANAGED" in src
