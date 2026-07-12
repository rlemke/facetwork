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
from dataclasses import dataclass, field
from typing import Any

from .errors import EvaluationError, ReferenceError
from .types import StepId, StepReference


@dataclass
class EvaluationContext:
    """Context for expression evaluation.

    Provides access to:
    - Workflow input parameters
    - Completed step outputs
    - Foreach iteration variables
    - Step lookups (for bare step refs and StepReference dereferencing)
    """

    # Workflow input values
    inputs: dict[str, Any]

    # Step output getter: step_name -> attribute -> value
    get_step_output: Callable[[str, str], Any]

    # Step lookup by statement name — used for bare `ds = s1` step refs.
    # Returns the step definition (with id, workflow_id, facet_name) or None.
    get_step_by_name: Callable[[str], Any] | None = None

    # Step lookup by id — used to dereference a StepReference encountered
    # while walking a path (e.g. `$.ds.field` when `ds` is a step ref).
    get_step_by_id: Callable[[str], Any] | None = None

    # Mixin sub-step lookup by (parent_step_id, alias) — used when the next
    # segment after a StepReference is a mixin alias on the referenced
    # facet (e.g. `$.fref.m1.output`). Returns the mixin sub-step
    # definition or None. Optional; when absent or returning None, the
    # resolver falls through to the standard "no attribute" error.
    get_mixin_step_by_alias: Callable[[str, str], Any] | None = None

    # Per-tick resolved-ref cache keyed by step_id; avoids repeated lookups
    # when the same StepReference is dereferenced multiple times.
    _resolved_refs: dict[str, Any] = field(default_factory=dict)

    # Foreach variable (if in foreach block)
    foreach_var: str | None = None
    foreach_value: Any | None = None

    # Current step ID for error reporting
    step_id: StepId | None = None

    # Relative-scoping container stack (docs/architecture/ffl-relative-scoping.md).
    # When set (flag-on), $. resolves against this stack instead of `inputs`:
    # index 0 = the immediate container's attribute surface (params ∪ returns),
    # index 1 = one container up ($$), etc. None = legacy flat `inputs` resolution.
    scope_stack: list[dict[str, Any]] | None = None


