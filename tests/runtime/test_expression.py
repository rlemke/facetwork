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

"""Tests for FFL expression evaluation."""

import pytest

from facetwork.runtime import (
    EvaluationContext,
    ExpressionEvaluator,
    evaluate_args,
    evaluate_default,
)
from facetwork.runtime.errors import EvaluationError, ReferenceError


class TestExpressionEvaluator:
    """Tests for ExpressionEvaluator."""

    @pytest.fixture
    def evaluator(self):
        """Create evaluator instance."""
        return ExpressionEvaluator()

    @pytest.fixture
    def basic_context(self):
        """Create basic evaluation context."""

        def get_step_output(step_name: str, attr_name: str):
            outputs = {
                "s1": {"input": 2, "output": 10},
                "s2": {"input": 3, "output": 20},
            }
            if step_name in outputs and attr_name in outputs[step_name]:
                return outputs[step_name][attr_name]
            raise ValueError(f"Not found: {step_name}.{attr_name}")

        return EvaluationContext(
            inputs={"input": 1, "name": "test"},
            get_step_output=get_step_output,
        )

    def test_literal_string(self, evaluator, basic_context):
        """Test string literal evaluation."""
        expr = {"type": "String", "value": "hello"}
        result = evaluator.evaluate(expr, basic_context)
        assert result == "hello"

    def test_literal_int(self, evaluator, basic_context):
        """Test integer literal evaluation."""
        expr = {"type": "Int", "value": 42}
        result = evaluator.evaluate(expr, basic_context)
        assert result == 42

    def test_literal_boolean(self, evaluator, basic_context):
        """Test boolean literal evaluation."""
        expr = {"type": "Boolean", "value": True}
        result = evaluator.evaluate(expr, basic_context)
        assert result is True

    def test_literal_null(self, evaluator, basic_context):
        """Test null literal evaluation."""
        expr = {"type": "Null"}
        result = evaluator.evaluate(expr, basic_context)
        assert result is None

    def test_literal_double(self, evaluator, basic_context):
        """Test double literal evaluation."""
        expr = {"type": "Double", "value": 3.14}
        result = evaluator.evaluate(expr, basic_context)
        assert result == 3.14
        assert isinstance(result, float)

    def test_input_ref(self, evaluator, basic_context):
        """Test input reference evaluation."""
        expr = {"type": "InputRef", "path": ["input"]}
        result = evaluator.evaluate(expr, basic_context)
        assert result == 1

    def test_input_ref_not_found(self, evaluator, basic_context):
        """Test input reference error for unknown input."""
        expr = {"type": "InputRef", "path": ["unknown"]}
        with pytest.raises(ReferenceError):
            evaluator.evaluate(expr, basic_context)

    def test_step_ref(self, evaluator, basic_context):
        """Test step reference evaluation."""
        expr = {"type": "StepRef", "path": ["s1", "input"]}
        result = evaluator.evaluate(expr, basic_context)
        assert result == 2

    def test_step_ref_not_found(self, evaluator, basic_context):
        """Test step reference error for unknown step."""
        expr = {"type": "StepRef", "path": ["unknown", "input"]}
        with pytest.raises(ReferenceError):
            evaluator.evaluate(expr, basic_context)

    def test_concat_expr(self, evaluator, basic_context):
        """Test concatenation expression."""
        expr = {
            "type": "ConcatExpr",
            "operands": [
                {"type": "String", "value": "Hello "},
                {"type": "InputRef", "path": ["name"]},
            ],
        }
        result = evaluator.evaluate(expr, basic_context)
        assert result == "Hello test"

    def test_binary_expr_add(self, evaluator, basic_context):
        """Test binary addition expression."""
        expr = {
            "type": "BinaryExpr",
            "operator": "+",
            "left": {"type": "Int", "value": 10},
            "right": {"type": "Int", "value": 5},
        }
        result = evaluator.evaluate(expr, basic_context)
        assert result == 15

    def test_binary_expr_with_refs(self, evaluator, basic_context):
        """Test binary expression with references."""
        expr = {
            "type": "BinaryExpr",
            "operator": "+",
            "left": {"type": "InputRef", "path": ["input"]},
            "right": {"type": "Int", "value": 1},
        }
        result = evaluator.evaluate(expr, basic_context)
        assert result == 2  # 1 + 1

    def test_foreach_variable(self, evaluator):
        """Test foreach variable access."""
        ctx = EvaluationContext(
            inputs={},
            get_step_output=lambda s, a: None,
            foreach_var="item",
            foreach_value=42,
        )

        expr = {"type": "InputRef", "path": ["item"]}
        result = evaluator.evaluate(expr, ctx)
        assert result == 42


