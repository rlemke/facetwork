"""Claiming offers memory that is free NOW, not just the host's capacity.

Measured 2026-10-03: a 30 GB host running ~23 runners, a router and a nightly
cron job OOM-killed four runner processes. Capacity-only claiming is a floor,
not a reservation.
"""

from __future__ import annotations

from facetwork.runtime import base_runner as br


def _fresh(monkeypatch, capacity: float, live: float | None):
    monkeypatch.setattr(br, "measured_resources", lambda: {"memory_gb": capacity, "cpus": 8.0})
    monkeypatch.setattr(br, "_available_memory_gb", lambda: live)


def test_claim_offers_the_smaller_of_capacity_and_free_memory(monkeypatch):
    _fresh(monkeypatch, 30.0, 6.5)
    assert br.claim_resources() == {"memory_gb": 6.5, "cpus": 8.0}


def test_capacity_still_caps_a_large_free_figure(monkeypatch):
    _fresh(monkeypatch, 14.0, 40.0)  # a VM reporting more than its cgroup allows
    assert br.claim_resources()["memory_gb"] == 14.0


def test_unknown_live_memory_falls_back_to_capacity(monkeypatch):
    _fresh(monkeypatch, 30.0, None)
    assert br.claim_resources()["memory_gb"] == 30.0


def test_opt_out_restores_capacity_only(monkeypatch):
    _fresh(monkeypatch, 30.0, 2.0)
    monkeypatch.setenv("FW_CLAIM_LIVE_MEMORY", "0")
    assert br.claim_resources()["memory_gb"] == 30.0


def test_registration_still_advertises_capacity(monkeypatch):
    _fresh(monkeypatch, 30.0, 2.0)
    assert br.BaseRunner._measured_resources(object()) == {"memory_gb": 30.0, "cpus": 8.0}