class ExpressionEvaluator:
    """Evaluates FFL expressions from compiled AST."""

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

        if ctx.scope_stack is not None:
            return self._eval_input_ref_relative(expr, path, field, ctx)

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

    def _eval_input_ref_relative(
        self, expr: dict, path: list, field: str, ctx: EvaluationContext
    ) -> Any:
        """Resolve $. / $$. under relative scoping using ``ctx.scope_stack``.

        ``up_levels`` picks the frame (0 = immediate container, 1 = one up, …).
        A frame's params are static (bound at init); its returns are resolved
        lazily through ``get_step_output`` so a body that reads its containing
        step's return defers until the step completes (same mechanism a
        ``step.field`` ref uses). The foreach loop var lives on the immediate
        frame ($.r). An attribute that is neither a param, a return, nor the loop
        var evaluates to None (docs/architecture/ffl-relative-scoping.md §5.2).
        """
        up = int(expr.get("up_levels", 0))
        stack = ctx.scope_stack or []
        dollars = "$" * (up + 1)
        if up >= len(stack):
            raise ReferenceError(
                f"{dollars}.{field}",
                f"Reference walks up {up} container level(s), past the outermost "
                f"container ({max(len(stack) - 1, 0)} available)",
                ctx.step_id,
            )
        # Loop var is bound on the immediate frame only.
        if up == 0 and ctx.foreach_var and field == ctx.foreach_var:
            value = ctx.foreach_value
        else:
            frame = stack[up]
            if field in frame["params"]:
                value = frame["params"][field]
            elif field in frame["returns"] and frame.get("name"):
                # A container return — resolve via the step-output getter, which
                # defers (raises _StepNotReady) until the container completes.
                value = ctx.get_step_output(frame["name"], field)
            elif up == 0 and field in ctx.inputs:
                # Values injected into the immediate scope that aren't declared
                # params/returns — e.g. a catch clause's error / error_type.
                value = ctx.inputs[field]
            else:
                # Unknown / not-yet-assigned attribute → None.
                value = None
        return self._resolve_path(value, path[1:], f"{dollars}.{field}", ctx)

    def _eval_step_ref(self, expr: dict, ctx: EvaluationContext) -> Any:
        """Evaluate a step reference.

        Two forms:
        - Bare step name (`s1`, path=[step_name]): returns a StepReference
          carrying the step's id, workflow id, and facet name. Used when a
          parameter is typed as a facet so the consumer can read multiple
          fields from the same upstream step.
        - Field access (`s1.field` or `s1.field.sub`, path>=2): returns the
          value of `field`, with any remaining path segments resolved.

        Args:
            expr: The StepRef expression
            ctx: Evaluation context

        Returns:
            A StepReference for bare names, or the resolved value for field
            access.

        Raises:
            ReferenceError: If step or attribute not found.
        """
        path = expr.get("path", [])
        if not path:
            raise ReferenceError(
                str(path),
                "Step reference has empty path",
                ctx.step_id,
            )

        step_name = path[0]

        if len(path) == 1:
            # Bare step name — build a StepReference. Requires the caller to
            # have wired get_step_by_name on the evaluation context.
            if ctx.get_step_by_name is None:
                raise ReferenceError(
                    step_name,
                    "Bare step reference requires get_step_by_name on context",
                    ctx.step_id,
                )
            step_def = ctx.get_step_by_name(step_name)
            if step_def is None:
                raise ReferenceError(
                    step_name,
                    f"Step '{step_name}' not found",
                    ctx.step_id,
                )
            ref = StepReference(
                step_id=str(step_def.id),
                workflow_id=str(step_def.workflow_id),
                facet_name=getattr(step_def, "facet_name", "") or "",
            )
            ctx._resolved_refs[ref.step_id] = step_def
            return ref

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

            # StepReference: dereference into the referenced step's returns.
            if isinstance(value, StepReference):
                if ctx.get_step_by_id is None:
                    raise ReferenceError(
                        f"{base_path}.{segment}",
                        "Step reference dereference requires get_step_by_id on context",
                        ctx.step_id,
                    )
                step_def = ctx._resolved_refs.get(value.step_id)
                if step_def is None:
                    step_def = ctx.get_step_by_id(value.step_id)
                    if step_def is None:
                        raise ReferenceError(
                            f"{base_path}.{segment}",
                            f"Referenced step '{value.step_id}' not found",
                            ctx.step_id,
                        )
                    ctx._resolved_refs[value.step_id] = step_def
                attrs = getattr(step_def, "attributes", None)
                if attrs is None:
                    raise ReferenceError(
                        f"{base_path}.{segment}",
                        f"Referenced step has no attribute '{segment}'",
                        ctx.step_id,
                    )
                # A step-ref exposes bound inputs, computed outputs, and
                # aliased mixin sub-steps.  Resolution order:
                #   1. Mixin sub-step under this alias — always the live
                #      persisted state, so consumers see Scope-A yield
                #      overrides applied to the sub-step at the parent's
                #      STATEMENT_CAPTURE_BEGIN.  Beats the snapshot dict
                #      ``MixinCaptureBeginHandler`` left on
                #      ``attrs.params[alias]`` (the snapshot freezes at
                #      mixin-execution time and goes stale once routing
                #      writes to the persisted sub-step).
                #   2. ``returns`` — shadows params on name collision.
                #   3. ``params`` — bound inputs.
                # ``MIXIN_ALIAS_NAME_CONFLICT`` already forbids an alias
                # colliding with a primary param or return, so this
                # precedence change doesn't reintroduce ambiguity.
                mixin_step = None
                if ctx.get_mixin_step_by_alias is not None:
                    mixin_step = ctx.get_mixin_step_by_alias(value.step_id, segment)
                if mixin_step is not None:
                    mixin_ref = StepReference(
                        step_id=str(mixin_step.id),
                        workflow_id=str(getattr(mixin_step, "workflow_id", "")),
                        facet_name=getattr(mixin_step, "facet_name", "") or "",
                    )
                    ctx._resolved_refs[mixin_ref.step_id] = mixin_step
                    value = mixin_ref
                elif segment in attrs.returns:
                    value = attrs.returns[segment].value
                elif segment in attrs.params:
                    value = attrs.params[segment].value
                else:
                    raise ReferenceError(
                        f"{base_path}.{segment}",
                        f"Referenced step has no param, return, or mixin alias '{segment}'",
                        ctx.step_id,
                    )
            elif isinstance(value, dict):
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
            # Containment + string-match operators (Scope: sys.log/assert
            # work, but usable anywhere a Boolean expression is valid).
            elif operator == "in":
                return left in right
            elif operator == "not in":
                return left not in right
            elif operator == "contains":
                return right in left
            elif operator == "startsWith":
                return str(left).startswith(str(right))
            elif operator == "endsWith":
                return str(left).endswith(str(right))
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


def evaluate_default(expr: Any) -> Any:
    """Evaluate a compile-time constant AST expression (parameter default).

    Recursively converts AST literal nodes to Python values.
    No EvaluationContext needed since defaults can't contain InputRef or StepRef.
    """
    if not isinstance(expr, dict) or "type" not in expr:
        return expr
    t = expr["type"]
    if t in ("String", "Int", "Float"):
        return expr["value"]
    if t == "Double":
        return float(expr.get("value", 0.0))
    if t == "Boolean":
        return bool(expr.get("value", False))
    if t == "Null":
        return None
    if t == "ArrayLiteral":
        return [evaluate_default(e) for e in expr.get("elements", [])]
    if t == "MapLiteral":
        return {e["key"]: evaluate_default(e.get("value")) for e in expr.get("entries", [])}
    if t == "UnaryExpr" and expr.get("operator") == "-":
        return -evaluate_default(expr.get("operand"))
    # Fallback: return the value field if present, else the dict as-is
    return expr.get("value", expr)
