"""Tests for the Claude workflow catalog (facetwork.catalog).

Exercises the service against an in-memory catalog store + MemoryStore for flow
materialization — fully offline, no Mongo.
"""

import pytest

from facetwork.catalog import (
    CatalogError,
    CatalogRunBlocked,
    CatalogService,
    InMemoryCatalogStore,
)
from facetwork.catalog.entities import STATUS_DRAFT, STATUS_PUBLISHED
from facetwork.runtime import MemoryStore

WF = """namespace claude.demo {
    schema R { message: String }
    event facet Greet(name: String) => (result: R)
    workflow Hello(name: String = "world") => (greeting: R) andThen {
        greeting = Greet(name = $.name)
    }
}"""

WF_V2 = WF.replace('"world"', '"earth"')

# Validator error (WORKFLOW_AT_TOP_LEVEL): parses, but workflow is outside a namespace.
INVALID = """workflow Oops(x: String = "a") => (y: String) andThen {
    yield Oops(y = $.x)
}"""

LIB = """namespace claude.lib.geo {
    schema Pt { lat: Double, lon: Double }
    event facet Locate(name: String) => (point: Pt)
}"""

DEP_WF = """namespace claude.demo2 {
    use claude.lib.geo
    workflow Find(name: String = "x") => (lat: Double) andThen {
        p = Locate(name = $.name)
        yield Find(lat = p.point.lat)
    }
}"""


class _FlowStore(MemoryStore):
    """MemoryStore + in-memory flow/workflow CRUD.

    Flows/workflows are a MongoStore concern in production (the catalog wires a
    MongoStore as its flow_store); this adds the same surface in memory so the
    store-agnostic CatalogService can be exercised fully offline.
    """

    def __init__(self):
        super().__init__()
        self._flow_defs = {}
        self._wf_defs = {}

    def save_flow(self, flow):
        self._flow_defs[flow.uuid] = flow

    def get_flow(self, flow_id):
        return self._flow_defs.get(flow_id)

    def save_workflow(self, wf):
        self._wf_defs[wf.uuid] = wf

    def get_workflow(self, workflow_id):
        return self._wf_defs.get(workflow_id)


def _svc():
    return CatalogService(InMemoryCatalogStore(), _FlowStore())


class _RegFlowStore(_FlowStore):
    """_FlowStore + a handler registry, so the run() preflight is exercised."""

    def __init__(self, regs):
        super().__init__()
        self._regs = list(regs)

    def list_handler_registrations(self):
        return list(self._regs)

    def get_handler_registration(self, name):
        return next((r for r in self._regs if r.facet_name == name), None)


def _reg(facet_name):
    # module_uri="json" is importable, so preload(verify=True) keeps it.
    from facetwork.runtime.entities.server import HandlerRegistration

    return HandlerRegistration(facet_name=facet_name, module_uri="json", entrypoint="dumps")


def test_save_creates_draft_and_materializes_viewable_flow():
    svc = _svc()
    r = svc.save("demo.hello", ffl_source=WF, title="Hello", description="greets", tags=["demo"])
    assert r.ok and r.version == 1 and r.is_valid and r.status == STATUS_DRAFT
    # Materialized FlowDefinition is viewable (same path the dashboard renders).
    flow = svc._flows.get_flow(r.flow_id)
    assert flow is not None and flow.compiled_sources[0].content
    assert flow.name.path == "claude:demo.hello:v1"
    # Entry workflow + param schema surfaced for the UI / Claude.
    detail = svc.get("demo.hello")
    assert detail["entry_workflow"] == "claude.demo.Hello"
    assert any(p["name"] == "name" and p["default"] == "world" for p in detail["param_schema"])


def test_dedup_identical_content_no_new_version():
    svc = _svc()
    r1 = svc.save("demo.hello", ffl_source=WF)
    r2 = svc.save("demo.hello", ffl_source=WF, description="only metadata changed")
    assert r2.deduped and r2.version == r1.version == 1
    assert len(svc._catalog.get_revisions_for_slug("demo.hello")) == 1


def test_edit_bumps_version_old_revision_immutable():
    svc = _svc()
    r1 = svc.save("demo.hello", ffl_source=WF)
    r2 = svc.save("demo.hello", ffl_source=WF_V2)
    assert r2.version == 2 and not r2.deduped
    assert [x.version for x in svc._catalog.get_revisions_for_slug("demo.hello")] == [1, 2]
    v1 = svc._catalog.get_revision_by_version("demo.hello", 1)
    assert '"world"' in v1.ffl_source and v1.flow_id == r1.flow_id  # unchanged
    assert r2.flow_id != r1.flow_id  # new immutable flow per revision


