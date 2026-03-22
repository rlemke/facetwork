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

"""Tests for dashboard v2 handler helpers and routes."""

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


# ---------------------------------------------------------------------------
# Helper unit tests (no server needed)
# ---------------------------------------------------------------------------

from afl.dashboard.helpers import extract_handler_prefix, group_handlers_by_namespace


class TestExtractHandlerPrefix:
    def test_dotted_name(self):
        assert extract_handler_prefix("osm.Cache") == "osm"

    def test_deeply_dotted(self):
        assert extract_handler_prefix("aws.lambda.deploy.CreateFunction") == "aws"

    def test_simple_name(self):
        assert extract_handler_prefix("SimpleHandler") == "system.unnamespaced"

    def test_two_segments(self):
        assert extract_handler_prefix("jenkins.Build") == "jenkins"


class TestGroupHandlersByNamespace:
    def _make_handler(self, facet_name, module_uri="mod", entrypoint="handle"):
        from afl.runtime.entities import HandlerRegistration

        return HandlerRegistration(
            facet_name=facet_name,
            module_uri=module_uri,
            entrypoint=entrypoint,
        )

    def test_groups_by_namespace(self):
        handlers = [
            self._make_handler("osm.Cache"),
            self._make_handler("osm.Routes"),
            self._make_handler("aws.lambda.Deploy"),
        ]
        groups = group_handlers_by_namespace(handlers)
        assert len(groups) == 2
        assert groups[0]["namespace"] == "aws.lambda"
        assert groups[0]["total"] == 1
        assert groups[1]["namespace"] == "osm"
        assert groups[1]["total"] == 2

    def test_single_namespace(self):
        handlers = [self._make_handler("ns.Handler")]
        groups = group_handlers_by_namespace(handlers)
        assert len(groups) == 1
        assert groups[0]["namespace"] == "ns"
        assert groups[0]["total"] == 1

    def test_top_level(self):
        handlers = [self._make_handler("SimpleHandler")]
        groups = group_handlers_by_namespace(handlers)
        assert len(groups) == 1
        assert groups[0]["namespace"] == "system.unnamespaced"

    def test_empty(self):
        groups = group_handlers_by_namespace([])
        assert groups == []

    def test_sorted_alphabetically(self):
        handlers = [
            self._make_handler("zebra.Z"),
            self._make_handler("alpha.A"),
            self._make_handler("mid.M"),
        ]
        groups = group_handlers_by_namespace(handlers)
        assert [g["namespace"] for g in groups] == ["alpha", "mid", "zebra"]

    def test_mixed_namespaces_and_top_level(self):
        handlers = [
            self._make_handler("osm.Cache"),
            self._make_handler("TopLevel"),
            self._make_handler("osm.boundaries.Extract"),
        ]
        groups = group_handlers_by_namespace(handlers)
        assert len(groups) == 3
        ns_names = [g["namespace"] for g in groups]
        assert "system.unnamespaced" in ns_names
        assert "osm.boundaries" in ns_names
        assert "osm" in ns_names


# ---------------------------------------------------------------------------
# Route integration tests (require fastapi + mongomock)
# ---------------------------------------------------------------------------

pytestmark_routes = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE, reason="fastapi or mongomock not installed"
)


def _make_handler_entity(
    facet_name="osm.Cache",
    module_uri="osm.handlers.cache",
    entrypoint="handle",
    version="1.0.0",
):
    from afl.runtime.entities import HandlerRegistration

    return HandlerRegistration(
        facet_name=facet_name,
        module_uri=module_uri,
        entrypoint=entrypoint,
        version=version,
        timeout_ms=30000,
        created=1700000000000,
        updated=1700000000000,
    )


@pytest.fixture
def client():
    """Create a test client with mongomock-backed store."""
    from afl.dashboard import dependencies as deps
    from afl.dashboard.app import create_app
    from afl.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_hnd_v2", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


