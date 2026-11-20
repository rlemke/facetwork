"""A fleet-agent reloading its own code must not re-apply an unchanged config.

Measured 2026-11-19: a commit added one env var to docker-compose.fleet.yml.
Once it was pulled, every agent re-exec'd itself (it reloads when its own source
changes), started with no memory of the applied version, re-applied it, and
compose -- seeing a changed service definition -- recreated every runner on
every host. 18 stitched TESS chunks about 3 h into their work were reaped and
redone. The applied version now survives the re-exec.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load():
    pytest.importorskip("pymongo", reason="fleet/agent needs pymongo (the `mongodb` extra)")
    path = REPO / "scripts" / "lib" / "fleet" / "agent"
    loader = importlib.machinery.SourceFileLoader("fleet_agent_reload_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ag = _load()


class _Exec(Exception):
    pass


def _reexec(monkeypatch, last_version, last_image):
    seen = {}

    def fake_execv(exe, argv):
        seen["state"] = ag.os.environ.get(ag._RESUME_ENV)
        raise _Exec

    monkeypatch.setattr(ag, "_source_fingerprint", lambda: ("changed",))
    monkeypatch.setattr(ag.os, "execv", fake_execv)
    monkeypatch.delenv(ag._RESUME_ENV, raising=False)
    with pytest.raises(_Exec):
        ag._reexec_if_source_changed(("old",), last_version, last_image)
    return seen["state"]


def test_a_reload_hands_over_the_applied_version(monkeypatch):
    state = _reexec(monkeypatch, 566, "registry/facetwork-runner:abc")
    assert json.loads(state) == {"version": 566, "image": "registry/facetwork-runner:abc"}
    # the new process reads it back, so its first cycle sees "up to date"
    assert ag._resume_state() == (566, "registry/facetwork-runner:abc")


def test_the_handed_over_state_is_read_once(monkeypatch):
    monkeypatch.setenv(ag._RESUME_ENV, json.dumps({"version": 3, "image": "x"}))
    assert ag._resume_state() == (3, "x")
    # gone: a child process or a later cold start must not inherit it
    assert ag._RESUME_ENV not in ag.os.environ
    assert ag._resume_state() == (None, None)


def test_nothing_applied_yet_hands_over_nothing(monkeypatch):
    # a reload before the first successful apply must still apply on its first cycle
    assert _reexec(monkeypatch, None, None) is None


def test_a_cold_start_or_bad_state_reconciles(monkeypatch):
    monkeypatch.delenv(ag._RESUME_ENV, raising=False)
    assert ag._resume_state() == (None, None)
    monkeypatch.setenv(ag._RESUME_ENV, "not json")
    assert ag._resume_state() == (None, None)
