"""Live-memory starvation verdict used by `fw maint unsatisfiable`.

A memory floor can fit a host's capacity and still never be claimed, because
runners claim against min(capacity, free-now). Measured 2026-10-16: a 13 GB
zoom build waited 111 min with zero tasks running while the capacity-only
check reported OK.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "_helpers"))
from _live_capacity import host_free_memory, starved  # noqa: E402

NOW = 10_000_000
FRESH = 5 * 60_000


def _srv(host: str, gb, at=NOW - 1000) -> dict:
    return {"server_name": host, "free_resources": {"memory_gb": gb, "at": at}}


def test_host_reading_is_best_runner_on_that_host():
    free = host_free_memory([_srv("a", 1.2), _srv("a", 3.4), _srv("b", 8.0)], NOW, FRESH)
    assert free == {"a": 3.4, "b": 8.0}


def test_stale_and_missing_readings_are_unknown_not_zero():
    free = host_free_memory(
        [_srv("a", 20.0, at=NOW - FRESH - 1), {"server_name": "b"}], NOW, FRESH)
    assert free == {}


def test_starved_when_no_capable_host_has_it_free():
    assert starved(13, {"a": 10.9, "b": 3.4}, {"a", "b"}) is True


def test_not_starved_when_one_capable_host_has_it_free():
    assert starved(13, {"a": 15.0, "b": 3.4}, {"a", "b"}) is False


def test_only_capable_hosts_count():
    # Plenty free on a host too SMALL for the floor does not help.
    assert starved(13, {"small": 14.0, "big": 4.0}, {"big"}) is True


def test_an_unread_capable_host_means_could_not_tell():
    """Mid-rollout an old-image host publishes nothing; it may have the memory
    free, so no verdict rather than a false alarm."""
    assert starved(13, {"a": 4.0}, {"a", "b"}) is None
    assert starved(13, {}, {"a"}) is None
    assert starved(13, {"a": 4.0}, set()) is None
