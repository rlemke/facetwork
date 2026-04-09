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

"""Tests for module entry points and CLI coverage."""

import importlib


class TestMcpEntryPoint:
    def test_module_importable(self):
        mod = importlib.import_module("facetwork.mcp.__main__")
        assert mod is not None

    def test_main_exists(self):
        from facetwork.mcp.__main__ import main

        assert callable(main)


class TestDashboardEntryPoint:
    def test_module_importable(self):
        mod = importlib.import_module("facetwork.dashboard.__main__")
        assert mod is not None

    def test_main_exists(self):
        from facetwork.dashboard.__main__ import main

        assert callable(main)


class TestRunnerEntryPoint:
    def test_module_importable(self):
        mod = importlib.import_module("facetwork.runtime.runner.__main__")
        assert mod is not None

    def test_main_exists(self):
        from facetwork.runtime.runner.__main__ import main

        assert callable(main)


class TestCliCoverage:
    def test_check_flag(self, tmp_path):
        from facetwork.cli import main

        src = tmp_path / "test.ffl"
        src.write_text("facet Hello()")
        result = main(["--check", str(src)])
        assert result == 0

    def test_output_flag(self, tmp_path):
        from facetwork.cli import main

        src = tmp_path / "test.ffl"
        src.write_text("facet Hello()")
        out = tmp_path / "out.json"
        result = main([str(src), "-o", str(out)])
        assert result == 0
        assert out.exists()

    def test_invalid_file(self):
        from facetwork.cli import main

        result = main(["nonexistent_file.ffl"])
        assert result == 1
