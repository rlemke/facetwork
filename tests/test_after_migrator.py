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

"""Tests for the `dependency_signal` → `after` codemod.

Every shape here was found by running the migrator over the real fleet domains;
the awkward ones (a sole argument on its own line, a closing paren sharing a
line with `catch`, a sum of step references) are exactly where a naive
line-rewrite corrupts the source.
"""

from facetwork.cli import main as cli_main  # noqa: F401  (import guard)
from facetwork.migration.after_migrator import migrate_source
from facetwork.parser import FFLParser

HEADER = """namespace demo {
    event facet Download() => (count: Int, feature_count: Int)
    event facet Other() => (count: Int, feature_count: Int)
    event facet BuildMap(dependency_signal: Int = 0, region: String = "g") => (html_path: String)

"""


def _compiles(src: str) -> bool:
    FFLParser().parse(src)
    return True


def _wf(body: str) -> str:
    return HEADER + (
        "    workflow W() => (p: String) andThen {\n" + body + "\n    }\n}\n"
    )


class TestAfterMigrator:
    def test_plain_step_reference(self):
        src = _wf(
            "        data = Download()\n"
            "        map = BuildMap(dependency_signal = data.count)\n"
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert res.changed and not res.manual
        assert "map = BuildMap() after data" in res.source
        assert "dependency_signal" not in res.source.split("workflow")[1]
        assert _compiles(res.source)

    def test_keeps_other_arguments(self):
        src = _wf(
            "        data = Download()\n"
            '        map = BuildMap(region = "us", dependency_signal = data.count)\n'
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert 'BuildMap(region = "us") after data' in res.source
        assert _compiles(res.source)

    def test_literal_is_dropped_without_an_edge(self):
        """`dependency_signal = 0` never created an edge — nothing to preserve."""
        src = _wf(
            "        map = BuildMap(dependency_signal = 0)\n        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert "map = BuildMap()" in res.source
        assert "after" not in res.source.split("andThen")[1]
        assert _compiles(res.source)

    def test_sum_of_step_refs_becomes_a_fan_in(self):
        """The fan-in shape: a discarded sum whose only purpose was its edges."""
        src = _wf(
            "        a = Download()\n"
            "        b = Other()\n"
            "        map = BuildMap(dependency_signal = a.feature_count + b.feature_count)\n"
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert "map = BuildMap() after a, b" in res.source
        assert _compiles(res.source)

    def test_sole_argument_on_its_own_line_collapses_the_parens(self):
        """`F(\\n)` is not valid FFL — an empty arg list cannot span lines."""
        src = _wf(
            "        data = Download()\n"
            "        map = BuildMap(\n"
            "            dependency_signal = data.count\n"
            "        )\n"
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert "BuildMap() after data" in res.source
        assert _compiles(res.source)

    def test_trailing_comma_is_cleaned_up(self):
        src = _wf(
            "        data = Download()\n"
            "        map = BuildMap(\n"
            '            region = "us",\n'
            "            dependency_signal = data.count\n"
            "        )\n"
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert '"us"\n' in res.source and '"us",' not in res.source
        assert ") after data" in res.source
        assert _compiles(res.source)

    def test_clause_is_inserted_before_catch_not_after_it(self):
        """`) catch {` must become `) after x catch {` — order matters."""
        src = _wf(
            "        data = Download()\n"
            "        map = BuildMap(\n"
            "            dependency_signal = data.count\n"
            "        ) catch {\n"
            '            yield W(p = "")\n'
            "        }\n"
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert ") after data catch {" in res.source
        assert _compiles(res.source)

    def test_input_valued_signal_is_reported_not_guessed(self):
        """`$.x` isn't a step edge; the migrator must not invent one."""
        src = HEADER + (
            "    workflow W(n: Int = 0) => (p: String) andThen {\n"
            "        map = BuildMap(dependency_signal = $.n)\n"
            "        yield W(p = map.html_path)\n"
            "    }\n}\n"
        )
        res = migrate_source(src)
        assert not res.changed
        assert res.manual and "not a plain step" in res.manual[0][1]

    def test_untouched_source_is_unchanged(self):
        src = _wf("        data = Download()\n        yield W(p = data.count)")
        res = migrate_source(src)
        assert not res.changed and res.source == src

    def test_comments_and_formatting_survive(self):
        src = _wf(
            "        data = Download()\n"
            "        // keep me\n"
            "        map = BuildMap(dependency_signal = data.count)   // and me\n"
            "        yield W(p = map.html_path)"
        )
        res = migrate_source(src)
        assert "// keep me" in res.source and "// and me" in res.source
        assert _compiles(res.source)
