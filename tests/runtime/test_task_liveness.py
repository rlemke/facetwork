"""Liveness callbacks must not let a store outage kill the handler."""

from __future__ import annotations

import logging

import pytest

from facetwork.runtime import task_liveness
from facetwork.runtime.task_liveness import make_stage_budget_setter, make_task_heartbeat


class _Store:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.heartbeats: list[tuple] = []
        self.budgets: list[tuple] = []

    def update_task_heartbeat(
        self, task_id, t, progress_pct=None, progress_message=None, expected_server_id=""
    ):
        if self.fail:
            raise ConnectionError("database host unreachable")
        self.heartbeats.append((task_id, t, progress_pct, progress_message, expected_server_id))

    def update_task_stage_budget(self, task_id, expires, stage_name=""):
        if self.fail:
            raise ConnectionError("database host unreachable")
        self.budgets.append((task_id, expires, stage_name))


def test_heartbeat_forwards_ownership_gate_and_progress():
    store = _Store()
    hb = make_task_heartbeat(store, "task-1", "srv-a", facet_name="osm.Build", now_ms=lambda: 1000)
    hb(progress_pct=40, progress_message="routed 2,000/8,000 pairs")
    assert store.heartbeats == [("task-1", 1000, 40, "routed 2,000/8,000 pairs", "srv-a")]


def test_heartbeat_survives_store_outage(caplog):
    store = _Store(fail=True)
    hb = make_task_heartbeat(store, "abcdef123456", "srv-a", facet_name="osm.Build")
    with caplog.at_level(logging.WARNING, logger=task_liveness.__name__):
        for _ in range(50):
            hb(progress_message="still routing")  # must not raise
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # Rate-limited: one line for the burst, naming the facet and task.
    assert len(warnings) == 1
    assert "osm.Build" in warnings[0].getMessage()
    assert "abcdef12" in warnings[0].getMessage()


def test_heartbeat_warns_again_after_interval(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(task_liveness.time, "monotonic", lambda: clock[0])
    hb = make_task_heartbeat(_Store(fail=True), "t", "s", facet_name="F")
    with caplog.at_level(logging.WARNING, logger=task_liveness.__name__):
        clock[0] = 1.0
        hb()
        clock[0] = 2.0
        hb()
        clock[0] = 1.0 + task_liveness._WARN_INTERVAL_S + 1
        hb()
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2
    assert "+1 more" in msgs[1]


def test_stage_budget_survives_store_outage():
    make_stage_budget_setter(_Store(fail=True), "t", facet_name="F")(60_000, "route")


def test_stage_budget_sets_expiry_and_ignores_nonpositive():
    store = _Store()
    setter = make_stage_budget_setter(store, "t", now_ms=lambda: 5_000)
    setter(0, "noop")
    setter(60_000, "route")
    assert store.budgets == [("t", 65_000, "route")]


@pytest.mark.parametrize(
    "module",
    [
        "facetwork.runtime.runner.service",
        "facetwork.runtime.task_processor",
        "facetwork.runtime.registry_runner",
        "facetwork.runtime.agent_poller",
    ],
)
def test_runners_do_not_call_the_store_directly_for_heartbeats(module):
    """Every runner builds its heartbeat through the shared best-effort helper."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert "make_task_heartbeat(" in src
    assert "self._persistence.update_task_heartbeat(" not in src
