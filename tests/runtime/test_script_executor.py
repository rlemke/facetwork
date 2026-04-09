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

"""Tests for FFL script executor."""

import pytest

from facetwork.runtime.script_executor import (
    ScriptError,
    ScriptExecutor,
    ScriptResult,
    execute_script,
)


class TestScriptExecutor:
    """Tests for ScriptExecutor class."""

    @pytest.fixture
    def executor(self):
        """Create a script executor."""
        return ScriptExecutor()

    def test_simple_assignment(self, executor):
        """Execute simple assignment to result."""
        result = executor.execute('result["output"] = 42')
        assert result.success
        assert result.result == {"output": 42}

    def test_params_access(self, executor):
        """Access params in script."""
        result = executor.execute(
            'result["doubled"] = params["x"] * 2',
            params={"x": 21},
        )
        assert result.success
        assert result.result == {"doubled": 42}

    def test_string_manipulation(self, executor):
        """String manipulation in script."""
        result = executor.execute(
            'result["upper"] = params["text"].upper()',
            params={"text": "hello"},
        )
        assert result.success
        assert result.result == {"upper": "HELLO"}

    def test_list_operations(self, executor):
        """List operations in script."""
        result = executor.execute(
            'result["sum"] = sum(params["numbers"])',
            params={"numbers": [1, 2, 3, 4, 5]},
        )
        assert result.success
        assert result.result == {"sum": 15}

    def test_dict_operations(self, executor):
        """Dict operations in script."""
        code = """
data = params["input"]
result["keys"] = list(data.keys())
result["values"] = list(data.values())
"""
        result = executor.execute(code, params={"input": {"a": 1, "b": 2}})
        assert result.success
        assert result.result["keys"] == ["a", "b"]
        assert result.result["values"] == [1, 2]

    def test_conditional_logic(self, executor):
        """Conditional logic in script."""
        code = """
if params["value"] > 10:
    result["status"] = "large"
else:
    result["status"] = "small"
"""
        result = executor.execute(code, params={"value": 15})
        assert result.success
        assert result.result == {"status": "large"}

    def test_loop(self, executor):
        """Loop in script."""
        code = """
total = 0
for item in params["items"]:
    total += item
result["total"] = total
"""
        result = executor.execute(code, params={"items": [1, 2, 3]})
        assert result.success
        assert result.result == {"total": 6}

    def test_multiple_results(self, executor):
        """Multiple result values."""
        code = """
result["a"] = 1
result["b"] = 2
result["c"] = params["x"] + params["y"]
"""
        result = executor.execute(code, params={"x": 10, "y": 20})
        assert result.success
        assert result.result == {"a": 1, "b": 2, "c": 30}

    def test_empty_result(self, executor):
        """Script that produces no results."""
        result = executor.execute("x = 1")
        assert result.success
        assert result.result == {}

    def test_syntax_error(self, executor):
        """Script with syntax error."""
        result = executor.execute("result[ = 1")  # Invalid syntax
        assert not result.success
        assert "Syntax error" in result.error

    def test_runtime_error(self, executor):
        """Script with runtime error."""
        result = executor.execute("x = 1 / 0")
        assert not result.success
        assert "ZeroDivisionError" in result.error

    def test_key_error(self, executor):
        """Script with missing param key."""
        result = executor.execute('result["x"] = params["missing"]', params={})
        assert not result.success
        assert "KeyError" in result.error

    def test_unsupported_language(self, executor):
        """Unsupported script language."""
        result = executor.execute("print('hello')", language="javascript")
        assert not result.success
        assert "Unsupported script language" in result.error

    def test_no_import(self, executor):
        """Import of disallowed modules should fail."""
        result = executor.execute("import os")
        assert not result.success
        # Either NameError (no __import__) or ImportError

    def test_anthropic_import_allowed(self, executor):
        """anthropic is in the allowed import list (not blocked by sandbox)."""
        from facetwork.runtime.script_executor import _SAFE_IMPORT_MODULES

        assert "anthropic" in _SAFE_IMPORT_MODULES

        # If package is installed, verify it actually imports in sandbox
        try:
            import anthropic  # noqa: F401

            result = executor.execute(
                'import anthropic\nresult["imported"] = hasattr(anthropic, "Anthropic")'
            )
            assert result.success
            assert result.result["imported"] is True
        except ImportError:
            pytest.skip("anthropic package not installed")

    def test_no_open(self, executor):
        """open() should not be available."""
        result = executor.execute('f = open("/etc/passwd")')
        assert not result.success
        assert "NameError" in result.error or "open" in result.error

    def test_no_eval(self, executor):
        """eval() should not be available."""
        result = executor.execute('x = eval("1 + 1")')
        assert not result.success

    def test_safe_builtins_available(self, executor):
        """Safe builtins should be available."""
        code = """
result["len"] = len([1, 2, 3])
result["sum"] = sum([1, 2, 3])
result["max"] = max([1, 2, 3])
result["sorted"] = sorted([3, 1, 2])
result["isinstance"] = isinstance(42, int)
"""
        result = executor.execute(code)
        assert result.success
        assert result.result["len"] == 3
        assert result.result["sum"] == 6
        assert result.result["max"] == 3
        assert result.result["sorted"] == [1, 2, 3]
        assert result.result["isinstance"] is True

    def test_params_not_modifiable_externally(self, executor):
        """Params should be copied so modifications don't affect original."""
        original_params = {"x": 1}
        result = executor.execute(
            'params["x"] = 999; result["x"] = params["x"]',
            params=original_params,
        )
        assert result.success
        # Original params should be unchanged
        assert original_params["x"] == 1


