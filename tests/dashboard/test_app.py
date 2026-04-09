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
    """Test that all expected routes are registered."""
    from facetwork.dashboard.app import create_app

    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/" in routes
    assert "/runners" in routes
    assert "/flows" in routes
    assert "/servers" in routes
    assert "/tasks" in routes
    assert "/api/runners" in routes
    assert "/api/flows" in routes
    assert "/api/servers" in routes


def test_static_files_mounted():
    """Test that static files are mounted."""
    from facetwork.dashboard.app import create_app

    app = create_app()
    route_paths = [r.path for r in app.routes]
    # Static files mount shows as /static in routes
    assert any("/static" in str(p) for p in route_paths)
