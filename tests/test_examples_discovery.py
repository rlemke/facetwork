"""Tests for facetwork.examples (entry-point + local discovery)."""

from __future__ import annotations

from pathlib import Path

import pytest

from facetwork._pkg_discovery import _parse_runner_env
from facetwork.examples import (
    ExamplePackage,
    collect_ffl_files,
    discover_all_examples,
    discover_local_examples,
    filter_examples,
)


def _make_local_example(root: Path, name: str, *, with_handlers: bool, with_ffl: bool) -> Path:
    ex = root / "examples" / name
    if with_handlers:
        (ex / "handlers").mkdir(parents=True)
        (ex / "handlers" / "__init__.py").write_text(
            f"def register_all_registry_handlers(runner):\n    runner.calls.append({name!r})\n"
        )
    if with_ffl:
        (ex / "ffl").mkdir(parents=True)
        (ex / "ffl" / f"{name}.ffl").write_text("// stub\n")
    return ex


class _FakeRunner:
    def __init__(self):
        self.calls: list[str] = []


class TestLocalDiscovery:
    def test_finds_examples_with_handlers_and_ffl(self, tmp_path):
        _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=True)
        _make_local_example(tmp_path, "beta", with_handlers=True, with_ffl=False)
        _make_local_example(tmp_path, "gamma", with_handlers=False, with_ffl=True)

        found = discover_local_examples(tmp_path)
        names = [p.name for p in found]
        assert names == ["alpha", "beta", "gamma"]

        alpha = next(p for p in found if p.name == "alpha")
        assert alpha.source == "local"
        assert alpha.handlers_path == tmp_path / "examples" / "alpha"
        assert alpha.ffl_dir == tmp_path / "examples" / "alpha" / "ffl"

        gamma = next(p for p in found if p.name == "gamma")
        assert gamma.handlers_path is None
        assert gamma.ffl_dir == tmp_path / "examples" / "gamma" / "ffl"

    def test_skips_dirs_without_handlers_or_ffl(self, tmp_path):
        (tmp_path / "examples" / "empty").mkdir(parents=True)
        assert discover_local_examples(tmp_path) == []

    def test_handles_missing_examples_dir(self, tmp_path):
        assert discover_local_examples(tmp_path) == []

    def test_register_handlers_invokes_local_module(self, tmp_path):
        _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=False)
        [pkg] = discover_local_examples(tmp_path)
        runner = _FakeRunner()
        pkg.register_handlers(runner)
        assert runner.calls == ["alpha"]

    def test_two_local_examples_do_not_collide(self, tmp_path):
        # Both define `handlers/__init__.py` — register must reload between calls.
        _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=False)
        _make_local_example(tmp_path, "beta", with_handlers=True, with_ffl=False)
        found = discover_local_examples(tmp_path)
        runner = _FakeRunner()
        for pkg in found:
            pkg.register_handlers(runner)
        assert runner.calls == ["alpha", "beta"]