class TestScriptTimeout:
    """Tests for subprocess timeout enforcement."""

    def test_timeout_enforced(self):
        """Script exceeding timeout should fail with timed out error."""
        executor = ScriptExecutor(timeout=1)
        result = executor.execute("while True: pass")
        assert not result.success
        assert "timed out" in result.error

    def test_fast_script_within_timeout(self):
        """Script completing within timeout should succeed."""
        executor = ScriptExecutor(timeout=5)
        result = executor.execute('result["x"] = 42')
        assert result.success
        assert result.result == {"x": 42}

    def test_default_timeout(self):
        """Default timeout should be 30 seconds."""
        executor = ScriptExecutor()
        assert executor.timeout == 30.0


class TestSubprocessEdgeCases:
    """Tests for subprocess execution edge cases."""

    def test_non_json_serializable_params(self):
        """Non-JSON-serializable params should fail before subprocess."""
        executor = ScriptExecutor()
        result = executor.execute('result["x"] = 1', params={"obj": object()})
        assert not result.success
        assert "not serializable" in result.error

    def test_non_json_serializable_result(self):
        """Non-JSON-serializable result should produce an error."""
        executor = ScriptExecutor()
        result = executor.execute('result["s"] = {1, 2, 3}')
        assert not result.success

    def test_large_result(self):
        """Large results should serialize correctly."""
        executor = ScriptExecutor()
        result = executor.execute('result["data"] = list(range(1000))')
        assert result.success
        assert result.result["data"] == list(range(1000))

    def test_print_does_not_corrupt_output(self):
        """print() in user code should not corrupt JSON output."""
        executor = ScriptExecutor()
        result = executor.execute('print("debug info")\nresult["x"] = 42')
        assert result.success
        assert result.result == {"x": 42}


class TestExecuteScriptFunction:
    """Tests for execute_script convenience function."""

    def test_success(self):
        """Successful script execution."""
        result = execute_script(
            'result["x"] = params["a"] + params["b"]',
            params={"a": 1, "b": 2},
        )
        assert result == {"x": 3}

    def test_error_raises(self):
        """Script error raises ScriptError."""
        with pytest.raises(ScriptError):
            execute_script("x = 1 / 0")

    def test_syntax_error_raises(self):
        """Syntax error raises ScriptError."""
        with pytest.raises(ScriptError, match="Syntax error"):
            execute_script("result[ = 1")


class TestScriptResult:
    """Tests for ScriptResult dataclass."""

    def test_success_result(self):
        """Success result."""
        result = ScriptResult(success=True, result={"x": 1})
        assert result.success
        assert result.result == {"x": 1}
        assert result.error is None

    def test_error_result(self):
        """Error result."""
        result = ScriptResult(success=False, result={}, error="Something went wrong")
        assert not result.success
        assert result.result == {}
        assert result.error == "Something went wrong"


class TestScriptError:
    """Tests for ScriptError exception."""

    def test_message(self):
        """Error message."""
        error = ScriptError("test error")
        assert str(error) == "test error"
        assert error.original is None

    def test_with_original(self):
        """Error with original exception."""
        original = ValueError("original")
        error = ScriptError("wrapped", original=original)
        assert str(error) == "wrapped"
        assert error.original is original