class TestEvaluateArgs:
    """Tests for evaluate_args helper."""

    def test_evaluate_multiple_args(self):
        """Test evaluating multiple arguments."""
        args = [
            {"name": "a", "value": {"type": "Int", "value": 1}},
            {"name": "b", "value": {"type": "String", "value": "test"}},
        ]

        ctx = EvaluationContext(
            inputs={},
            get_step_output=lambda s, a: None,
        )

        result = evaluate_args(args, ctx)
        assert result == {"a": 1, "b": "test"}

    def test_evaluate_args_with_refs(self):
        """Test evaluating arguments with references."""
        args = [
            {"name": "value", "value": {"type": "InputRef", "path": ["input"]}},
        ]

        ctx = EvaluationContext(
            inputs={"input": 42},
            get_step_output=lambda s, a: None,
        )

        result = evaluate_args(args, ctx)
        assert result == {"value": 42}


# =========================================================================
# Edge cases for ExpressionEvaluator.evaluate
# =========================================================================


class TestExpressionEvaluateEdgeCases:
    """Tests for uncovered branches in ExpressionEvaluator.evaluate."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        return EvaluationContext(
            inputs={"x": 1},
            get_step_output=lambda s, a: None,
        )

    def test_evaluate_none(self, evaluator, ctx):
        """evaluate returns None for None input (line 59)."""
        assert evaluator.evaluate(None, ctx) is None

    def test_evaluate_raw_string(self, evaluator, ctx):
        """evaluate returns raw string passthrough (line 62)."""
        assert evaluator.evaluate("hello", ctx) == "hello"

    def test_evaluate_raw_int(self, evaluator, ctx):
        """evaluate returns raw int passthrough (line 62)."""
        assert evaluator.evaluate(42, ctx) == 42

    def test_evaluate_raw_float(self, evaluator, ctx):
        """evaluate returns raw float passthrough (line 62)."""
        assert evaluator.evaluate(3.14, ctx) == 3.14

    def test_evaluate_raw_bool(self, evaluator, ctx):
        """evaluate returns raw bool passthrough (line 62)."""
        assert evaluator.evaluate(True, ctx) is True

    def test_evaluate_list(self, evaluator, ctx):
        """evaluate maps over list elements (line 65)."""
        expr = [{"type": "Int", "value": 1}, {"type": "Int", "value": 2}]
        result = evaluator.evaluate(expr, ctx)
        assert result == [1, 2]

    def test_evaluate_non_dict_object(self, evaluator, ctx):
        """evaluate returns non-dict non-primitive as-is (line 68)."""
        obj = object()
        assert evaluator.evaluate(obj, ctx) is obj

    def test_evaluate_unknown_type_with_value(self, evaluator, ctx):
        """evaluate returns 'value' key for unknown type (lines 90-91)."""
        expr = {"type": "CustomThing", "value": 99}
        result = evaluator.evaluate(expr, ctx)
        assert result == 99

    def test_evaluate_unknown_type_no_value(self, evaluator, ctx):
        """evaluate raises EvaluationError for unknown type without value (lines 92-96)."""
        expr = {"type": "CustomThing"}
        with pytest.raises(EvaluationError):
            evaluator.evaluate(expr, ctx)


# =========================================================================
# Edge cases for input/step references
# =========================================================================


class TestReferenceEdgeCases:
    """Tests for uncovered reference resolution paths."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    def test_input_ref_empty_path(self, evaluator):
        """_eval_input_ref raises on empty path (line 113)."""
        ctx = EvaluationContext(
            inputs={},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "InputRef", "path": []}
        with pytest.raises(ReferenceError, match="Empty input reference"):
            evaluator.evaluate(expr, ctx)

    def test_step_ref_too_short(self, evaluator):
        """_eval_step_ref raises when path < 2 elements (line 152)."""
        ctx = EvaluationContext(
            inputs={},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "StepRef", "path": ["s1"]}
        with pytest.raises(ReferenceError, match="at least step.attribute"):
            evaluator.evaluate(expr, ctx)