class TestRunnerEnvParser:
    def test_parses_basic_assignments(self, tmp_path):
        f = tmp_path / "runner.env"
        f.write_text("FOO=bar\nBAZ=qux\n")
        assert _parse_runner_env(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_inline_comments_on_unquoted_values(self, tmp_path):
        f = tmp_path / "runner.env"
        f.write_text("TIMEOUT=14400000   # 4 hours\n")
        assert _parse_runner_env(f) == {"TIMEOUT": "14400000"}

    def test_keeps_hash_inside_quoted_values(self, tmp_path):
        f = tmp_path / "runner.env"
        f.write_text('NOTE="hash # stays"\n')
        assert _parse_runner_env(f) == {"NOTE": "hash # stays"}

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / "runner.env"
        f.write_text("# comment\n\nFOO=bar\n")
        assert _parse_runner_env(f) == {"FOO": "bar"}

    def test_handles_export_prefix(self, tmp_path):
        f = tmp_path / "runner.env"
        f.write_text("export FOO=bar\n")
        assert _parse_runner_env(f) == {"FOO": "bar"}


class TestFflCollection:
    def test_local_collects_root_and_nested_ffl(self, tmp_path):
        ex = _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=True)
        nested = ex / "handlers" / "sub" / "ffl"
        nested.mkdir(parents=True)
        (nested / "nested.ffl").write_text("// stub\n")

        [pkg] = discover_local_examples(tmp_path)
        files = collect_ffl_files(pkg)
        rel = sorted(str(f.relative_to(ex)) for f in files)
        assert rel == ["ffl/alpha.ffl", "handlers/sub/ffl/nested.ffl"]

    def test_local_excludes_test_fixtures_except_real(self, tmp_path):
        ex = _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=True)
        (ex / "tests" / "fixtures" / "ffl").mkdir(parents=True)
        (ex / "tests" / "fixtures" / "ffl" / "fixture.ffl").write_text("// stub\n")
        (ex / "tests" / "real" / "ffl").mkdir(parents=True)
        (ex / "tests" / "real" / "ffl" / "real.ffl").write_text("// stub\n")

        [pkg] = discover_local_examples(tmp_path)
        files = collect_ffl_files(pkg)
        rel = sorted(str(f.relative_to(ex)) for f in files)
        assert "tests/fixtures/ffl/fixture.ffl" not in rel
        assert "tests/real/ffl/real.ffl" in rel

    def test_entry_point_collects_recursively_from_ffl_dir(self, tmp_path):
        ffl_root = tmp_path / "pkg" / "ffl"
        (ffl_root / "sub").mkdir(parents=True)
        (ffl_root / "a.ffl").write_text("// stub\n")
        (ffl_root / "sub" / "b.ffl").write_text("// stub\n")

        pkg = ExamplePackage(
            name="entry",
            ffl_dir=ffl_root,
            register_handlers=lambda r: None,
            source="entry_point",
        )
        files = collect_ffl_files(pkg)
        rel = sorted(str(f.relative_to(ffl_root)) for f in files)
        assert rel == ["a.ffl", "sub/b.ffl"]

    def test_extra_ffl_dirs_picked_up(self, tmp_path):
        # Simulate an entry-point package whose installed src tree only sees
        # the canonical ffl/, but the repo also ships fixtures at repo-root
        # tests/real/ffl/ — caller declares them via extra_ffl_dirs.
        pkg_root = tmp_path / "pkg"
        ffl_root = pkg_root / "ffl"
        ffl_root.mkdir(parents=True)
        (ffl_root / "main.ffl").write_text("// stub\n")

        repo_real = tmp_path / "tests" / "real" / "ffl"
        repo_real.mkdir(parents=True)
        (repo_real / "fixture_a.ffl").write_text("// stub\n")
        (repo_real / "fixture_b.ffl").write_text("// stub\n")

        pkg = ExamplePackage(
            name="entry",
            ffl_dir=ffl_root,
            register_handlers=lambda r: None,
            source="entry_point",
            extra_ffl_dirs=[repo_real],
        )
        files = collect_ffl_files(pkg)
        rel = sorted(str(f.relative_to(tmp_path)) for f in files)
        assert rel == [
            "pkg/ffl/main.ffl",
            "tests/real/ffl/fixture_a.ffl",
            "tests/real/ffl/fixture_b.ffl",
        ]

    def test_extra_ffl_dirs_dedupes_with_root_walk(self, tmp_path):
        # If extra_ffl_dirs overlaps a path the default walk already covers,
        # the file should only appear once.
        ex = _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=True)
        nested = ex / "tests" / "real" / "ffl"
        nested.mkdir(parents=True)
        (nested / "shared.ffl").write_text("// stub\n")

        [pkg] = discover_local_examples(tmp_path)
        pkg.extra_ffl_dirs.append(nested)
        files = collect_ffl_files(pkg)
        shared_hits = [f for f in files if f.name == "shared.ffl"]
        assert len(shared_hits) == 1

    def test_extra_ffl_dirs_skips_missing_dir(self, tmp_path):
        ffl_root = tmp_path / "pkg" / "ffl"
        ffl_root.mkdir(parents=True)
        (ffl_root / "main.ffl").write_text("// stub\n")

        pkg = ExamplePackage(
            name="entry",
            ffl_dir=ffl_root,
            register_handlers=lambda r: None,
            source="entry_point",
            extra_ffl_dirs=[tmp_path / "does-not-exist"],
        )
        files = collect_ffl_files(pkg)
        assert [f.name for f in files] == ["main.ffl"]


# Entry-point discovery moved to the domains family — see test_domains_discovery.py.
# Teaching examples are in-repo only, so discover_all_examples is local-only now.


class TestExamplesLocalOnly:
    def test_examples_are_local_only(self, tmp_path):
        # No entry-point packages leak into the teaching-example family.
        _make_local_example(tmp_path, "demo", with_handlers=True, with_ffl=True)
        names = [p.name for p in discover_all_examples(tmp_path)]
        assert names == ["demo"]

    def test_none_repo_root_yields_nothing(self):
        assert discover_all_examples(None) == []


class TestFilter:
    def test_filter_by_name(self, tmp_path):
        _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=False)
        _make_local_example(tmp_path, "beta", with_handlers=True, with_ffl=False)
        _make_local_example(tmp_path, "gamma", with_handlers=True, with_ffl=False)

        found = discover_local_examples(tmp_path)
        names = [p.name for p in filter_examples(found, include=["alpha", "gamma"])]
        assert names == ["alpha", "gamma"]

    def test_filter_none_passes_all_through(self, tmp_path):
        _make_local_example(tmp_path, "alpha", with_handlers=True, with_ffl=False)
        found = discover_local_examples(tmp_path)
        assert list(filter_examples(found, include=None)) == found


# Minimal but parser/validator-clean FFL with a namespace + two workflows.
_SEED_FFL = """\
namespace seedy {
    event facet AddOne(value: Long) => (result: Long)

    workflow AddOneWorkflow(input: Long) => (output: Long) andThen {
        added = AddOne(value = $.input)
        yield AddOneWorkflow(output = added.result)
    }

    workflow DoubleAddOne(input: Long) => (output: Long) andThen {
        first = AddOne(value = $.input)
        second = AddOne(value = first.result)
        yield DoubleAddOne(output = second.result)
    }
}
"""