def test_publish_gate_blocks_unattended_run():
    svc = _svc()
    svc.save("demo.hello", ffl_source=WF)
    with pytest.raises(CatalogRunBlocked):
        svc.run("demo.hello")  # draft → blocked
    # Attended override is allowed (interactive test run).
    res = svc.run("demo.hello", allow_unpublished=True, inputs={"name": "a"})
    assert res["workflow"] == "claude.demo.Hello"
    # After publish, an unattended run works.
    rev = svc.publish("demo.hello")
    assert rev.status == STATUS_PUBLISHED
    assert svc.run("demo.hello", inputs={"name": "b"})["version"] == 1


def test_run_creates_bootstrap_task_and_runner():
    svc = _svc()
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    res = svc.run("demo.hello", inputs={"name": "z"})
    task = svc._flows.get_task(res["task_id"])
    assert task.name == "fw:execute:claude.demo.Hello"
    assert task.data["inputs"] == {"name": "z"}
    assert task.data["flow_id"] and task.data["workflow_id"]
    assert svc._flows.get_runner(res["runner_id"]) is not None


def test_rerun_with_different_params_reuses_pinned_revision():
    svc = _svc()
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    a = svc.run("demo.hello", inputs={"name": "a"})
    b = svc.run("demo.hello", inputs={"name": "b"})
    assert a["version"] == b["version"]  # same pinned revision
    ta, tb = svc._flows.get_task(a["task_id"]), svc._flows.get_task(b["task_id"])
    assert ta.flow_id == tb.flow_id  # identical workflow body
    assert ta.data["inputs"] != tb.data["inputs"]  # only params differ


def test_invalid_ffl_stored_as_draft_but_not_publishable_or_runnable():
    svc = _svc()
    r = svc.save("bad.oops", ffl_source=INVALID)
    assert r.ok and not r.is_valid and r.warnings
    with pytest.raises(CatalogError):
        svc.publish("bad.oops")
    with pytest.raises(CatalogError):
        svc.run("bad.oops", allow_unpublished=True)


def test_library_composition_merges_and_pins_dependency():
    svc = _svc()
    lib = svc.save("lib.geo", kind="library", ffl_source=LIB)
    svc.publish("lib.geo")
    dep = svc.save("demo.find", ffl_source=DEP_WF, depends_on=[{"slug": "lib.geo"}])
    assert dep.ok and dep.is_valid, dep.warnings  # merged compile resolved Locate/Pt
    rev = svc._catalog.get_revision_by_version("demo.find", 1)
    assert rev.depends_on and rev.depends_on[0].slug == "lib.geo"
    assert rev.depends_on[0].version == lib.version
    # The library FFL was merged into the materialized flow source.
    flow = svc._flows.get_flow(dep.flow_id)
    assert "claude.lib.geo" in flow.compiled_sources[0].content


def test_library_change_does_not_alter_pinned_dependent():
    svc = _svc()
    svc.save("lib.geo", kind="library", ffl_source=LIB)
    svc.publish("lib.geo")
    svc.save("demo.find", ffl_source=DEP_WF, depends_on=[{"slug": "lib.geo"}])
    pinned = svc._catalog.get_revision_by_version("demo.find", 1).depends_on[0].revision_id
    # Evolve the base library → new library revision.
    svc.save(
        "lib.geo", kind="library", ffl_source=LIB.replace("lon: Double", "lon: Double, alt: Double")
    )
    # The dependent's pinned dependency is unchanged.
    still = svc._catalog.get_revision_by_version("demo.find", 1).depends_on[0].revision_id
    assert still == pinned


def test_search_ranks_by_query_and_filters_by_tag():
    svc = _svc()
    svc.save(
        "demo.hello", ffl_source=WF, title="Hello", description="greets a name", tags=["greeting"]
    )
    assert any(s["slug"] == "demo.hello" for s in svc.search("greet"))
    assert any(s["slug"] == "demo.hello" for s in svc.search("", tags=["greeting"]))
    assert svc.search("nonexistent-zzz") == []


def test_run_preflight_blocks_when_event_facet_handler_missing():
    # Registry is non-empty (a dummy handler) but lacks a handler for the
    # workflow's event facet (claude.demo.Greet) -> run is refused up front.
    svc = CatalogService(InMemoryCatalogStore(), _RegFlowStore([_reg("other.Thing")]))
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    with pytest.raises(CatalogRunBlocked) as ei:
        svc.run("demo.hello", inputs={"name": "x"})
    assert "claude.demo.Greet" in str(ei.value)


def test_run_preflight_passes_when_handler_registered():
    svc = CatalogService(InMemoryCatalogStore(), _RegFlowStore([_reg("claude.demo.Greet")]))
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    out = svc.run("demo.hello", inputs={"name": "x"})  # preflight passes
    assert out["workflow"] == "claude.demo.Hello"