# =========================================================================
# _resolve_path edge cases
# =========================================================================


class TestResolvePath:
    """Tests for _resolve_path nested access paths."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    def test_nested_input_dict_access(self, evaluator):
        """_resolve_path traverses nested dict (lines 199-206)."""
        ctx = EvaluationContext(
            inputs={"data": {"nested": {"deep": 42}}},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "InputRef", "path": ["data", "nested", "deep"]}
        result = evaluator.evaluate(expr, ctx)
        assert result == 42

    def test_nested_input_dict_property_not_found(self, evaluator):
        """_resolve_path raises when dict key missing (lines 201-205)."""
        ctx = EvaluationContext(
            inputs={"data": {"a": 1}},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "InputRef", "path": ["data", "missing"]}
        with pytest.raises(ReferenceError, match="not found"):
            evaluator.evaluate(expr, ctx)

    def test_nested_path_null_access(self, evaluator):
        """_resolve_path raises when accessing property on None (lines 192-197)."""
        ctx = EvaluationContext(
            inputs={"data": None},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "InputRef", "path": ["data", "field"]}
        with pytest.raises(ReferenceError, match="null"):
            evaluator.evaluate(expr, ctx)

    def test_nested_path_hasattr_access(self, evaluator):
        """_resolve_path uses getattr for non-dict objects (lines 207-208)."""

        class Obj:
            field = "hello"

        ctx = EvaluationContext(
            inputs={"obj": Obj()},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "InputRef", "path": ["obj", "field"]}
        result = evaluator.evaluate(expr, ctx)
        assert result == "hello"

    def test_nested_path_no_attr(self, evaluator):
        """_resolve_path raises when object has no attribute (lines 210-214)."""
        ctx = EvaluationContext(
            inputs={"val": 42},
            get_step_output=lambda s, a: None,
        )
        expr = {"type": "InputRef", "path": ["val", "missing"]}
        with pytest.raises(ReferenceError, match="Cannot access"):
            evaluator.evaluate(expr, ctx)

    def test_step_ref_nested_path(self, evaluator):
        """_resolve_path works for step refs with path > 2 (line 171)."""

        def get_step_output(step_name, attr_name):
            if step_name == "s1" and attr_name == "data":
                return {"nested": "value"}
            raise ValueError("Not found")

        ctx = EvaluationContext(
            inputs={},
            get_step_output=get_step_output,
        )
        expr = {"type": "StepRef", "path": ["s1", "data", "nested"]}
        result = evaluator.evaluate(expr, ctx)
        assert result == "value"


# =========================================================================
# Binary expression edge cases
# =========================================================================


class TestBinaryExprEdgeCases:
    """Tests for uncovered binary expression operators."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        return EvaluationContext(
            inputs={},
            get_step_output=lambda s, a: None,
        )

    def test_subtraction(self, evaluator, ctx):
        """BinaryExpr subtraction (line 256)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "-",
            "left": {"type": "Int", "value": 10},
            "right": {"type": "Int", "value": 3},
        }
        assert evaluator.evaluate(expr, ctx) == 7

    def test_multiplication(self, evaluator, ctx):
        """BinaryExpr multiplication (line 258)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "*",
            "left": {"type": "Int", "value": 4},
            "right": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) == 20

    def test_division(self, evaluator, ctx):
        """BinaryExpr division (line 266)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "/",
            "left": {"type": "Int", "value": 10},
            "right": {"type": "Int", "value": 2},
        }
        assert evaluator.evaluate(expr, ctx) == 5.0

    def test_division_by_zero(self, evaluator, ctx):
        """BinaryExpr division by zero raises EvaluationError (lines 260-265)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "/",
            "left": {"type": "Int", "value": 10},
            "right": {"type": "Int", "value": 0},
        }
        with pytest.raises(EvaluationError, match="Division by zero"):
            evaluator.evaluate(expr, ctx)

    def test_modulo(self, evaluator, ctx):
        """BinaryExpr modulo (line 268)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "%",
            "left": {"type": "Int", "value": 10},
            "right": {"type": "Int", "value": 3},
        }
        assert evaluator.evaluate(expr, ctx) == 1

    def test_unknown_operator(self, evaluator, ctx):
        """BinaryExpr unknown operator raises EvaluationError (lines 270-274)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "^",
            "left": {"type": "Int", "value": 2},
            "right": {"type": "Int", "value": 3},
        }
        with pytest.raises(EvaluationError, match="Unknown operator"):
            evaluator.evaluate(expr, ctx)

    def test_type_error_in_operation(self, evaluator, ctx):
        """BinaryExpr type mismatch raises EvaluationError (lines 275-280)."""
        expr = {
            "type": "BinaryExpr",
            "operator": "-",
            "left": {"type": "String", "value": "hello"},
            "right": {"type": "Int", "value": 1},
        }
        with pytest.raises(EvaluationError, match="Type error"):
            evaluator.evaluate(expr, ctx)


