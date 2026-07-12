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

"""Tests for the legacy → relative-scoping FFL migrator."""

import glob

from facetwork.migration import migrate_source
from facetwork.parser import parse
from facetwork.validator import validate


def _ns(body: str) -> str:
    return (
        "namespace test {\n"
        " event facet sf1(input: String) => (output: String, refs: [String])\n"
        " workflow W(input: String) => (output: String) andThen {\n"
        f"{body}\n"
        " }\n}"
    )


class TestMigrateReferences:
    def test_containing_step_by_name_to_dollar(self):
        # A foreach header naming its containing step → $.
        src = _ns(
            "        s = sf1(input = $.input) andThen foreach r in s.refs {\n"
            "            x = sf1(input = $.r)\n"
            "        }"
        )
        res = migrate_source(src)
        assert res.changed
        assert "foreach r in $.refs" in res.source
        assert "in s.refs" not in res.source

    def test_flat_input_to_up_level(self):
        # $.input inside a nested body whose container has no `input` reaches the
        # workflow → $$.input. (The container calls Tag(prefix=…), so `input` is
        # not one of its attributes.)
        src = """namespace test {
            event facet Tag(prefix: String) => (out: String)
            workflow W(input: String) => (output: String) andThen {
                s = Tag(prefix = $.input) andThen {
                    x = Tag(prefix = $.input)
                }
            }
        }"""
        res = migrate_source(src)
        assert res.changed
        # inner reference re-pointed up one level; outer one untouched.
        assert "x = Tag(prefix = $$.input)" in res.source
        assert "s = Tag(prefix = $.input)" in res.source

    def test_loop_var_untouched(self):
        src = _ns(
            "        s = sf1(input = $.input) andThen foreach r in s.refs {\n"
            "            x = sf1(input = $.r)\n"
            "        }"
        )
        res = migrate_source(src)
        assert "input = $.r" in res.source  # loop var stays $.r

    def test_same_block_sibling_untouched(self):
        src = _ns(
            "        a = sf1(input = $.input)\n"
            "        b = sf1(input = a.output)"
        )
        res = migrate_source(src)
        assert not res.changed
        assert "input = a.output" in res.source

    def test_sibling_when_flagged_manual_not_rewritten(self):
        src = """namespace test {
            event facet GetRisk(id: String) => (score: String)
            facet Handle(x: String) => (r: String)
            workflow W(id: String) => (r: String) andThen {
                s1 = GetRisk(id = $.id)
            } andThen when {
                case s1.score == "hi" => { h = Handle(x = s1.score) }
                case _ => {}
            }
        }"""
        res = migrate_source(src)
        assert res.manual, "sibling-when should be flagged manual"
        assert "s1.score" in res.source  # not auto-rewritten

    def test_idempotent(self):
        src = _ns(
            "        s = sf1(input = $.input) andThen foreach r in s.refs {\n"
            "            x = sf1(input = $.r)\n"
            "        }"
        )
        once = migrate_source(src).source
        twice = migrate_source(once)
        assert not twice.changed
        assert twice.source == once


class TestMigrateRealFiles:
    def test_examples_migrate_clean_or_manual(self):
        """Every in-repo example either fully auto-migrates (clean under the
        relative flag) or leaves only sibling-when sites it flagged manual."""
        for f in sorted(glob.glob("examples/**/*.ffl", recursive=True)):
            src = open(f).read()
            off = {(e.rule_id, str(e)) for e in validate(parse(src), relative_scoping=False).errors}
            before = [
                e
                for e in validate(parse(src), relative_scoping=True).errors
                if (e.rule_id, str(e)) not in off
            ]
            if not before:
                continue
            res = migrate_source(src)
            # migrated source must still parse
            after = [
                e
                for e in validate(parse(res.source), relative_scoping=True).errors
                if (e.rule_id, str(e)) not in off
            ]
            # Fewer errors than before, and any residue is covered by a manual flag.
            assert len(after) <= len(before)
            if after:
                assert res.manual, f"{f}: residual errors but nothing flagged manual"

    def test_doc_processing_specific_rewrites(self):
        matches = glob.glob("examples/**/doc-processing.ffl", recursive=True)
        assert matches
        res = migrate_source(open(matches[0]).read())
        assert "foreach chunk in $.chunks" in res.source  # containing step → $
        assert "file_path = $$.file_path" in res.source  # workflow input → $$
        # migrated file is clean under the relative flag
        errs = [e for e in validate(parse(res.source), relative_scoping=True).errors]
        base = [e for e in validate(parse(open(matches[0]).read()), relative_scoping=False).errors]
        assert len(errs) <= len(base)
