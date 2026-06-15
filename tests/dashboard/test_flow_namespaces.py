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
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

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
    from facetwork.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from facetwork.runtime.types import generate_id

    flow_id = generate_id()

    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name="demo-flow", path="test", uuid=flow_id),
        compiled_sources=[SourceText(name="source.ffl", content=VALID_AFL_SOURCE)],
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
    """Tests for namespace grouping on the v3 flow detail page.

    The v3 detail page (``/v3/flows/{id}``) groups workflows by namespace
    inline (collapsible ``<details>`` groups) — there are no separate
    ``/ns/{namespace}`` sub-pages. Each group shows the namespace name, a
    count badge, and short workflow names that link to the run form.
    """

    def test_namespace_groups_shown(self, client):
        """Detail page shows each namespace as a group."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert "osm.RegionMap" in resp.text
        assert "osm.Geocode" in resp.text
        # Top-level (unqualified) workflows group under system.unnamespaced.
        assert "system.unnamespaced" in resp.text

    def test_run_links_point_to_run_form(self, client):
        """Each workflow row links to the v3 run form by workflow UUID."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        for wf in wfs:
            assert f"/v3/flows/{flow.uuid}/run/{wf.uuid}" in resp.text

    def test_total_count_in_metrics(self, client):
        """The Workflows metric shows the total workflow count."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        # total_wf is rendered as the value of the "Workflows" metric.
        assert ">5</div>" in resp.text

    def test_namespace_counts(self, client):
        """Each namespace group shows its workflow count badge."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        html = resp.text
        # osm.Geocode has 2, osm.RegionMap has 2, system.unnamespaced has 1.
        # Counts render in a <span class="c"> badge.
        assert '<span class="c">2</span>' in html
        assert '<span class="c">1</span>' in html

    def test_top_level_group_for_unqualified(self, client):
        """Unqualified workflows appear under the system.unnamespaced group."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert "system.unnamespaced" in resp.text
        # The unqualified SimpleWF appears under that group.
        assert "SimpleWF" in resp.text

    def test_short_names_in_rows(self, client):
        """Workflow rows show short names, not fully qualified names."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        # Short names render as the row label.
        assert "Address" in resp.text
        assert "BicycleMap" in resp.text
        # The namespace prefix is the group header, not part of the row label,
        # so the fully qualified name never appears verbatim.
        assert "osm.Geocode.Address" not in resp.text
        assert "osm.RegionMap.BicycleMap" not in resp.text

    def test_missing_flow(self, client):
        """Detail page handles a missing flow gracefully."""
        tc, store = client
        resp = tc.get("/v3/flows/nonexistent")
        assert resp.status_code == 200
        assert "Flow not found" in resp.text

    def test_source_json_links(self, client):
        """Detail page links to the v3 source and JSON code views."""
        tc, store = client
        flow, wfs = _seed_namespaced_flow(store)
        resp = tc.get(f"/v3/flows/{flow.uuid}")
        assert resp.status_code == 200
        assert f"/v3/flows/{flow.uuid}/source" in resp.text
        assert f"/v3/flows/{flow.uuid}/json" in resp.text