class TestCollectionEvaluation:
    """Tests for collection literal evaluation."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        def get_step_output(step_name, attr_name):
            outputs = {"s1": {"items": [10, 20, 30], "name": "test"}}
            if step_name in outputs and attr_name in outputs[step_name]:
                return outputs[step_name][attr_name]
            raise ValueError(f"Not found: {step_name}.{attr_name}")

        return EvaluationContext(
            inputs={"x": 5, "idx": 1},
            get_step_output=get_step_output,
        )

    def test_empty_array(self, evaluator, ctx):
        """Empty ArrayLiteral evaluates to empty list."""
        expr = {"type": "ArrayLiteral", "elements": []}
        assert evaluator.evaluate(expr, ctx) == []

    def test_array_with_literals(self, evaluator, ctx):
        """ArrayLiteral with literals evaluates correctly."""
        expr = {
            "type": "ArrayLiteral",
            "elements": [
                {"type": "Int", "value": 1},
                {"type": "Int", "value": 2},
                {"type": "Int", "value": 3},
            ],
        }
        assert evaluator.evaluate(expr, ctx) == [1, 2, 3]

    def test_array_with_refs(self, evaluator, ctx):
        """ArrayLiteral with input references evaluates correctly."""
        expr = {
            "type": "ArrayLiteral",
            "elements": [
                {"type": "InputRef", "path": ["x"]},
                {"type": "Int", "value": 10},
            ],
        }
        assert evaluator.evaluate(expr, ctx) == [5, 10]

    def test_nested_array(self, evaluator, ctx):
        """Nested ArrayLiteral evaluates correctly."""
        expr = {
            "type": "ArrayLiteral",
            "elements": [
                {"type": "ArrayLiteral", "elements": [{"type": "Int", "value": 1}]},
                {"type": "ArrayLiteral", "elements": [{"type": "Int", "value": 2}]},
            ],
        }
        assert evaluator.evaluate(expr, ctx) == [[1], [2]]

    def test_empty_map(self, evaluator, ctx):
        """Empty MapLiteral evaluates to empty dict."""
        expr = {"type": "MapLiteral", "entries": []}
        assert evaluator.evaluate(expr, ctx) == {}

    def test_map_with_literals(self, evaluator, ctx):
        """MapLiteral with literal values evaluates correctly."""
        expr = {
            "type": "MapLiteral",
            "entries": [
                {"key": "name", "value": {"type": "String", "value": "test"}},
                {"key": "count", "value": {"type": "Int", "value": 42}},
            ],
        }
        assert evaluator.evaluate(expr, ctx) == {"name": "test", "count": 42}

    def test_map_with_refs(self, evaluator, ctx):
        """MapLiteral with references evaluates correctly."""
        expr = {
            "type": "MapLiteral",
            "entries": [
                {"key": "val", "value": {"type": "InputRef", "path": ["x"]}},
            ],
        }
        assert evaluator.evaluate(expr, ctx) == {"val": 5}

    def test_index_on_array(self, evaluator, ctx):
        """IndexExpr on array evaluates correctly."""
        expr = {
            "type": "IndexExpr",
            "target": {
                "type": "ArrayLiteral",
                "elements": [
                    {"type": "String", "value": "a"},
                    {"type": "String", "value": "b"},
                    {"type": "String", "value": "c"},
                ],
            },
            "index": {"type": "Int", "value": 1},
        }
        assert evaluator.evaluate(expr, ctx) == "b"

    def test_index_on_map(self, evaluator, ctx):
        """IndexExpr on map evaluates correctly."""
        expr = {
            "type": "IndexExpr",
            "target": {
                "type": "MapLiteral",
                "entries": [
                    {"key": "name", "value": {"type": "String", "value": "test"}},
                ],
            },
            "index": {"type": "String", "value": "name"},
        }
        assert evaluator.evaluate(expr, ctx) == "test"

    def test_index_on_step_ref(self, evaluator, ctx):
        """IndexExpr on step reference evaluates correctly."""
        expr = {
            "type": "IndexExpr",
            "target": {"type": "StepRef", "path": ["s1", "items"]},
            "index": {"type": "Int", "value": 2},
        }
        assert evaluator.evaluate(expr, ctx) == 30

    def test_index_with_input_ref(self, evaluator, ctx):
        """IndexExpr with input ref as index evaluates correctly."""
        expr = {
            "type": "IndexExpr",
            "target": {"type": "StepRef", "path": ["s1", "items"]},
            "index": {"type": "InputRef", "path": ["idx"]},
        }
        assert evaluator.evaluate(expr, ctx) == 20

    def test_index_out_of_bounds(self, evaluator, ctx):
        """IndexExpr with invalid index raises EvaluationError."""
        expr = {
            "type": "IndexExpr",
            "target": {
                "type": "ArrayLiteral",
                "elements": [{"type": "Int", "value": 1}],
            },
            "index": {"type": "Int", "value": 99},
        }
        with pytest.raises(EvaluationError, match="Index error"):
            evaluator.evaluate(expr, ctx)

    def test_index_key_not_found(self, evaluator, ctx):
        """IndexExpr with missing key raises EvaluationError."""
        expr = {
            "type": "IndexExpr",
            "target": {
                "type": "MapLiteral",
                "entries": [
                    {"key": "a", "value": {"type": "Int", "value": 1}},
                ],
            },
            "index": {"type": "String", "value": "missing"},
        }
        with pytest.raises(EvaluationError, match="Index error"):
            evaluator.evaluate(expr, ctx)


class TestUnaryExprEvaluation:
    """Tests for UnaryExpr evaluation."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        def get_step_output(step_name, attr_name):
            outputs = {"s1": {"value": 42}}
            if step_name in outputs and attr_name in outputs[step_name]:
                return outputs[step_name][attr_name]
            raise ValueError(f"Not found: {step_name}.{attr_name}")

        return EvaluationContext(
            inputs={"x": 10, "neg": -3},
            get_step_output=get_step_output,
        )

    def test_negate_int(self, evaluator, ctx):
        """Negating an integer literal."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) == -5

    def test_negate_float(self, evaluator, ctx):
        """Negating a float literal."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "Double", "value": 3.14},
        }
        result = evaluator.evaluate(expr, ctx)
        assert result == pytest.approx(-3.14)

    def test_negate_input_ref(self, evaluator, ctx):
        """Negating a positive input reference."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "InputRef", "path": ["x"]},
        }
        assert evaluator.evaluate(expr, ctx) == -10

    def test_negate_negative_input(self, evaluator, ctx):
        """Negating a negative input reference gives positive."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "InputRef", "path": ["neg"]},
        }
        assert evaluator.evaluate(expr, ctx) == 3

    def test_double_negation(self, evaluator, ctx):
        """Double negation returns original value."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {
                "type": "UnaryExpr",
                "operator": "-",
                "operand": {"type": "Int", "value": 7},
            },
        }
        assert evaluator.evaluate(expr, ctx) == 7

    def test_negate_in_binary(self, evaluator, ctx):
        """Negation used as operand in binary expression."""
        expr = {
            "type": "BinaryExpr",
            "operator": "+",
            "left": {"type": "Int", "value": 10},
            "right": {
                "type": "UnaryExpr",
                "operator": "-",
                "operand": {"type": "Int", "value": 3},
            },
        }
        assert evaluator.evaluate(expr, ctx) == 7

    def test_negate_step_ref(self, evaluator, ctx):
        """Negating a step reference."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "StepRef", "path": ["s1", "value"]},
        }
        assert evaluator.evaluate(expr, ctx) == -42

    def test_negate_string_raises(self, evaluator, ctx):
        """Negating a string should raise EvaluationError."""
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "String", "value": "hello"},
        }
        with pytest.raises(EvaluationError, match="Type error"):
            evaluator.evaluate(expr, ctx)