class _RecordingStore:
    """A store that just records save_flow / save_workflow calls."""

    def __init__(self):
        self.flows: list = []
        self.workflows: list = []

    def save_flow(self, flow):
        self.flows.append(flow)

    def save_workflow(self, wf):
        self.workflows.append(wf)


class TestSeedExampleFlows:
    def test_compiles_and_saves_flow_plus_workflows(self, tmp_path):
        from facetwork.examples import seed_example_flows

        ex = _make_local_example(tmp_path, "seedy", with_handlers=False, with_ffl=True)
        (ex / "ffl" / "seedy.ffl").write_text(_SEED_FFL)
        [pkg] = discover_local_examples(tmp_path)

        store = _RecordingStore()
        n_flows, n_workflows, _warnings = seed_example_flows(pkg, store, replace=False)

        assert (n_flows, n_workflows) == (1, 2)
        assert len(store.flows) == 1
        assert store.flows[0].name.path == "example:seedy"
        assert store.flows[0].name.name == "seedy"
        wf_names = sorted(w.name for w in store.workflows)
        assert wf_names == ["seedy.AddOneWorkflow", "seedy.DoubleAddOne"]
        # Every workflow links back to the seeded flow.
        assert {w.flow_id for w in store.workflows} == {store.flows[0].uuid}

    def test_no_ffl_files_returns_zero(self, tmp_path):
        from facetwork.examples import seed_example_flows

        _make_local_example(tmp_path, "handlers-only", with_handlers=True, with_ffl=False)
        [pkg] = discover_local_examples(tmp_path)

        store = _RecordingStore()
        assert seed_example_flows(pkg, store) == (0, 0, [])
        assert store.flows == [] and store.workflows == []

    def test_ffl_without_workflows_returns_zero(self, tmp_path):
        from facetwork.examples import seed_example_flows

        ex = _make_local_example(tmp_path, "facets-only", with_handlers=False, with_ffl=True)
        (ex / "ffl" / "facets-only.ffl").write_text(
            "namespace x {\n    event facet F(a: Long) => (b: Long)\n}\n"
        )
        [pkg] = discover_local_examples(tmp_path)

        store = _RecordingStore()
        n_flows, n_workflows, _w = seed_example_flows(pkg, store)
        assert (n_flows, n_workflows) == (0, 0)
        assert store.flows == []

    def test_replace_is_idempotent(self, tmp_path):
        mongomock = pytest.importorskip("mongomock")
        from facetwork.examples import seed_example_flows
        from facetwork.runtime.mongo_store import MongoStore

        ex = _make_local_example(tmp_path, "seedy", with_handlers=False, with_ffl=True)
        (ex / "ffl" / "seedy.ffl").write_text(_SEED_FFL)
        [pkg] = discover_local_examples(tmp_path)

        store = MongoStore(database_name="t_seed_idem", client=mongomock.MongoClient())
        try:
            seed_example_flows(pkg, store)
            seed_example_flows(pkg, store)  # re-seed should replace, not duplicate
            assert store._db.flows.count_documents({"name.path": "example:seedy"}) == 1
            assert store._db.workflows.count_documents({"namespace_id": "example:seedy"}) == 2
        finally:
            store.drop_database()
            store.close()

    def test_reseed_preserves_uuids(self, tmp_path):
        """Re-seeding must keep the same flow_id / workflow_ids.

        Regression test for the stress-test failure where a runner restart
        re-seeded mid-flight, regenerating UUIDs and orphaning in-flight
        bootstrap tasks (Flow not found / Workflow not found).
        """
        mongomock = pytest.importorskip("mongomock")
        from facetwork.examples import seed_example_flows
        from facetwork.runtime.mongo_store import MongoStore

        ex = _make_local_example(tmp_path, "seedy", with_handlers=False, with_ffl=True)
        (ex / "ffl" / "seedy.ffl").write_text(_SEED_FFL)
        [pkg] = discover_local_examples(tmp_path)

        store = MongoStore(database_name="t_seed_stable", client=mongomock.MongoClient())
        try:
            seed_example_flows(pkg, store)
            flow_id_before = store._db.flows.find_one({"name.path": "example:seedy"})["uuid"]
            wf_ids_before = {
                d["name"]: d["uuid"]
                for d in store._db.workflows.find({"flow_id": flow_id_before})
            }
            assert flow_id_before
            assert wf_ids_before  # at least one workflow

            seed_example_flows(pkg, store)  # re-seed
            flow_id_after = store._db.flows.find_one({"name.path": "example:seedy"})["uuid"]
            wf_ids_after = {
                d["name"]: d["uuid"]
                for d in store._db.workflows.find({"flow_id": flow_id_after})
            }
            assert flow_id_after == flow_id_before
            assert wf_ids_after == wf_ids_before
        finally:
            store.drop_database()
            store.close()
