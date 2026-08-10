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

"""The lease/execution-timeout ordering invariant.

A task lease shorter than the execution timeout lets another runner reclaim a
task while its current claimant is still executing it — the work then runs
twice, which for an external API's rate limit or a PostGIS import is a
correctness bug, not a performance one. The lease is therefore *derived*:

    lease = max(DEFAULT_LEASE_MS, execution_timeout + LEASE_OVER_EXEC_GRACE_MS)

so the owning runner's own execution-timeout watchdog always fires before any
other runner's lease-expiry reclaim can pick the task up.

``FW_LEASE_DURATION_MS`` used to be honoured *verbatim*, which let an operator
setting a "reasonable" 5-minute lease beside the 15-minute execution timeout
silently invert the invariant with no warning. It is now clamped up to the
derived floor. See docs/thesis/paper-timeout-interactions.md §4.1.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.mongo_store.base import BaseMixin

MIN = 60_000


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FW_LEASE_DURATION_MS", raising=False)
    monkeypatch.delenv("FW_TASK_EXECUTION_TIMEOUT_MS", raising=False)
    # The clamp warning is once-per-process; reset it so each test can observe.
    BaseMixin._lease_clamp_warned = False
    yield
    BaseMixin._lease_clamp_warned = False


def _lease() -> int:
    # Bypass __init__: it opens a Mongo connection, and the lease derivation
    # reads only class attributes and the environment.
    return object.__new__(BaseMixin)._lease_ms()


def test_default_lease_is_derived_from_the_execution_timeout():
    """Not the 5-minute floor: 15min execution timeout + 1min grace = 16min."""
    assert _lease() == 15 * MIN + MIN


def test_raising_the_execution_timeout_raises_the_lease(monkeypatch):
    """The per-domain hazard: a domain raising its timeout must raise its lease."""
    monkeypatch.setenv("FW_TASK_EXECUTION_TIMEOUT_MS", str(40 * MIN))
    assert _lease() == 41 * MIN


def test_explicit_lease_below_the_floor_is_clamped(monkeypatch):
    """The documented foot-gun: a 'reasonable' 5min lease beside a 15min timeout."""
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(5 * MIN))
    assert _lease() == 16 * MIN, "short explicit lease was honoured — double-execution hazard"


def test_explicit_lease_above_the_floor_is_honoured(monkeypatch):
    """Longer than required is safe (slower reclaim only) and must be respected."""
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(60 * MIN))
    assert _lease() == 60 * MIN


def test_explicit_lease_exactly_at_the_floor_is_honoured(monkeypatch):
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(16 * MIN))
    assert _lease() == 16 * MIN


def test_clamp_holds_against_a_raised_execution_timeout(monkeypatch):
    """Both knobs set: the invariant still wins.

    This is the combination the paper flags — an explicit lease that was fine
    against the default timeout becomes an inversion once a domain raises the
    timeout. The floor is recomputed per call, so it does not go stale.
    """
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(20 * MIN))
    monkeypatch.setenv("FW_TASK_EXECUTION_TIMEOUT_MS", str(40 * MIN))
    assert _lease() == 41 * MIN


def test_clamp_warns_so_the_operator_learns(monkeypatch, caplog):
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(5 * MIN))
    with caplog.at_level("WARNING"):
        _lease()
    assert "clamping the lease" in caplog.text
    assert "FW_LEASE_DURATION_MS" in caplog.text


def test_clamp_warns_only_once(monkeypatch, caplog):
    """_lease_ms runs on every claim, heartbeat and stage renewal.

    An un-throttled warning would flood the logs of exactly the host whose logs
    the operator is trying to read.
    """
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(5 * MIN))
    with caplog.at_level("WARNING"):
        for _ in range(50):
            _lease()
    assert caplog.text.count("clamping the lease") == 1


def test_honoured_lease_does_not_warn(monkeypatch, caplog):
    monkeypatch.setenv("FW_LEASE_DURATION_MS", str(60 * MIN))
    with caplog.at_level("WARNING"):
        _lease()
    assert "clamping" not in caplog.text