def test_run_mints_fresh_execution_workflow_id_per_run():
    # Each run gets its own execution workflow_id (distinct from the definition
    # and from other runs) so a prior run's state can't collide.
    svc = CatalogService(InMemoryCatalogStore(), _RegFlowStore([_reg("claude.demo.Greet")]))
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    rev = svc._catalog.get_revision_by_version("demo.hello", 1)

    r1 = svc.run("demo.hello", inputs={"name": "a"})
    r2 = svc.run("demo.hello", inputs={"name": "b"})

    assert r1["workflow_id"] != r2["workflow_id"]
    assert r1["workflow_id"] != rev.workflow_id and r2["workflow_id"] != rev.workflow_id
    assert r1["workflow"] == r2["workflow"] == rev.entry_workflow
    # both per-run WorkflowDefinitions reference the same immutable flow
    wf1 = svc._flows.get_workflow(r1["workflow_id"])
    wf2 = svc._flows.get_workflow(r2["workflow_id"])
    assert wf1.flow_id == wf2.flow_id == rev.flow_id
    # the bootstrap task carries the execution id, not the definition id
    assert r1["workflow_id"] != rev.workflow_id


def test_run_preflight_skipped_when_registry_empty():
    # No registrations at all (no runner up) -> can't assess -> don't false-block.
    svc = CatalogService(InMemoryCatalogStore(), _RegFlowStore([]))
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    out = svc.run("demo.hello", inputs={"name": "x"})
    assert out["workflow"] == "claude.demo.Hello"


def test_run_preflight_catches_unimportable_handler_module(tmp_path):
    # A handler whose MODULE won't import (broken top-level import) is caught by
    # check_loadable before launch.
    from facetwork.runtime.entities.server import HandlerRegistration

    h = tmp_path / "brokenmodule.py"
    h.write_text(
        "import totally_missing_top_level_pkg_xyz\n\ndef handle(payload):\n    return {}\n"
    )
    reg = HandlerRegistration(
        facet_name="claude.demo.Greet", module_uri=f"file://{h}", entrypoint="handle"
    )
    svc = CatalogService(InMemoryCatalogStore(), _RegFlowStore([reg]))
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    with pytest.raises(CatalogRunBlocked) as ei:
        svc.run("demo.hello", inputs={"name": "x"})
    assert "claude.demo.Greet" in str(ei.value)


def test_run_preflight_passes_handler_with_lazy_sibling_import(tmp_path):
    # A shared handler module may host other facets' handlers with their own
    # (possibly broken) lazy imports; that must NOT false-block this facet — the
    # module imports + the entrypoint is callable, so the preflight passes.
    from facetwork.runtime.entities.server import HandlerRegistration

    h = tmp_path / "sharedmodule.py"
    h.write_text(
        "def handle(payload):\n"
        "    return {'result': {}}\n\n"
        "def _other_facet(payload):\n"
        "    from totally_missing_sibling_pkg import x  # a sibling's broken lazy import\n"
        "    return x\n"
    )
    reg = HandlerRegistration(
        facet_name="claude.demo.Greet", module_uri=f"file://{h}", entrypoint="handle"
    )
    svc = CatalogService(InMemoryCatalogStore(), _RegFlowStore([reg]))
    svc.save("demo.hello", ffl_source=WF)
    svc.publish("demo.hello")
    out = svc.run("demo.hello", inputs={"name": "x"})  # not false-blocked
    assert out["workflow"] == "claude.demo.Hello"


def test_save_records_authoring_summary_and_dedup_refreshes_it():
    svc = _svc()
    svc.save(
        "demo.hello",
        ffl_source=WF,
        summary="Greets a user by name. Built from the 'greeter' request.",
    )
    assert "greet" in svc.get("demo.hello")["summary"].lower()
    # Re-saving identical FFL de-dupes but updates the (descriptive) summary.
    r2 = svc.save("demo.hello", ffl_source=WF, summary="Refined: greets by name, returns schema R.")
    assert r2.deduped and r2.version == 1
    assert "Refined" in svc.get("demo.hello")["summary"]


def test_list_all_groups_packages_and_members():
    svc = _svc()
    svc.save("lib.geo", kind="library", ffl_source=LIB)
    svc.publish("lib.geo")
    svc.save("demo.find", ffl_source=DEP_WF, depends_on=[{"slug": "lib.geo"}])
    svc.save("demo.hello", ffl_source=WF)  # standalone (no library dep)

    rows = svc.list_all()
    by_slug = {r["slug"]: r for r in rows}
    # Libraries sort first.
    assert rows[0]["slug"] == "lib.geo" and rows[0]["kind"] == "library"
    # The library counts its dependents; the dependent records its package.
    assert by_slug["lib.geo"]["member_count"] == 1
    assert by_slug["demo.find"]["package"] == "lib.geo"
    assert by_slug["demo.find"]["depends_on"] == ["lib.geo"]
    # A standalone workflow has no package and no members.
    assert by_slug["demo.hello"]["package"] is None
    assert by_slug["demo.hello"]["member_count"] == 0