class TestComparisonOperators:
    """Tests for comparison operator evaluation."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        def get_step_output(step_name: str, attr_name: str):
            outputs = {
                "s1": {"value": 42, "status": "success", "flag": True},
                "s2": {"value": 10, "status": "failed", "flag": False},
            }
            if step_name in outputs and attr_name in outputs[step_name]:
                return outputs[step_name][attr_name]
            raise ValueError(f"Not found: {step_name}.{attr_name}")

        return EvaluationContext(
            inputs={"x": 5, "y": 10, "active": True},
            get_step_output=get_step_output,
        )

    def test_equal_true(self, evaluator, ctx):
        """== returns True when equal."""
        expr = {
            "type": "BinaryExpr",
            "operator": "==",
            "left": {"type": "Int", "value": 5},
            "right": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_equal_false(self, evaluator, ctx):
        """== returns False when not equal."""
        expr = {
            "type": "BinaryExpr",
            "operator": "==",
            "left": {"type": "Int", "value": 5},
            "right": {"type": "Int", "value": 10},
        }
        assert evaluator.evaluate(expr, ctx) is False

    def test_not_equal(self, evaluator, ctx):
        """!= returns True when not equal."""
        expr = {
            "type": "BinaryExpr",
            "operator": "!=",
            "left": {"type": "String", "value": "a"},
            "right": {"type": "String", "value": "b"},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_greater_than(self, evaluator, ctx):
        """> operator."""
        expr = {
            "type": "BinaryExpr",
            "operator": ">",
            "left": {"type": "Int", "value": 10},
            "right": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_less_than(self, evaluator, ctx):
        """< operator."""
        expr = {
            "type": "BinaryExpr",
            "operator": "<",
            "left": {"type": "Int", "value": 3},
            "right": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_greater_equal(self, evaluator, ctx):
        """>= operator."""
        expr = {
            "type": "BinaryExpr",
            "operator": ">=",
            "left": {"type": "Int", "value": 5},
            "right": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_less_equal(self, evaluator, ctx):
        """<= operator."""
        expr = {
            "type": "BinaryExpr",
            "operator": "<=",
            "left": {"type": "Int", "value": 5},
            "right": {"type": "Int", "value": 5},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_comparison_with_refs(self, evaluator, ctx):
        """Comparison using step references."""
        expr = {
            "type": "BinaryExpr",
            "operator": ">",
            "left": {"type": "StepRef", "path": ["s1", "value"]},
            "right": {"type": "StepRef", "path": ["s2", "value"]},
        }
        assert evaluator.evaluate(expr, ctx) is True  # 42 > 10


class TestBooleanOperators:
    """Tests for boolean operator evaluation."""

    @pytest.fixture
    def evaluator(self):
        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        return EvaluationContext(
            inputs={"a": True, "b": False},
            get_step_output=lambda s, a: None,
        )

    def test_and_true(self, evaluator, ctx):
        """&& returns True when both true."""
        expr = {
            "type": "BinaryExpr",
            "operator": "&&",
            "left": {"type": "Boolean", "value": True},
            "right": {"type": "Boolean", "value": True},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_and_false(self, evaluator, ctx):
        """&& returns False when one is false."""
        expr = {
            "type": "BinaryExpr",
            "operator": "&&",
            "left": {"type": "Boolean", "value": True},
            "right": {"type": "Boolean", "value": False},
        }
        assert evaluator.evaluate(expr, ctx) is False

    def test_or_true(self, evaluator, ctx):
        """|| returns True when one is true."""
        expr = {
            "type": "BinaryExpr",
            "operator": "||",
            "left": {"type": "Boolean", "value": False},
            "right": {"type": "Boolean", "value": True},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_or_false(self, evaluator, ctx):
        """|| returns False when both false."""
        expr = {
            "type": "BinaryExpr",
            "operator": "||",
            "left": {"type": "Boolean", "value": False},
            "right": {"type": "Boolean", "value": False},
        }
        assert evaluator.evaluate(expr, ctx) is False

    def test_not_true(self, evaluator, ctx):
        """! negates True to False."""
        expr = {
            "type": "UnaryExpr",
            "operator": "!",
            "operand": {"type": "Boolean", "value": True},
        }
        assert evaluator.evaluate(expr, ctx) is False

    def test_not_false(self, evaluator, ctx):
        """! negates False to True."""
        expr = {
            "type": "UnaryExpr",
            "operator": "!",
            "operand": {"type": "Boolean", "value": False},
        }
        assert evaluator.evaluate(expr, ctx) is True

    def test_and_short_circuit(self, evaluator, ctx):
        """&& short-circuits: false && (error) should not evaluate right side."""
        # Right side would raise if evaluated (division by zero)
        expr = {
            "type": "BinaryExpr",
            "operator": "&&",
            "left": {"type": "Boolean", "value": False},
            "right": {
                "type": "BinaryExpr",
                "operator": "/",
                "left": {"type": "Int", "value": 1},
                "right": {"type": "Int", "value": 0},
            },
        }
        assert evaluator.evaluate(expr, ctx) is False

    def test_or_short_circuit(self, evaluator, ctx):
        """|| short-circuits: true || (error) should not evaluate right side."""
        expr = {
            "type": "BinaryExpr",
            "operator": "||",
            "left": {"type": "Boolean", "value": True},
            "right": {
                "type": "BinaryExpr",
                "operator": "/",
                "left": {"type": "Int", "value": 1},
                "right": {"type": "Int", "value": 0},
            },
        }
        assert evaluator.evaluate(expr, ctx) is True


class TestEvaluateDefault:
    """Tests for evaluate_default() — compile-time constant AST evaluation."""

    def test_string_literal(self):
        assert evaluate_default({"type": "String", "value": "hello"}) == "hello"

    def test_int_literal(self):
        assert evaluate_default({"type": "Int", "value": 42}) == 42

    def test_float_literal(self):
        assert evaluate_default({"type": "Float", "value": 3.14}) == 3.14

    def test_double_literal(self):
        assert evaluate_default({"type": "Double", "value": 2.718}) == 2.718

    def test_boolean_true(self):
        assert evaluate_default({"type": "Boolean", "value": True}) is True

    def test_boolean_false(self):
        assert evaluate_default({"type": "Boolean", "value": False}) is False

    def test_null_literal(self):
        assert evaluate_default({"type": "Null"}) is None

    def test_array_literal(self):
        expr = {
            "type": "ArrayLiteral",
            "elements": [
                {"type": "String", "value": "AL"},
                {"type": "String", "value": "AK"},
                {"type": "String", "value": "AZ"},
            ],
        }
        assert evaluate_default(expr) == ["AL", "AK", "AZ"]

    def test_map_literal(self):
        expr = {
            "type": "MapLiteral",
            "entries": [
                {"key": "name", "value": {"type": "String", "value": "test"}},
                {"key": "count", "value": {"type": "Int", "value": 5}},
            ],
        }
        assert evaluate_default(expr) == {"name": "test", "count": 5}

    def test_nested_array_with_maps(self):
        expr = {
            "type": "ArrayLiteral",
            "elements": [
                {
                    "type": "MapLiteral",
                    "entries": [
                        {"key": "id", "value": {"type": "Int", "value": 1}},
                    ],
                },
                {
                    "type": "MapLiteral",
                    "entries": [
                        {"key": "id", "value": {"type": "Int", "value": 2}},
                    ],
                },
            ],
        }
        assert evaluate_default(expr) == [{"id": 1}, {"id": 2}]

    def test_unary_negation(self):
        expr = {
            "type": "UnaryExpr",
            "operator": "-",
            "operand": {"type": "Int", "value": 10},
        }
        assert evaluate_default(expr) == -10

    def test_passthrough_plain_string(self):
        assert evaluate_default("plain") == "plain"

    def test_passthrough_plain_int(self):
        assert evaluate_default(99) == 99

    def test_passthrough_none(self):
        assert evaluate_default(None) is None

    def test_passthrough_plain_list(self):
        assert evaluate_default(["a", "b"]) == ["a", "b"]

    def test_empty_array(self):
        assert evaluate_default({"type": "ArrayLiteral", "elements": []}) == []

    def test_empty_map(self):
        assert evaluate_default({"type": "MapLiteral", "entries": []}) == {}
