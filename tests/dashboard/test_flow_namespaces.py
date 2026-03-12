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

"""Integration tests for namespace-level navigation on the flow detail page."""

import pytest

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE, reason="fastapi or mongomock not installed"
)


VALID_AFL_SOURCE = """
facet Compute(input: Long)

workflow SimpleWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield SimpleWF(result = s1.input)
}
"""


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from afl.dashboard import dependencies as deps
    from afl.dashboard.app import create_app
    from afl.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_flow_ns", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


def _seed_namespaced_flow(store):
    """Seed a flow with workflows spanning multiple namespaces.

    Creates:
    - osm.Geocode.Address, osm.Geocode.BatchGeocode  (namespace osm.Geocode, 2 wf)
    - osm.RegionMap.BicycleMap, osm.RegionMap.HikingMap  (namespace osm.RegionMap, 2 wf)
    - SimpleWF  (top-level, 1 wf)
    """
    from afl.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from afl.runtime.types import generate_id

    flow_id = generate_id()

    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name="osm-geocoder", path="test", uuid=flow_id),
        compiled_sources=[SourceText(name="source.afl", content=VALID_AFL_SOURCE)],
    )
    store.save_flow(flow)

    workflow_names = [
        "osm.Geocode.Address",
        "osm.Geocode.BatchGeocode",
        "osm.RegionMap.BicycleMap",
        "osm.RegionMap.HikingMap",
        "SimpleWF",
    ]
    workflows = []
    for name in workflow_names:
        wf_id = generate_id()
        wf = WorkflowDefinition(
            uuid=wf_id,
            name=name,
            namespace_id="docker:seed",
            facet_id=wf_id,
            flow_id=flow_id,
            starting_step="",
            version="1.0",
            date=0,
        )
        store.save_workflow(wf)
        workflows.append(wf)

    return flow, workflows


class TestFlowDetailNamespaces:
    """Tests for namespace grouping on the flow detail page."""

    def test_namespace_list_shown(self, client):
        """Detail page shows namespace list instead of flat workflows."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        # Should show namespace names, not individual workflow names
        assert "osm.RegionMap" in resp.text
        assert "osm.Geocode" in resp.text
        assert "(top-level)" in resp.text

    def test_namespace_links(self, client):
        """Detail page has links to /ns/ routes."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert f"/flows/{flow.uuid}/ns/osm.Geocode" in resp.text
        assert f"/flows/{flow.uuid}/ns/osm.RegionMap" in resp.text
        assert f"/flows/{flow.uuid}/ns/_top" in resp.text

    def test_total_count_in_heading(self, client):
        """Heading shows total workflow count."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert "Workflows (5)" in resp.text

    def test_namespace_counts(self, client):
        """Each namespace row shows correct workflow count."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        # The table should contain count cells
        html = resp.text
        # osm.Geocode has 2, osm.RegionMap has 2, (top-level) has 1
        assert ">2<" in html
        assert ">1<" in html

    def test_top_level_group_for_unqualified(self, client):
        """Unqualified workflows appear under (top-level) group."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert "(top-level)" in resp.text
        assert f"/flows/{flow.uuid}/ns/_top" in resp.text

    def test_no_flat_workflow_names(self, client):
        """Detail page should NOT show individual workflow names in the table."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}")
        assert resp.status_code == 200
        # Individual workflow names should not appear in the namespace table
        assert "Address" not in resp.text
        assert "BicycleMap" not in resp.text


class TestFlowNamespaceView:
    """Tests for GET /flows/{flow_id}/ns/{namespace_name}."""

    def test_correct_workflows_shown(self, client):
        """Namespace view shows only workflows from that namespace."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.Geocode")
        assert resp.status_code == 200
        assert "Address" in resp.text
        assert "BatchGeocode" in resp.text
        # Should NOT show RegionMap workflows (different namespace)
        assert "BicycleMap" not in resp.text
        assert "HikingMap" not in resp.text

    def test_short_names_used(self, client):
        """Namespace view shows short names, not fully qualified names."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.Geocode")
        assert resp.status_code == 200
        # Short names should appear
        assert "Address" in resp.text
        # Full qualified names should NOT appear
        assert "osm.Geocode.Address" not in resp.text

    def test_nested_namespace(self, client):
        """Dotted namespace path works for nested namespaces."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.RegionMap")
        assert resp.status_code == 200
        assert "BicycleMap" in resp.text
        assert "HikingMap" in resp.text
        assert "Address" not in resp.text

    def test_other_namespaces_excluded(self, client):
        """Namespace view excludes workflows from other namespaces."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.RegionMap")
        assert resp.status_code == 200
        assert "Address" not in resp.text
        assert "BatchGeocode" not in resp.text
        assert "SimpleWF" not in resp.text

    def test_run_button_present(self, client):
        """Namespace view shows Run buttons for each workflow."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.Geocode")
        assert resp.status_code == 200
        assert "Run</a>" in resp.text
        # Check run links point to workflow UUIDs
        geo_wfs = [w for w in wfs if w.name.startswith("osm.Geocode")]
        for wf in geo_wfs:
            assert f"/flows/{flow.uuid}/run/{wf.uuid}" in resp.text

    def test_back_link(self, client):
        """Namespace view has a back link to the flow detail page."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.Geocode")
        assert resp.status_code == 200
        assert f"/flows/{flow.uuid}" in resp.text
        assert "Back to flow" in resp.text

    def test_source_json_links(self, client):
        """Namespace view shows source and JSON links when compiled_sources exists."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.Geocode")
        assert resp.status_code == 200
        assert f"/flows/{flow.uuid}/source" in resp.text
        assert f"/flows/{flow.uuid}/json" in resp.text

    def test_missing_flow(self, client):
        """Namespace view handles missing flow gracefully."""
        tc, store = client
        resp = tc.get("/flows/nonexistent/ns/osm.Geocode")
        assert resp.status_code == 200
        assert "Flow not found" in resp.text

    def test_empty_namespace(self, client):
        """Namespace view returns empty table for nonexistent namespace."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/nonexistent.namespace")
        assert resp.status_code == 200
        assert "Workflows (0)" in resp.text

    def test_top_level_namespace(self, client):
        """_top prefix shows only top-level (unqualified) workflows."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/_top")
        assert resp.status_code == 200
        assert "SimpleWF" in resp.text
        assert "(top-level)" in resp.text
        # Should not show namespaced workflows
        assert "Address" not in resp.text
        assert "BicycleMap" not in resp.text

    def test_namespace_heading(self, client):
        """Namespace view shows flow name and namespace in heading."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/flows/{flow.uuid}/ns/osm.Geocode")
        assert resp.status_code == 200
        assert "osm-geocoder" in resp.text
        assert "osm.Geocode" in resp.text
