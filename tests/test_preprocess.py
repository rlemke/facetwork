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

"""Tests for the pre-lex preprocessor (brace-delimited script blocks)."""

import pytest

from afl.preprocess import PreprocessError, preprocess_script_braces


class TestPreprocessScriptBraces:
    """Tests for preprocess_script_braces()."""

    def test_simple_single_line(self):
        """Single-line script block with braces."""
        source = 'facet F() script { x = 1 }'
        result = preprocess_script_braces(source)
        assert 'script "x = 1"' in result

    def test_multiline(self):
        """Multiline script block preserves code."""
        source = 'facet F() script {\n    x = 1\n    y = 2\n}'
        result = preprocess_script_braces(source)
        assert 'script "' in result
        assert "x = 1" in result
        assert "y = 2" in result

    def test_nested_braces_in_python_dict(self):
        """Python dict literals with braces inside script block."""
        source = 'facet F() script {\n    data = {"a": 1, "b": 2}\n}'
        result = preprocess_script_braces(source)
        assert 'script "' in result
        # The code should be extractable — no unbalanced brace error
        assert "PreprocessError" not in result

    def test_python_strings_containing_braces(self):
        """Braces inside Python string literals are ignored."""
        source = "facet F() script {\n    s = '{not a brace}'\n}"
        result = preprocess_script_braces(source)
        assert 'script "' in result

    def test_afl_comments_around_script(self):
        """AFL comments near script blocks are preserved."""
        source = '// comment\nfacet F() script { x = 1 }\n// end'
        result = preprocess_script_braces(source)
        assert '// comment' in result
        assert '// end' in result
        assert 'script "' in result

    def test_script_python_keyword(self):
        """script python { code } preserves the python keyword."""
        source = 'facet F() script python { x = 1 }'
        result = preprocess_script_braces(source)
        assert 'script python "' in result

    def test_quoted_string_passthrough(self):
        """Already-quoted script blocks pass through unchanged."""
        source = 'facet F() script "x = 1"'
        result = preprocess_script_braces(source)
        assert result == source

    def test_unbalanced_brace_error(self):
        """Unbalanced braces raise PreprocessError."""
        source = 'facet F() script { x = 1'
        with pytest.raises(PreprocessError, match="Unbalanced brace"):
            preprocess_script_braces(source)

    def test_line_preservation(self):
        """Line count is preserved after preprocessing."""
        source = 'facet F() script {\n    x = 1\n    y = 2\n}\nfacet G()'
        result = preprocess_script_braces(source)
        # Same number of newlines
        assert source.count('\n') == result.count('\n')

    def test_empty_braces(self):
        """Empty script block produces empty string."""
        source = 'facet F() script {}'
        result = preprocess_script_braces(source)
        assert 'script ""' in result

    def test_andthen_script_braces(self):
        """andThen script { code } is also converted."""
        source = 'facet F() andThen script { y = 2 }'
        result = preprocess_script_braces(source)
        assert 'andThen script "' in result

    def test_multiple_script_blocks(self):
        """Multiple script blocks in same source are all converted."""
        source = 'facet F() script { x = 1 }\nfacet G() script { y = 2 }'
        result = preprocess_script_braces(source)
        assert result.count('script "') == 2

    def test_dedent_multiline(self):
        """Multiline code is dedented to remove common indentation."""
        source = 'facet F() script {\n    x = 1\n    y = 2\n}'
        result = preprocess_script_braces(source)
        # After preprocessing, the code should be "x = 1\ny = 2" (dedented)
        assert "x = 1\\ny = 2" in result

    def test_python_triple_quoted_string(self):
        """Triple-quoted Python strings with braces inside are handled."""
        source = "facet F() script {\n    s = '''{\n    multi}'''\n}"
        result = preprocess_script_braces(source)
        assert 'script "' in result

    def test_afl_block_comment_not_preprocessed(self):
        """script inside AFL block comments is not converted."""
        source = '/* script { x = 1 } */\nfacet F()'
        result = preprocess_script_braces(source)
        # The block comment is preserved as-is
        assert '/* script { x = 1 } */' in result

    def test_afl_string_literal_not_preprocessed(self):
        """script inside AFL string literals is not converted."""
        source = 'facet F(x: String = "script { code }")'
        result = preprocess_script_braces(source)
        assert result == source
