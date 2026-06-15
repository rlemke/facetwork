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

"""Tests for shared dashboard partials (_state_badge, _empty_state)."""

from pathlib import Path

import pytest

try:
    from jinja2 import Environment, FileSystemLoader

    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False

pytestmark = pytest.mark.skipif(not JINJA2_AVAILABLE, reason="jinja2 not installed")

_TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "facetwork" / "dashboard" / "templates"
)


@pytest.fixture
def env():
    """Create a Jinja2 environment with dashboard filters."""
    from facetwork.dashboard.filters import register_filters

    jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
    register_filters(jinja_env)
    return jinja_env


class TestStateBadgePartial:
    """Test _state_badge.html partial."""

    def test_running_state(self, env):
        """Running state should render badge-primary."""
        tmpl = env.get_template("partials/_state_badge.html")
        html = tmpl.render(state="running")
        assert "badge-primary" in html
        assert "running" in html

    def test_completed_state(self, env):
        """Completed state should render badge-success."""
        tmpl = env.get_template("partials/_state_badge.html")
        html = tmpl.render(state="completed")
        assert "badge-success" in html
        assert "completed" in html

    def test_failed_state(self, env):
        """Failed state should render badge-danger."""
        tmpl = env.get_template("partials/_state_badge.html")
        html = tmpl.render(state="failed")
        assert "badge-danger" in html
        assert "failed" in html

    def test_custom_label(self, env):
        """Custom label should override state text."""
        tmpl = env.get_template("partials/_state_badge.html")
        html = tmpl.render(state="state.statement.Error", label="Error")
        assert "Error" in html
        assert "badge-danger" in html

    def test_unknown_state(self, env):
        """Unknown state should render badge-secondary."""
        tmpl = env.get_template("partials/_state_badge.html")
        html = tmpl.render(state="something_else")
        assert "badge-secondary" in html
