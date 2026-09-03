"""`fw maint unsatisfiable` must see BOTH routing dimensions.

⚠️ It originally checked only `requires`. An unmet `environment_hash` strands a
task exactly the same way -- never claimed, never retried, never dead-lettered --
and the check reported "OK" while it happened. That is the failure the tool
exists to catch, so a check that misses it is worse than none: it certifies
health it did not verify.
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "maint" / "unsatisfiable"


def _text() -> str:
    return SRC.read_text()


def test_pending_query_includes_env_routed_tasks():
    """Filtering on `requires` alone hides every script task."""
    s = _text()
    assert '"environment_hash"' in s, "pending query must consider environment_hash"
    q = s[s.index('db.tasks.find('):s.index('servers = list')]
    assert "$or" in q, "must match tasks with EITHER a resource floor or an environment"
    assert "requires" in q and "environment_hash" in q


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
