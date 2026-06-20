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

"""Tests for dashboard app creation and route registration."""

import pytest

try:
    from fastapi.testclient import TestClient  # noqa: F401

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

pytestmark = pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="fastapi not installed")


def test_create_app():
    """Test that create_app returns a valid FastAPI app."""
    from facetwork.dashboard.app import create_app

    app = create_app()
    assert app is not None
    assert app.title == "Facetwork Dashboard"


def test_app_has_routes():
    """The expected routes are registered.

    Checked behaviorally via TestClient (a non-404 response means the route
    exists) rather than by introspecting ``app.routes`` — fastapi 0.136+ makes
    ``include_router`` lazy, so ``app.routes`` holds opaque ``_IncludedRouter``
    entries with no ``.path``. This keeps the test robust across fastapi versions.
    """
    from fastapi.testclient import TestClient

    from facetwork.dashboard.app import create_app

    # Legacy page routes were removed in favour of the v3 UI; the kept redirect
    # (`/runners`→/v3/workflows) plus the v3 pages stand in for them.
    expected = [
        "/", "/runners", "/v3/flows", "/v3/servers", "/v3/tasks",
        "/api/runners", "/api/flows", "/api/servers",
    ]
    client = TestClient(create_app(), raise_server_exceptions=False)
    for path in expected:
        # follow_redirects=False so a redirect route (e.g. /runners) stays 3xx;
        # any status other than 404 means the route is registered (500 = it
        # exists but errored without a backing DB, which still proves wiring).
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code != 404, f"route {path} not registered (404)"


def test_static_files_mounted():
    """Static files are served (behavioral check, robust to route representation)."""
    from fastapi.testclient import TestClient

    from facetwork.dashboard.app import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    resp = client.get("/static/style.css")
    assert resp.status_code == 200, "static mount not serving /static/style.css"
