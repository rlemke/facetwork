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

"""Tests for the v3 Infra page (MinIO console + mongo-express links)."""

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


@pytest.fixture
def client():
    """Test client with a mongomock-backed store (mirrors test_routes.py)."""
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    mock_client = mongomock.MongoClient()
    store = MongoStore(database_name="afl_test_infra", client=mock_client)

    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store

    with TestClient(app) as tc:
        yield tc, store

    store.drop_database()
    store.close()


class TestInfraPage:
    def test_page_renders(self, client):
        tc, _ = client
        resp = tc.get("/v3/infra")
        assert resp.status_code == 200
        assert "MinIO Console" in resp.text
        assert "Mongo Express" in resp.text

    def test_default_urls_derive_from_request_host(self, client, monkeypatch):
        # No env override → URLs derived from the request host + standard ports.
        monkeypatch.delenv("FW_MINIO_CONSOLE_URL", raising=False)
        monkeypatch.delenv("FW_MONGO_UI_URL", raising=False)
        tc, _ = client
        resp = tc.get("/v3/infra")
        assert resp.status_code == 200
        # TestClient uses host "testserver".
        assert "http://testserver:9001" in resp.text
        assert "http://testserver:8081" in resp.text

    def test_env_override_wins(self, client, monkeypatch):
        monkeypatch.setenv("FW_MINIO_CONSOLE_URL", "http://minio.example:9999")
        monkeypatch.setenv("FW_MONGO_UI_URL", "http://mongo.example:8888")
        tc, _ = client
        resp = tc.get("/v3/infra")
        assert resp.status_code == 200
        assert "http://minio.example:9999" in resp.text
        assert "http://mongo.example:8888" in resp.text

    def test_nav_active(self, client):
        tc, _ = client
        resp = tc.get("/v3/infra")
        # The Infra nav link is rendered active on this page.
        assert 'href="/v3/infra" class="active"' in resp.text
