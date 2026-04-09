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

"""Tests for shared dashboard partials (_state_badge, _empty_state, _attrs_table)."""

from pathlib import Path

import pytest

try:
    from jinja2 import Environment, FileSystemLoader

    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False

pytestmark = pytest.mark.skipif(not JINJA2_AVAILABLE, reason="jinja2 not installed")

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "afl" / "dashboard" / "templates"


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


class TestEmptyStatePartial:
    """Test _empty_state.html partial."""

    def test_default_icon_and_message(self, env):
        """Default empty state should use search icon."""
        tmpl = env.get_template("partials/_empty_state.html")
        html = tmpl.render()
        assert "empty-state" in html
        assert "No items found." in html

    def test_custom_icon_and_message(self, env):
        """Custom icon and message should render."""
        tmpl = env.get_template("partials/_empty_state.html")
        html = tmpl.render(empty_icon="&#x2699;", empty_message="No servers found.")
        assert "No servers found." in html
        assert "&#x2699;" in html


class TestAttrsTablePartial:
    """Test _attrs_table.html partial."""

    class FakeAttr:
        def __init__(self, value, type_hint="String"):
            self.value = value
            self.type_hint = type_hint

    def test_full_table(self, env):
        """Full table should include Name, Value, Type headers."""
        tmpl = env.get_template("partials/_attrs_table.html")
        attrs = {"name": self.FakeAttr("Alice"), "age": self.FakeAttr(30, "Int")}
        html = tmpl.render(attrs=attrs)
        assert "<th>Name</th>" in html
        assert "<th>Value</th>" in html
        assert "<th>Type</th>" in html
        assert "Alice" in html

    def test_compact_table(self, env):
        """Compact table should omit headers."""
        tmpl = env.get_template("partials/_attrs_table.html")
        attrs = {"name": self.FakeAttr("Alice")}
        html = tmpl.render(attrs=attrs, attrs_compact=True)
        assert "<th>" not in html
        assert "Alice" in html
