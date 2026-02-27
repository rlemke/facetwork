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

"""Initialization phase handlers.

Handles:
- StatementBegin: Initial setup when step is created
- FacetInitializationBegin: Evaluate attribute expressions
- FacetInitializationEnd: Complete facet initialization
"""

from typing import TYPE_CHECKING

from ..changers.base import StateChangeResult
from ..expression import EvaluationContext, ExpressionEvaluator, evaluate_args
from ..types import ObjectType
from .base import StateHandler

if TYPE_CHECKING:
    pass


class StatementBeginHandler(StateHandler):
    """Handler for state.statement.Created state.

    Sets up initial step state and prepares for execution.
    """

    def process_state(self) -> StateChangeResult:
        """Process statement begin."""
        # Mark step as initialized and ready to transition
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class FacetInitializationBeginHandler(StateHandler):
    """Handler for state.facet.initialization.Begin.

    Evaluates all attribute expressions and stores results.
    This is where $.input + 1 becomes a concrete value.
    """

    def process_state(self) -> StateChangeResult:
        """Evaluate facet attribute expressions."""
        # Get the statement definition for this step
        stmt_def = self.context.get_statement_definition(self.step)
        if stmt_def is None:
            # Workflow root step - use workflow inputs directly
            workflow_ast = self.context.get_workflow_ast()
            if workflow_ast:
                params = workflow_ast.get("params", [])
                for param in params:
                    name = param.get("name", "")
                    # Check for default value in param
                    param.get("type", "Any")
                    default_value = self._get_default_value(name, workflow_ast)
                    if default_value is not None:
                        self.step.set_attribute(name, default_value)

            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Build evaluation context
        ctx = self._build_context()

        # Evaluate arguments
        try:
            args = stmt_def.args
            evaluated = evaluate_args(args, ctx)

            # Evaluate call-site mixin args
            for mixin in (stmt_def.mixins or []):
                mixin_args = mixin.get("args", [])
                mixin_alias = mixin.get("alias")
                mixin_evaluated = evaluate_args(mixin_args, ctx)
                if mixin_alias:
                    evaluated[mixin_alias] = mixin_evaluated
                else:
                    for k, v in mixin_evaluated.items():
                        if k not in evaluated:
                            evaluated[k] = v

            # Apply implicit defaults for any params not provided in the call
            if self.step.facet_name:
                implicit_args = self.context.get_implicit_args(self.step.facet_name)
                if implicit_args:
                    expr_eval = ExpressionEvaluator()
                    for name, value_expr in implicit_args.items():
                        if name not in evaluated:
                            evaluated[name] = expr_eval.evaluate(value_expr, ctx)

            # Apply facet defaults for any params not provided in the call
            if self.step.facet_name:
                facet_def = self.context.get_facet_definition(self.step.facet_name)
                if facet_def:
                    expr_eval = ExpressionEvaluator()
                    for param in facet_def.get("params", []):
                        param_name = param.get("name", "")
                        if param_name not in evaluated and "default" in param:
                            evaluated[param_name] = expr_eval.evaluate(
                                param["default"], ctx
                            )

            # For schema instantiation, store values as returns (accessible via step.field)
            # For facet calls, store values as params
            is_schema = self.step.object_type == ObjectType.SCHEMA_INSTANTIATION
            for name, value in evaluated.items():
                self.step.set_attribute(name, value, is_return=is_schema)

            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        except Exception as e:
            return self.error(e)

    def _build_context(self) -> EvaluationContext:
        """Build evaluation context for expressions.

        For InputRef ($.) resolution:
        - If this step is in the workflow root block → use workflow root params
        - If this step is in a nested block → use the block's container step params
        - If this step is in a foreach sub-block → foreach variable is also available
        """
        inputs = self._resolve_inputs()

        # Check for foreach variable on the containing block
        foreach_var = None
        foreach_value = None
        if self.step.block_id:
            block_step = self.context._find_step(self.step.block_id)
            if block_step and block_step.foreach_var is not None:
                foreach_var = block_step.foreach_var
                foreach_value = block_step.foreach_value

        # Build step output getter
        def get_step_output(step_name: str, attr_name: str) -> object:
            step = self.context.get_completed_step_by_name(step_name, self.step.block_id)
            if step is None:
                raise ValueError(f"Step '{step_name}' not found or not complete")
            value = step.get_attribute(attr_name)
            if value is None:
                raise ValueError(f"Attribute '{attr_name}' not found on step '{step_name}'")
            return value

        return EvaluationContext(
            inputs=inputs,
            get_step_output=get_step_output,
            step_id=self.step.id,
            foreach_var=foreach_var,
            foreach_value=foreach_value,
        )

    def _resolve_inputs(self) -> dict:
        """Resolve the InputRef ($.) scope for this step.

        For steps in the workflow root block, inputs come from the
        workflow root step's params. For steps in nested blocks,
        inputs come from the container step that owns the block.

        Returns:
            Dict of input name -> value
        """
        # Find the block containing this step
        if self.step.block_id:
            block_step = self.context._find_step(self.step.block_id)
            if block_step and block_step.container_id:
                # Get the container of the block
                container = self.context._find_step(block_step.container_id)
                if container and container.container_id is not None:
                    # This is a nested block — use container's params as inputs
                    inputs = {}
                    for name, attr in container.attributes.params.items():
                        inputs[name] = attr.value
                    return inputs

        # Default: workflow root params
        workflow_root = self.context.get_workflow_root()
        inputs = {}
        if workflow_root:
            for name, attr in workflow_root.attributes.params.items():
                inputs[name] = attr.value
        return inputs

    def _get_default_value(self, param_name: str, workflow_ast: dict) -> object:
        """Get default value for a workflow parameter."""
        # Look in the workflow's default values
        defaults = self.context.workflow_defaults
        return defaults.get(param_name)


class FacetInitializationEndHandler(StateHandler):
    """Handler for state.facet.initialization.End.

    Completes facet initialization phase.
    """

    def process_state(self) -> StateChangeResult:
        """Complete initialization and transition."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)
