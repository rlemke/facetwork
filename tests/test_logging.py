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

"""Tests for afl.logging — SplunkJsonFormatter and configure_logging."""

from __future__ import annotations

import json
import logging
import re

import pytest

from facetwork.logging import SplunkJsonFormatter, configure_logging

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    message: str = "hello",
    level: int = logging.INFO,
    name: str = "test.logger",
    exc_info: tuple | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=exc_info,
    )
    return record


# ---------------------------------------------------------------------------
# SplunkJsonFormatter — output structure
# ---------------------------------------------------------------------------


class TestSplunkJsonFormatter:
    def test_output_is_valid_json(self):
        fmt = SplunkJsonFormatter()
        record = _make_record()
        line = fmt.format(record)
        obj = json.loads(line)
        assert isinstance(obj, dict)

    def test_required_fields_present(self):
        fmt = SplunkJsonFormatter()
        record = _make_record()
        obj = json.loads(fmt.format(record))
        assert set(obj.keys()) == {"timestamp", "level", "logger", "message", "source"}

    def test_timestamp_iso8601_utc(self):
        fmt = SplunkJsonFormatter()
        record = _make_record()
        obj = json.loads(fmt.format(record))
        ts = obj["timestamp"]
        # ISO 8601 with ms and Z suffix: 2026-02-23T12:34:56.789Z
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts)

    def test_level_matches_record(self):
        fmt = SplunkJsonFormatter()
        for level, name in [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
        ]:
            record = _make_record(level=level)
            obj = json.loads(fmt.format(record))
            assert obj["level"] == name

    def test_logger_field(self):
        fmt = SplunkJsonFormatter()
        record = _make_record(name="facetwork.runtime.evaluator")
        obj = json.loads(fmt.format(record))
        assert obj["logger"] == "facetwork.runtime.evaluator"

    def test_message_field(self):
        fmt = SplunkJsonFormatter()
        record = _make_record(message="workflow started")
        obj = json.loads(fmt.format(record))
        assert obj["message"] == "workflow started"

    def test_source_field_is_facetwork(self):
        fmt = SplunkJsonFormatter()
        record = _make_record()
        obj = json.loads(fmt.format(record))
        assert obj["source"] == "facetwork"

    def test_exc_info_included_when_present(self):
        fmt = SplunkJsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc = sys.exc_info()

        record = _make_record(exc_info=exc)
        obj = json.loads(fmt.format(record))
        assert "exc_info" in obj
        assert "ValueError: boom" in obj["exc_info"]
        assert "Traceback" in obj["exc_info"]

    def test_exc_info_omitted_when_absent(self):
        fmt = SplunkJsonFormatter()
        record = _make_record()
        obj = json.loads(fmt.format(record))
        assert "exc_info" not in obj

    def test_output_is_compact_single_line(self):
        fmt = SplunkJsonFormatter()
        record = _make_record()
        line = fmt.format(record)
        # No spaces after separators (compact), single line
        assert " : " not in line
        assert "\n" not in line


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _reset_root_logger(self):
        """Remove handlers added during tests so they don't leak."""
        root = logging.getLogger()
        before = list(root.handlers)
        yield
        root.handlers = before

    def test_json_format_installs_splunk_formatter(self):
        configure_logging(level="DEBUG", log_format="json")
        root = logging.getLogger()
        json_handlers = [h for h in root.handlers if isinstance(h.formatter, SplunkJsonFormatter)]
        assert len(json_handlers) >= 1

    def test_text_format_installs_plain_formatter(self):
        configure_logging(level="DEBUG", log_format="text")
        root = logging.getLogger()
        text_handlers = [
            h
            for h in root.handlers
            if h.formatter is not None and not isinstance(h.formatter, SplunkJsonFormatter)
        ]
        assert len(text_handlers) >= 1