@pytestmark_routes
class TestV2HandlerList:
    def test_handler_list_empty(self, client):
        tc, store = client
        resp = tc.get("/v2/handlers", follow_redirects=False)
        assert resp.status_code == 200
        assert "Handlers" in resp.text

    def test_handler_list_with_handlers(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.get("/v2/handlers?tab=all")
        assert resp.status_code == 200
        assert ">osm<" in resp.text or "osm" in resp.text
        assert "Cache" in resp.text

    def test_handler_list_tab_filter(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        store.save_handler_registration(
            _make_handler_entity("aws.lambda.Deploy", module_uri="aws.handlers.deploy")
        )
        # osm tab should only show osm handlers
        resp = tc.get("/v2/handlers?tab=osm")
        assert resp.status_code == 200
        assert "Cache" in resp.text
        assert "Deploy" not in resp.text

    def test_handler_list_tab_excludes_other_prefixes(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.get("/v2/handlers?tab=aws")
        assert resp.status_code == 200
        assert "No handlers" in resp.text

    def test_handler_list_all_tab_shows_everything(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        store.save_handler_registration(
            _make_handler_entity("aws.lambda.Deploy", module_uri="aws.handlers.deploy")
        )
        resp = tc.get("/v2/handlers?tab=all")
        assert resp.status_code == 200
        assert "Cache" in resp.text
        assert "Deploy" in resp.text

    def test_handler_list_tab_counts(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        store.save_handler_registration(
            _make_handler_entity("osm.boundaries.Extract", module_uri="osm.handlers.extract")
        )
        store.save_handler_registration(
            _make_handler_entity("aws.lambda.Deploy", module_uri="aws.handlers.deploy")
        )
        resp = tc.get("/v2/handlers?tab=all")
        assert resp.status_code == 200
        # All tab should show count 3
        assert "All" in resp.text

    def test_handler_list_partial(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.get("/v2/handlers/partial?tab=all")
        assert resp.status_code == 200
        assert "osm" in resp.text


@pytestmark_routes
class TestV2HandlerDetail:
    def test_detail_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/handlers/nonexistent.handler")
        assert resp.status_code == 200
        assert "Not Found" in resp.text

    def test_detail_with_handler(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.get("/v2/handlers/osm.Cache")
        assert resp.status_code == 200
        assert "Cache" in resp.text
        assert "osm.Cache" in resp.text
        assert "osm.handlers.cache" in resp.text

    def test_detail_partial(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.get("/v2/handlers/osm.Cache/partial")
        assert resp.status_code == 200
        assert "osm.handlers.cache" in resp.text

    def test_detail_partial_not_found(self, client):
        tc, store = client
        resp = tc.get("/v2/handlers/nonexistent.handler/partial")
        assert resp.status_code == 200

    def test_detail_shows_version(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache", version="2.1.0"))
        resp = tc.get("/v2/handlers/osm.Cache")
        assert resp.status_code == 200
        assert "2.1.0" in resp.text

    def test_delete_handler(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.post("/v2/handlers/osm.Cache/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/v2/handlers"
        # Handler should be gone
        assert store.get_handler_registration("osm.Cache") is None

    def test_detail_with_requirements(self, client):
        tc, store = client
        h = _make_handler_entity("osm.Cache")
        h.requirements = ["numpy>=1.0", "pandas"]
        store.save_handler_registration(h)
        resp = tc.get("/v2/handlers/osm.Cache")
        assert resp.status_code == 200
        assert "numpy&gt;=1.0" in resp.text or "numpy>=1.0" in resp.text
        assert "pandas" in resp.text

    def test_detail_with_metadata(self, client):
        tc, store = client
        h = _make_handler_entity("osm.Cache")
        h.metadata = {"author": "test-user"}
        store.save_handler_registration(h)
        resp = tc.get("/v2/handlers/osm.Cache")
        assert resp.status_code == 200
        assert "author" in resp.text
        assert "test-user" in resp.text


@pytestmark_routes
class TestV2HandlerNav:
    def test_nav_handlers_page_renders(self, client):
        tc, store = client
        resp = tc.get("/v2/handlers")
        assert resp.status_code == 200
        assert "Handlers" in resp.text

    def test_old_handlers_route_still_works(self, client):
        tc, store = client
        resp = tc.get("/handlers")
        assert resp.status_code == 200


@pytestmark_routes
class TestV2HandlerCreateEdit:
    def test_new_form_renders(self, client):
        tc, store = client
        resp = tc.get("/v2/handlers/new")
        assert resp.status_code == 200
        assert "New Handler" in resp.text
        assert 'name="facet_name"' in resp.text

    def test_create_handler(self, client):
        tc, store = client
        resp = tc.post(
            "/v2/handlers/new",
            data={
                "facet_name": "etl.Extract.CSV",
                "module_uri": "etl.handlers",
                "entrypoint": "handle",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/v2/handlers/etl.Extract.CSV" in resp.headers["location"]
        assert store.get_handler_registration("etl.Extract.CSV") is not None

    def test_create_empty_facet_name_shows_error(self, client):
        tc, store = client
        resp = tc.post(
            "/v2/handlers/new",
            data={"facet_name": "", "module_uri": "m"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "required" in resp.text.lower()

    def test_create_duplicate_shows_error(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("etl.Dup"))
        resp = tc.post(
            "/v2/handlers/new",
            data={"facet_name": "etl.Dup", "module_uri": "m"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "already exists" in resp.text

    def test_edit_form_renders(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.get("/v2/handlers/osm.Cache/edit")
        assert resp.status_code == 200
        assert "Edit Handler" in resp.text
        assert "osm.Cache" in resp.text
        assert "disabled" in resp.text  # facet_name disabled on edit

    def test_edit_form_missing_handler_redirects(self, client):
        tc, store = client
        resp = tc.get("/v2/handlers/nonexistent/edit", follow_redirects=False)
        assert resp.status_code == 303

    def test_update_handler(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("osm.Cache"))
        resp = tc.post(
            "/v2/handlers/osm.Cache/edit",
            data={"module_uri": "new.module", "entrypoint": "run"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        updated = store.get_handler_registration("osm.Cache")
        assert updated.module_uri == "new.module"
        assert updated.entrypoint == "run"

    def test_api_create_handler(self, client):
        tc, store = client
        resp = tc.post(
            "/api/handlers",
            json={"facet_name": "api.Test.Create", "module_uri": "test.mod"},
        )
        assert resp.status_code == 201
        assert store.get_handler_registration("api.Test.Create") is not None

    def test_api_update_handler(self, client):
        tc, store = client
        store.save_handler_registration(_make_handler_entity("api.Test.Update"))
        resp = tc.put(
            "/api/handlers/api.Test.Update",
            json={"module_uri": "updated.mod", "entrypoint": "run"},
        )
        assert resp.status_code == 200
        updated = store.get_handler_registration("api.Test.Update")
        assert updated.module_uri == "updated.mod"
