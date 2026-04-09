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

"""Tests for Jinja2 template filters."""

from facetwork.dashboard.filters import (
    doc_description,
    doc_params,
    doc_returns,
    duration_fmt,
    state_color,
    state_label,
    step_state_bg,
    timestamp_fmt,
    truncate_uuid,
)


class TestTimestampFmt:
    def test_zero_returns_dash(self):
        assert timestamp_fmt(0) == "—"

    def test_none_returns_dash(self):
        assert timestamp_fmt(None) == "—"

    def test_valid_timestamp(self):
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        result = timestamp_fmt(1704067200000)
        assert "2024" in result
        assert "01" in result

    def test_custom_format(self):
        result = timestamp_fmt(1704067200000, fmt="%Y")
        assert "2024" in result
        assert "data-ts=" in result


class TestDurationFmt:
    def test_zero_returns_dash(self):
        assert duration_fmt(0) == "—"

    def test_none_returns_dash(self):
        assert duration_fmt(None) == "—"

    def test_seconds(self):
        assert duration_fmt(5000) == "5s"

    def test_minutes(self):
        assert duration_fmt(90000) == "1m 30s"

    def test_hours(self):
        assert duration_fmt(3_660_000) == "1h 1m"


class TestStateColor:
    def test_running(self):
        assert state_color("running") == "primary"

    def test_completed(self):
        assert state_color("completed") == "success"

    def test_failed(self):
        assert state_color("failed") == "danger"

    def test_paused(self):
        assert state_color("paused") == "warning"

    def test_none(self):
        assert state_color(None) == "secondary"

    def test_unknown(self):
        assert state_color("some_weird_state") == "secondary"

    def test_dotted_state(self):
        # Extracts last segment
        assert state_color("state.facet.completion.Complete") == "secondary"


class TestStateLabel:
    def test_simple(self):
        assert state_label("running") == "running"

    def test_dotted(self):
        assert state_label("state.facet.initialization.Begin") == "Begin"

    def test_none(self):
        assert state_label(None) == "unknown"


class TestTruncateUuid:
    def test_truncate(self):
        assert truncate_uuid("abcdef12-3456-7890") == "abcdef12"

    def test_custom_length(self):
        assert truncate_uuid("abcdef12-3456-7890", length=4) == "abcd"

    def test_none(self):
        assert truncate_uuid(None) == "—"


# =============================================================================
# v0.11.2 — Additional edge-case tests for filter functions
# =============================================================================


class TestDurationFmtEdgeCases:
    def test_negative_value(self):
        # Negative ms is truthy, so it should not return the dash
        result = duration_fmt(-5000)
        # Negative divided by 1000 truncated to int => -5 seconds
        assert "s" in result

    def test_very_large_value_one_day(self):
        # 86400000 ms = 1 day = 24h 0m
        result = duration_fmt(86400000)
        assert "24h" in result
        assert "0m" in result

    def test_exact_minute_boundary(self):
        # 60000 ms = exactly 1 minute 0 seconds
        result = duration_fmt(60000)
        assert "1m" in result
        assert "0s" in result


class TestTimestampFmtEdgeCases:
    def test_negative_timestamp(self):
        # Negative timestamp is truthy, should not return dash
        result = timestamp_fmt(-1000)
        # Should format as a date before epoch
        assert "1969" in result or "1970" in result

    def test_far_future_timestamp(self):
        # Year 2100: approx 4102444800000 ms
        result = timestamp_fmt(4102444800000)
        assert "2100" in result


class TestStateColorEdgeCases:
    def test_all_known_states_covered(self):
        known = {
            "running": "primary",
            "completed": "success",
            "failed": "danger",
            "paused": "warning",
            "cancelled": "secondary",
            "error": "danger",
            "pending": "warning",
        }
        for state_val, expected_color in known.items():
            assert state_color(state_val) == expected_color, (
                f"state_color('{state_val}') should be '{expected_color}'"
            )

    def test_empty_string_returns_secondary(self):
        assert state_color("") == "secondary"


# =============================================================================
# v0.12.37 — Doc comment filter tests
# =============================================================================


class TestDocDescription:
    def test_none_returns_empty(self):
        assert doc_description(None) == ""

    def test_plain_string(self):
        result = doc_description("Hello world")
        assert "Hello world" in result

    def test_dict_with_description(self):
        result = doc_description({"description": "Hello **bold**", "params": [], "returns": []})
        assert "Hello" in result
        assert "<strong>bold</strong>" in result or "**bold**" in result

    def test_dict_empty_description(self):
        result = doc_description({"description": "", "params": [], "returns": []})
        assert result == ""

    def test_non_string_non_dict_returns_empty(self):
        assert doc_description(42) == ""


class TestDocParams:
    def test_none_returns_empty_list(self):
        assert doc_params(None) == []

    def test_string_returns_empty_list(self):
        assert doc_params("some doc") == []

    def test_dict_with_params(self):
        doc = {
            "description": "desc",
            "params": [{"name": "x", "description": "The x"}],
            "returns": [],
        }
        result = doc_params(doc)
        assert len(result) == 1
        assert result[0]["name"] == "x"

    def test_dict_without_params_key(self):
        assert doc_params({"description": "desc"}) == []


class TestDocReturns:
    def test_none_returns_empty_list(self):
        assert doc_returns(None) == []

    def test_string_returns_empty_list(self):
        assert doc_returns("some doc") == []

    def test_dict_with_returns(self):
        doc = {
            "description": "desc",
            "params": [],
            "returns": [{"name": "result", "description": "The result"}],
        }
        result = doc_returns(doc)
        assert len(result) == 1
        assert result[0]["name"] == "result"

    def test_dict_without_returns_key(self):
        assert doc_returns({"description": "desc"}) == []


# =============================================================================
# v0.12.79 — step_state_bg filter tests
# =============================================================================


class TestStepStateBg:
    def test_none_returns_other(self):
        assert step_state_bg(None) == "state-bg-other"

    def test_empty_string_returns_other(self):
        assert step_state_bg("") == "state-bg-other"

    def test_complete_state(self):
        assert step_state_bg("state.facet.completion.Complete") == "state-bg-complete"

    def test_error_state(self):
        assert step_state_bg("state.facet.error.Error") == "state-bg-error"

    def test_event_transmit_state(self):
        assert step_state_bg("state.facet.event.EventTransmit") == "state-bg-transmit"

    def test_created_state(self):
        assert step_state_bg("state.step.Created") == "state-bg-running"

    def test_continue_state(self):
        assert step_state_bg("state.facet.Continue") == "state-bg-continue"

    def test_unknown_state_returns_other(self):
        assert step_state_bg("state.facet.SomeWeirdState") == "state-bg-other"

    def test_simple_complete(self):
        assert step_state_bg("Complete") == "state-bg-complete"

    def test_case_insensitive(self):
        assert step_state_bg("COMPLETE") == "state-bg-complete"
        assert step_state_bg("error") == "state-bg-error"
        assert step_state_bg("EventTransmit") == "state-bg-transmit"

    def test_state_color_created_is_primary(self):
        """Created state should map to primary (active), not secondary."""
        assert state_color("created") == "primary"
