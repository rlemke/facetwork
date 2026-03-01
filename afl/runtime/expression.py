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

"""AFL expression evaluation.

Evaluates expressions from compiled AST including:
- Literals (String, Int, Boolean, Null)
- Input references ($.field)
- Step references (step.field)
- Concatenation (expr ++ expr)
- Arithmetic (+ - * /)
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import EvaluationError, ReferenceError
from .types import StepId


@dataclass
class EvaluationContext:
    """Context for expression evaluation.

    Provides access to:
    - Workflow input parameters
    - Completed step outputs
    - Foreach iteration variables
    """

    # Workflow input values
    inputs: dict[str, Any]

    # Step output getter: step_name -> attribute -> value
    get_step_output: Callable[[str, str], Any]

    # Foreach variable (if in foreach block)
    foreach_var: str | None = None
    foreach_value: Any | None = None

    # Current step ID for error reporting
    step_id: StepId | None = None


class ExpressionEvaluator:
    """Evaluates AFL expressions from compiled AST."""

    def evaluate(self, expr: Any, ctx: EvaluationContext) -> Any:
        """Evaluate an expression.

        Args:
            expr: The expression AST (dict or primitive)
            ctx: Evaluation context

        Returns:
            The evaluated value

        Raises:
            EvaluationError: If evaluation fails
            ReferenceError: If a reference cannot be resolved
        """
        if expr is None:
            return None

        if isinstance(expr, (str, int, float, bool)):
            return expr

        if isinstance(expr, list):
            return [self.evaluate(item, ctx) for item in expr]

        if not isinstance(expr, dict):
            return expr

        expr_type = expr.get("type", "")

        if expr_type == "String":
            return expr.get("value", "")
        elif expr_type == "Int":
            return int(expr.get("value", 0))
        elif expr_type == "Double":
            return float(expr.get("value", 0.0))
        elif expr_type == "Boolean":
            return bool(expr.get("value", False))
        elif expr_type == "Null":
            return None
        elif expr_type == "InputRef":
            return self._eval_input_ref(expr, ctx)
        elif expr_type == "StepRef":
            return self._eval_step_ref(expr, ctx)
        elif expr_type == "ConcatExpr":
            return self._eval_concat(expr, ctx)
        elif expr_type == "BinaryExpr":
            return self._eval_binary(expr, ctx)
        elif expr_type == "ArrayLiteral":
            return self._eval_array_literal(expr, ctx)
        elif expr_type == "MapLiteral":
            return self._eval_map_literal(expr, ctx)
        elif expr_type == "UnaryExpr":
            return self._eval_unary(expr, ctx)
        elif expr_type == "IndexExpr":
            return self._eval_index(expr, ctx)
        else:
            # Unknown type, try to return value directly
            if "value" in expr:
                return expr["value"]
            raise EvaluationError(
                str(expr),
                f"Unknown expression type: {expr_type}",
                ctx.step_id,
            )

    def _eval_input_ref(self, expr: dict, ctx: EvaluationContext) -> Any:
        """Evaluate an input reference ($.field).

        Args:
            expr: The InputRef expression
            ctx: Evaluation context

        Returns:
            The input value

        Raises:
            ReferenceError: If input not found
        """
        path = expr.get("path", [])
        if not path:
            raise ReferenceError(
                "$",
                "Empty input reference path",
                ctx.step_id,
            )

        field = path[0]

        # Check foreach variable first
        if ctx.foreach_var and field == ctx.foreach_var:
            value = ctx.foreach_value
        else:
            # Look up in inputs
            if field not in ctx.inputs:
                raise ReferenceError(
                    f"$.{field}",
                    f"Input parameter '{field}' not found",
                    ctx.step_id,
                )
            value = ctx.inputs[field]

        # Handle nested path
        return self._resolve_path(value, path[1:], f"$.{field}", ctx)

    def _eval_step_ref(self, expr: dict, ctx: EvaluationContext) -> Any:
        """Evaluate a step reference (step.field).

        Args:
            expr: The StepRef expression
            ctx: Evaluation context

        Returns:
            The step output value

        Raises:
            ReferenceError: If step or attribute not found
        """
        path = expr.get("path", [])
        if len(path) < 2:
            raise ReferenceError(
                str(path),
                "Step reference requires at least step.attribute",
                ctx.step_id,
            )

        step_name = path[0]
        attr_name = path[1]

        try:
            value = ctx.get_step_output(step_name, attr_name)
        except Exception as e:
            raise ReferenceError(
                f"{step_name}.{attr_name}",
                str(e),
                ctx.step_id,
            ) from e

        # Handle nested path beyond step.attr
        return self._resolve_path(value, path[2:], f"{step_name}.{attr_name}", ctx)

    def _resolve_path(
        self,
        value: Any,
        remaining_path: list[str],
        base_path: str,
        ctx: EvaluationContext,
    ) -> Any:
        """Resolve remaining path segments on a value.

        Args:
            value: The current value
            remaining_path: Remaining path segments
            base_path: Base path for error messages
            ctx: Evaluation context

        Returns:
            The resolved value
        """
        for segment in remaining_path:
            if value is None:
                raise ReferenceError(
                    f"{base_path}.{segment}",
                    "Cannot access property on null",
                    ctx.step_id,
                )

            if isinstance(value, dict):
                if segment not in value:
                    raise ReferenceError(
                        f"{base_path}.{segment}",
                        f"Property '{segment}' not found",
                        ctx.step_id,
                    )
                value = value[segment]
            elif hasattr(value, segment):
                value = getattr(value, segment)
            else:
                raise ReferenceError(
                    f"{base_path}.{segment}",
                    f"Cannot access '{segment}' on {type(value).__name__}",
                    ctx.step_id,
                )
            base_path = f"{base_path}.{segment}"

        return value

    def _eval_concat(self, expr: dict, ctx: EvaluationContext) -> str:
        """Evaluate a concatenation expression.

        Args:
            expr: The ConcatExpr expression
            ctx: Evaluation context

        Returns:
            The concatenated string
        """
        operands = expr.get("operands", [])
        parts = []

        for operand in operands:
            value = self.evaluate(operand, ctx)
            parts.append(str(value) if value is not None else "")

        return "".join(parts)

    def _eval_binary(self, expr: dict, ctx: EvaluationContext) -> Any:
        """Evaluate a binary expression (arithmetic, comparison, boolean).

        Args:
            expr: The BinaryExpr expression
            ctx: Evaluation context

        Returns:
            The computed result
        """
        operator = expr.get("operator", "+")

        # Short-circuit evaluation for boolean operators
        if operator == "&&":
            left = self.evaluate(expr.get("left"), ctx)
            if not left:
                return False
            return bool(self.evaluate(expr.get("right"), ctx))
        elif operator == "||":
            left = self.evaluate(expr.get("left"), ctx)
            if left:
                return True
            return bool(self.evaluate(expr.get("right"), ctx))

        left = self.evaluate(expr.get("left"), ctx)
        right = self.evaluate(expr.get("right"), ctx)

        try:
            # Comparison operators
            if operator == "==":
                return left == right
            elif operator == "!=":
                return left != right
            elif operator == ">":
                return left > right
            elif operator == "<":
                return left < right
            elif operator == ">=":
                return left >= right
            elif operator == "<=":
                return left <= right
            # Arithmetic operators
            elif operator == "+":
                return left + right
            elif operator == "-":
                return left - right
            elif operator == "*":
                return left * right
            elif operator == "/":
                if right == 0:
                    raise EvaluationError(
                        str(expr),
                        "Division by zero",
                        ctx.step_id,
                    )
                return left / right
            elif operator == "%":
                return left % right
            else:
                raise EvaluationError(
                    str(expr),
                    f"Unknown operator: {operator}",
                    ctx.step_id,
                )
        except TypeError as e:
            raise EvaluationError(
                str(expr),
                f"Type error in {operator} operation: {e}",
                ctx.step_id,
            ) from e

    def _eval_unary(self, expr: dict, ctx: EvaluationContext) -> Any:
        """Evaluate a unary expression (negation or logical not).

        Args:
            expr: The UnaryExpr expression
            ctx: Evaluation context

        Returns:
            The result value
        """
        operand = self.evaluate(expr.get("operand"), ctx)
        operator = expr.get("operator", "-")

        try:
            if operator == "-":
                return -operand
            elif operator == "!":
                return not operand
            else:
                raise EvaluationError(
                    str(expr),
                    f"Unknown unary operator: {operator}",
                    ctx.step_id,
                )
        except TypeError as e:
            raise EvaluationError(
                str(expr),
                f"Type error in unary {operator} operation: {e}",
                ctx.step_id,
            ) from e

    def _eval_array_literal(self, expr: dict, ctx: EvaluationContext) -> list:
        """Evaluate an array literal."""
        elements = expr.get("elements", [])
        return [self.evaluate(elem, ctx) for elem in elements]

    def _eval_map_literal(self, expr: dict, ctx: EvaluationContext) -> dict:
        """Evaluate a map literal."""
        entries = expr.get("entries", [])
        result = {}
        for entry in entries:
            key = entry.get("key", "")
            value = self.evaluate(entry.get("value"), ctx)
            result[key] = value
        return result

    def _eval_index(self, expr: dict, ctx: EvaluationContext) -> Any:
        """Evaluate an index expression (target[index])."""
        target = self.evaluate(expr.get("target"), ctx)
        index = self.evaluate(expr.get("index"), ctx)

        try:
            return target[index]
        except (KeyError, IndexError, TypeError) as e:
            raise EvaluationError(
                str(expr),
                f"Index error: {e}",
                ctx.step_id,
            ) from e


def evaluate_args(
    args: list[dict],
    ctx: EvaluationContext,
) -> dict[str, Any]:
    """Evaluate all arguments in a call expression.

    Args:
        args: List of named argument dicts
        ctx: Evaluation context

    Returns:
        Dict mapping argument names to evaluated values
    """
    evaluator = ExpressionEvaluator()
    result = {}

    for arg in args:
        name = arg.get("name", "")
        value_expr = arg.get("value", {})
        result[name] = evaluator.evaluate(value_expr, ctx)

    return result
