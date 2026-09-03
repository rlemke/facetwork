"""`fw maint unsatisfiable` must see ALL THREE routing dimensions.

⚠️ It originally checked only `requires`. An unmet `environment_hash` strands a
task exactly the same way -- never claimed, never retried, never dead-lettered --
and the check reported "OK" while it happened. That is the failure the tool
exists to catch, so a check that misses it is worse than none: it certifies
health it did not verify.

The third dimension is the facet NAME. `claim_task` is name-filtered
server-side, so a task whose facet no live runner has a handler for is never
CLAIMED -- and therefore never released-with-backoff, never retried, never
dead-lettered. Measured 2026-09-02: repro.asteroids.PickExposure sat pending
for 8h at retry_count=0 while this check reported OK, because the query was
restricted to tasks declaring `requires` or `environment_hash` and that task
declared neither.
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "maint" / "unsatisfiable"


def _text() -> str:
    return SRC.read_text()


def test_pending_query_includes_env_routed_tasks():
    """Filtering on `requires` alone hid every script task.

    The query no longer filters at all — adding the name dimension widened it
    to every pending task — so the invariant is now that the environment is
    still FETCHED and still tested, not that it appears in a $or.
    """
    s = _text()
    q = s[s.index("db.tasks.find("):s.index("servers = list")]
    assert '"environment_hash": 1' in q, "env hash must be projected for the test below"
    assert '"requires": 1' in q
    assert "provided by NO live runner" in s, "env dimension must still be tested"


def test_compares_against_what_runners_advertise():
    s = _text()
    assert "provided_environments" in s, (
        "must read provided_environments from the server records, which is the "
        "same field claim_script_task filters on")


def test_reports_the_environment_that_is_missing():
    """A bare 'unsatisfiable' is not actionable; the hash must be named."""
    s = _text()
    assert re.search(r"environment \{?.*provided by NO live runner", s), \
        "the reason line must name the unprovided environment"


def test_points_at_the_bake_as_the_usual_cause():
    """Every real instance so far was an environment the image did not bake."""
    s = _text()
    assert "bake" in s.lower(), "guidance should point at the image build"


def test_docstring_records_why_the_dimension_was_added():
    s = _text()
    assert "stale cached layer" in s or "never baked" in s, (
        "the docstring should carry the incident that motivated the check")


def test_pending_query_is_not_restricted_to_constrained_tasks():
    """The stranding task declares NOTHING, so a query filtered to tasks that
    declare a constraint cannot see it."""
    s = _text()
    q = s[s.index("db.tasks.find("):s.index("servers = list")]
    assert '"state": "pending"' in q
    assert "$or" not in q, (
        "the name dimension applies to every pending task; restricting the "
        "query to declared constraints is what hid PickExposure"
    )


def test_name_dimension_is_checked_against_live_handlers():
    s = _text()
    assert '"handlers": 1' in s, "server query must fetch advertised handlers"
    assert "served" in s and "has a handler for" in s


def test_script_tasks_are_exempt_from_the_name_dimension():
    """A script task is named by its facet and is in NO runner's handler set --
    the environment is its capability (persistence.claim_script_task). Applying
    the name rule to it would report every script task as unservable."""
    s = _text()
    assert 'kind' in s and '"script"' in s


def test_protocol_tasks_are_exempt():
    """Every runner handles fw:execute / fw:resume by definition."""
    s = _text()
    assert 'startswith("fw:")' in s


def test_capacity_listing_is_deduplicated():
    """85 runners are ~20 processes per host advertising identical numbers;
    printing each buried the one finding under 85 near-identical lines, in a
    report that now runs on a timer."""
    s = _text()
    block = s[s.index("fleet capacity"):s.index("environments provided")]
    assert "seen[key] = seen.get(key, 0) + 1" in block, "capacity must be grouped"
    assert "for (host, res), n in sorted(seen.items())" in block, (
        "one row per DISTINCT (host, capacity), not one per runner process"
    )
