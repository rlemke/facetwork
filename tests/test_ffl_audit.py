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

"""Tests for ``fw util ffl-audit``.

The load-bearing part is :func:`dependencies_for`. Both naive library sets
produce ~90 phantom errors — validating a repo alone breaks the pure-FFL
catalogs, and supplying every repo makes short names ambiguous across domains —
so an audit that gets this wrong reports drift on nearly everything and stops
being read at all.
"""

from __future__ import annotations

import pathlib

from facetwork.ffl_audit import (
    CONTRACT_CHECKS,
    dependencies_for,
    ffl_files,
    scan_contracts,
    scan_namespaces,
)


# --- dependency derivation --------------------------------------------------


def test_a_catalog_repo_gets_its_provider():
    """fwh_osm_lz `use`s osm.types but defines only osm_lz.* — it needs fwh_osm."""
    defines = {"fwh_osm": {"osm.types", "osm.cache"}, "fwh_osm_lz": {"osm_lz.workflows"}}
    uses = {"fwh_osm": set(), "fwh_osm_lz": {"osm.types"}}
    assert dependencies_for("fwh_osm_lz", defines, uses) == ["fwh_osm"]


def test_a_self_contained_repo_gets_nothing():
    """Supplying unrelated repos is what makes short names ambiguous — e.g. four
    domains each define a `RetryPolicy` mixin. A repo that defines everything it
    uses must be compiled ALONE."""
    defines = {"fwh_a": {"a.types", "a.work"}, "fwh_b": {"b.types"}}
    uses = {"fwh_a": {"a.types"}, "fwh_b": set()}
    assert dependencies_for("fwh_a", defines, uses) == []


def test_a_use_at_a_different_depth_still_resolves():
    """FFL namespaces are hierarchical and a catalog may `use` at a depth the
    provider does not declare verbatim, in either direction."""
    defines = {"fwh_osm": {"osm.Transit.GTFS.Feeds"}, "fwh_lz": {"lz.x"}}
    uses = {"fwh_osm": set(), "fwh_lz": {"osm.Transit.GTFS"}}
    assert dependencies_for("fwh_lz", defines, uses) == ["fwh_osm"]

    defines2 = {"fwh_osm": {"osm.Transit"}, "fwh_lz": {"lz.x"}}
    uses2 = {"fwh_osm": set(), "fwh_lz": {"osm.Transit.GTFS.Feeds"}}
    assert dependencies_for("fwh_lz", defines2, uses2) == ["fwh_osm"]


def test_a_repo_is_never_its_own_dependency():
    defines = {"fwh_a": {"a.types", "a.work"}}
    uses = {"fwh_a": {"a.types", "a.missing"}}
    assert dependencies_for("fwh_a", defines, uses) == []


# --- source scanning --------------------------------------------------------


def test_scan_namespaces_reads_defines_and_uses(tmp_path):
    f = tmp_path / "x.ffl"
    f.write_text(
        "namespace demo.types {\n}\n"
        "namespace demo.work {\n"
        "    use demo.types\n"
        "    use other.thing\n"
        "}\n"
    )
    defined, used = scan_namespaces([f])
    assert defined == {"demo.types", "demo.work"}
    assert used == {"demo.types", "other.thing"}


def test_ffl_files_skips_vendored_virtualenvs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/real.ffl").write_text("namespace a {}\n")
    (tmp_path / ".venv/lib").mkdir(parents=True)
    (tmp_path / ".venv/lib/vendored.ffl").write_text("namespace b {}\n")
    found = [p.name for p in ffl_files(tmp_path)]
    assert found == ["real.ffl"]


# --- handler contracts ------------------------------------------------------


def _handler(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    d = tmp_path / "src" / "handlers"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "h.py"
    f.write_text(body)
    return f


def test_the_obsolete_step_log_list_form_is_flagged(tmp_path):
    """This is the failure the audit exists for: `_step_log` is a callback, and
    `.append` raises AttributeError at the handler's first log line."""
    _handler(tmp_path, 'def h(p):\n    step_log = p.get("_step_log")\n'
                       '    step_log.append({"message": "x", "level": "info"})\n')
    hits = scan_contracts(tmp_path)
    assert len(hits) == 1 and "_step_log-as-list" in hits[0]


def test_the_correct_call_form_is_not_flagged(tmp_path):
    _handler(tmp_path, 'def h(p):\n    step_log = p.get("_step_log")\n'
                       '    step_log("x", level="info")\n')
    assert scan_contracts(tmp_path) == []


def test_tests_are_exempt(tmp_path):
    """A regression test may construct the old shape deliberately to prove it is
    rejected. Flagging that would punish the very test written to prevent this
    drift — which is exactly what fwh_sensor_monitoring now carries."""
    d = tmp_path / "tests"
    d.mkdir(parents=True)
    (d / "test_x.py").write_text('def t():\n    step_log.append({"message": "x"})\n')
    assert scan_contracts(tmp_path) == []


def test_a_vendored_dependency_is_not_scanned(tmp_path):
    d = tmp_path / ".venv" / "lib" / "site-packages" / "dep"
    d.mkdir(parents=True)
    (d / "mod.py").write_text('step_log.append({"message": "x"})\n')
    assert scan_contracts(tmp_path) == []


def test_step_log_check_catches_the_spelling_it_is_named_after():
    """⚠️ The regex was `\\bstep_log…`, which MISSES `_step_log.append(`.

    `_` is a word character, so there is no boundary between it and the `s`.
    The check is called `_step_log-as-list` and the documented failure it exists
    to prevent (six handlers dead in fwh_sensor_monitoring, 48 tests green) is
    written `_step_log` — so the check was blind to its own motivating case
    while happily matching the bare `step_log.append(`.
    """
    pattern = CONTRACT_CHECKS[0][1]
    for src in ('_step_log.append("x")',
                'step_log.append("x")',
                'params["_step_log"].append("x")',
                "params['_step_log'].append('x')",
                '_step_log .append( "x" )',
                '        _step_log.append(msg)'):
        assert pattern.search(src), f"must flag: {src}"


def test_step_log_check_does_not_flag_unrelated_lists():
    """A genuine list that merely contains 'step_log' in its name is not the bug."""
    pattern = CONTRACT_CHECKS[0][1]
    for src in ('my_step_log.append("x")',
                'step_logs.append(x)',
                'self.step_log_list.append(x)',
                'astep_log.append(x)',
                'step_log(msg, level="info")'):
        assert not pattern.search(src), f"must NOT flag: {src}"


def test_build_artifacts_are_excluded_from_both_scans(tmp_path):
    """A build/ copy of a repo's own FFL made every reference to its namespace
    'ambiguous' against two candidates with the SAME name — 218 phantom errors
    from one `python -m build --wheel`, enough to bury a real finding.

    Both scans must use the same exclusions; they used to differ, so an artifact
    was invisible to one and poisoned the other.
    """
    repo = tmp_path / "fwh_x"
    for rel in ("src/x/ffl/a.ffl", "build/lib/x/ffl/a.ffl",
                "dist/x/ffl/a.ffl", ".venv/lib/x.ffl", "x.egg-info/a.ffl"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("namespace x {}\n")
    found = [str(f.relative_to(repo)) for f in ffl_files(repo)]
    assert found == ["src/x/ffl/a.ffl"], found

    for rel in ("src/x/h.py", "build/lib/x/h.py", "site-packages/x/h.py"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('_step_log.append("x")\n')
    hits = scan_contracts(repo)
    assert len(hits) == 1 and "src/x/h.py" in hits[0], hits
