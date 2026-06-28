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

import logging
from typing import TYPE_CHECKING

from ..changers.base import StateChangeResult
from ..errors import EvaluationError
from ..expression import EvaluationContext, ExpressionEvaluator, evaluate_args
from ..types import ObjectType
from .base import StateHandler

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class _StepNotReady(Exception):
    """Raised when a cross-block step reference is not yet complete.

    Signals FacetInitializationBeginHandler to defer rather than error.
    """


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

        # Inline diagnostic statements: sys.log / sys.assert.  They use
        # SYS_STMT_TRANSITIONS, so FACET_INIT_BEGIN is the one place
        # their side effect runs.  No subsequent state evaluates args
        # for them.
        if self.step.object_type in (ObjectType.SYS_LOG, ObjectType.SYS_ASSERT):
            return self._execute_sys_stmt(stmt_def)

        if stmt_def is None:
            # Two cases land here:
            # 1. Mixin sub-step — written by MixinBlocksBeginHandler with
            #    its params pre-bound from the parent's already-evaluated
            #    sig-args.  We skip call-arg evaluation and only fill
            #    facet defaults for params the parent didn't bind.
            # 2. Workflow root step — use the workflow's declared params
            #    and any default values.
            if self.step.container_id is not None and self.step.statement_name:
                return self._init_mixin_sub_step()

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

            # Evaluate sig-level mixin args first.  The facet sig may
            # declare aliased mixins with bound args, e.g.
            #     facet Parent(input: String) with M(x = $.input) as m
            # Those args are evaluated in the parent's scope (so $.input
            # here resolves to the parent's bound ``input`` param) and
            # placed under ``params[alias]`` as a nested dict.  This is
            # the seed ``MixinBlocksBeginHandler`` reads when binding
            # the mixin sub-step's own params.  Call-site mixin args
            # (handled below) override sig-level args on the same alias.
            facet_def_for_sig = None
            if self.step.facet_name:
                facet_def_for_sig = self.context.get_facet_definition(self.step.facet_name)
            if facet_def_for_sig:
                for sig_mixin in facet_def_for_sig.get("mixins", []) or []:
                    sig_alias = sig_mixin.get("alias")
                    sig_args = sig_mixin.get("args", [])
                    if not sig_alias or not sig_args:
                        continue
                    sig_evaluated = evaluate_args(sig_args, ctx)
                    # Initialise the alias bag if call-site didn't already.
                    existing = evaluated.get(sig_alias)
                    if isinstance(existing, dict):
                        # Fill in keys the call-site override didn't specify.
                        for k, v in sig_evaluated.items():
                            if k not in existing:
                                existing[k] = v
                    else:
                        evaluated[sig_alias] = sig_evaluated

            # Evaluate call-site mixin args
            for mixin in stmt_def.mixins or []:
                mixin_args = mixin.get("args", [])
                mixin_alias = mixin.get("alias")
                mixin_evaluated = evaluate_args(mixin_args, ctx)
                if mixin_alias:
                    existing = evaluated.get(mixin_alias)
                    if isinstance(existing, dict):
                        # Call-site args override sig-level args under the
                        # same alias.
                        existing.update(mixin_evaluated)
                    else:
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

            # Apply facet defaults for any params not provided in the call.
            # Skip for yield assignments — yields only carry explicitly
            # set values; applying the target facet's defaults would leak
            # unwanted params into the capture merge.
            if self.step.facet_name and self.step.object_type != ObjectType.YIELD_ASSIGNMENT:
                facet_def = self.context.get_facet_definition(self.step.facet_name)
                if facet_def:
                    expr_eval = ExpressionEvaluator()
                    for param in facet_def.get("params", []):
                        param_name = param.get("name", "")
                        if param_name not in evaluated and "default" in param:
                            evaluated[param_name] = expr_eval.evaluate(param["default"], ctx)

            # For schema instantiation, store values as returns (accessible via step.field)
            # For facet calls, store values as params
            is_schema = self.step.object_type == ObjectType.SCHEMA_INSTANTIATION
            for name, value in evaluated.items():
                self.step.set_attribute(name, value, is_return=is_schema)

            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        except _StepNotReady as snr:
            # Direct _StepNotReady (before expression evaluator wrapping)
            logger.debug(
                "Facet initialization deferred: %s (step=%s)",
                snr,
                self.step.id,
            )
            return self.stay(push=True)

        except Exception as e:
            # Check if the root cause is _StepNotReady wrapped by
            # ExpressionEvaluator._eval_step_ref into a ReferenceError.
            if isinstance(getattr(e, "__cause__", None), _StepNotReady):
                logger.debug(
                    "Facet initialization deferred: %s (step=%s)",
                    e.__cause__,
                    self.step.id,
                )
                return self.stay(push=True)
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

        # Build step output getter.
        # When a referenced step exists but is not yet complete (common
        # for cross-block references in sequential andThen blocks),
        # raise _StepNotReady so the caller can defer instead of error.
        def get_step_output(step_name: str, attr_name: str) -> object:
            step = self.context.get_completed_step_by_name(step_name, self.step.block_id)
            if step is None:
                raise _StepNotReady(
                    f"Step '{step_name}' not found or not yet complete "
                    f"(reference: {step_name}.{attr_name})"
                )
            value = step.get_attribute(attr_name)
            if value is None:
                raise ValueError(f"Attribute '{attr_name}' not found on step '{step_name}'")
            return value

        # Bare-step-ref lookups (for `ds = s1` style args). Same scope as
        # get_step_output — the dep tracker has already gated this step on
        # s1's completion, so by the time we evaluate args s1 must be done.
        def get_step_by_name(step_name: str):
            step = self.context.get_completed_step_by_name(step_name, self.step.block_id)
            if step is None:
                raise _StepNotReady(
                    f"Step '{step_name}' not found or not yet complete "
                    f"(bare step reference: {step_name})"
                )
            return step

        def get_step_by_id(step_id: str):
            return self.context.persistence.get_step(step_id)

        def get_mixin_step_by_alias(parent_step_id: str, alias: str):
            from ..mixin_alias import resolve_mixin_step_by_alias

            return resolve_mixin_step_by_alias(
                parent_step_id,
                alias,
                self.context.persistence,
                self.context.get_facet_definition,
            )

        return EvaluationContext(
            inputs=inputs,
            get_step_output=get_step_output,
            get_step_by_name=get_step_by_name,
            get_step_by_id=get_step_by_id,
            get_mixin_step_by_alias=get_mixin_step_by_alias,
            step_id=self.step.id,
            foreach_var=foreach_var,
            foreach_value=foreach_value,
        )

    def _resolve_inputs(self) -> dict:
        """Resolve the InputRef ($.) scope for this step.

        For steps in the workflow root block, inputs come from the
        workflow root step's params.  For steps in nested blocks
        (e.g. inside a called sub-workflow), inputs come from the
        container step that owns the block, overlaid on workflow
        root params.

        For steps inside a **mixin sub-step's body** (Scope B), the
        scope is *isolated*: only the mixin sub-step's own attributes
        (params + returns) are in $.   The workflow root is not seeded
        and the walk stops at the mixin sub-step.  This enforces the
        rule that a mixin body cannot reference anything outside its
        own scope.

        Returns:
            Dict of input name -> value
        """
        # Walk up from this step's block looking for the nearest
        # VARIABLE_ASSIGNMENT facet ancestor.  If that ancestor turns
        # out to be a mixin sub-step, the body's scope is isolated to
        # that sub-step's attributes alone.  Otherwise we fall back to
        # the standard workflow-root + nearest-facet-ancestor overlay.
        if self.step.block_id:
            block_step = self.context._find_step(self.step.block_id)
            if block_step and block_step.container_id:
                container = self.context._find_step(block_step.container_id)
                if container and container.container_id is not None:
                    cursor = container
                    while cursor:
                        if (
                            cursor.object_type == ObjectType.VARIABLE_ASSIGNMENT
                            and cursor.facet_name
                            and cursor.container_id is not None
                        ):
                            # Skip event facet calls — they don't create
                            # a new $. scope.
                            is_event = False
                            if self.context.program_ast:
                                fdef = self.context.get_facet_definition(cursor.facet_name)
                                if fdef and fdef.get("type") == "EventFacetDecl":
                                    is_event = True
                            if not is_event:
                                if self._is_mixin_sub_step(cursor):
                                    # Mixin body scope isolation: $. is
                                    # the mixin's own attributes only.
                                    return self._mixin_step_scope(cursor)
                                # FacetDecl or WorkflowDecl — overlay
                                # params on workflow root.
                                inputs: dict = {}
                                workflow_root = self.context.get_workflow_root()
                                if workflow_root:
                                    for name, attr in workflow_root.attributes.params.items():
                                        inputs[name] = attr.value
                                for name, attr in cursor.attributes.params.items():
                                    inputs[name] = attr.value
                                return inputs
                        if cursor.container_id is None:
                            break
                        next_step = self.context._find_step(cursor.container_id)
                        if next_step is None:
                            break
                        cursor = next_step

        # Top-level step (no facet ancestor) — workflow root params only.
        inputs = {}
        workflow_root = self.context.get_workflow_root()
        if workflow_root:
            for name, attr in workflow_root.attributes.params.items():
                inputs[name] = attr.value
        return inputs

    def _is_mixin_sub_step(self, step) -> bool:
        """A step is a mixin sub-step when its ``statement_name`` is
        declared as an alias on its container facet's signature."""
        if not step.statement_name or not step.container_id:
            return False
        parent = self.context._find_step(step.container_id)
        if not parent or not parent.facet_name:
            return False
        parent_def = self.context.get_facet_definition(parent.facet_name)
        if not parent_def:
            return False
        for mixin in parent_def.get("mixins", []) or []:
            if mixin.get("alias") == step.statement_name:
                return True
        return False

    def _mixin_step_scope(self, mixin_step) -> dict:
        """Build a scope dict from a mixin sub-step's attributes
        (params + returns, with returns shadowing params on collision —
        matching ``FacetAttributes.merge``)."""
        scope: dict = {}
        for name, attr in mixin_step.attributes.params.items():
            scope[name] = attr.value
        for name, attr in mixin_step.attributes.returns.items():
            scope[name] = attr.value
        return scope

    def _get_default_value(self, param_name: str, workflow_ast: dict) -> object:
        """Get default value for a workflow parameter."""
        # Look in the workflow's default values
        defaults = self.context.workflow_defaults
        return defaults.get(param_name)

    def _execute_sys_stmt(self, stmt_def) -> StateChangeResult:
        """Execute a sys.log / sys.assert inline diagnostic statement.

        sys.log: evaluate every named arg in the current scope, build
        the Splunk JSON envelope with the user's name/value pairs
        plus the runtime context (workflow_id, runner_id, server_id,
        step_id, facet_name, source location), emit to stdout via
        Python's logging machinery (so the SplunkJsonFormatter
        configured in ``facetwork.logging`` picks it up), and mirror
        an INFO-level step-log entry.

        sys.assert: evaluate the condition expression.  False → mark
        the step errored with an ``AssertionError`` carrying the
        source location.  True → step transitions normally.

        Either way the step exits FACET_INIT_BEGIN and the transition
        table walks it through STATEMENT_END → STATEMENT_COMPLETE.
        """
        from ..types import ObjectType

        if stmt_def is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        ctx = self._build_context()
        evaluator = ExpressionEvaluator()

        try:
            if self.step.object_type == ObjectType.SYS_LOG:
                fields: dict = {}
                for arg in stmt_def.args or []:
                    name = arg.get("name") if isinstance(arg, dict) else None
                    if not name:
                        continue
                    value_expr = arg.get("value") if isinstance(arg, dict) else None
                    fields[name] = evaluator.evaluate(value_expr, ctx)
                self._emit_sys_log(fields)
            else:  # SYS_ASSERT
                condition_expr = getattr(stmt_def, "sys_condition", None)
                if condition_expr is None:
                    raise EvaluationError(
                        "sys.assert",
                        "missing condition expression",
                        self.step.id,
                    )
                result = evaluator.evaluate(condition_expr, ctx)
                if not result:
                    loc = ""
                    if self.step.statement_id:
                        loc = f" (statement_id={self.step.statement_id})"
                    raise AssertionError(f"sys.assert failed{loc}")
        except _StepNotReady:
            return self.stay(push=True)
        except AssertionError as ae:
            return self.error(ae)
        except Exception as e:
            if isinstance(getattr(e, "__cause__", None), _StepNotReady):
                return self.stay(push=True)
            return self.error(e)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _emit_sys_log(self, fields: dict) -> None:
        """Write a Splunk-format JSON log line for a sys.log statement
        and mirror it as an INFO step-log entry.

        The Python ``logging`` machinery handles the stdout side: the
        ``facetwork.sys.log`` logger inherits any handler configured by
        ``configure_logging``, which in production uses
        :class:`SplunkJsonFormatter`.  The handler picks up ``extra``
        keys as additional JSON fields, so ``workflow_id``,
        ``runner_id``, ``server_id``, ``step_id``, ``facet_name``,
        ``line``, and ``column`` show up alongside the user's named
        args.
        """
        import socket
        import time as _time

        from ..entities import StepLogEntry, StepLogLevel, StepLogSource
        from ..types import generate_id

        sys_log_logger = logging.getLogger("facetwork.sys.log")
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = ""

        extra: dict = {
            "workflow_id": self.step.workflow_id,
            "runner_id": getattr(self.context, "runner_id", "") or "",
            "server_id": getattr(self.context, "server_id", "") or "",
            "step_id": self.step.id,
            "facet_name": self.step.facet_name or "",
            "hostname": hostname,
            "event": fields,
        }
        sys_log_logger.info("sys.log", extra={"_sys_log": extra})

        try:
            import json as _json

            self.context.persistence.save_step_log(
                StepLogEntry(
                    uuid=generate_id(),
                    step_id=self.step.id,
                    workflow_id=self.step.workflow_id,
                    facet_name=self.step.facet_name or "",
                    source=StepLogSource.FRAMEWORK,
                    level=StepLogLevel.INFO,
                    message=_json.dumps({"sys.log": fields}, default=str),
                    time=int(_time.time() * 1000),
                )
            )
        except Exception:
            # step-log mirror is best-effort; logging still went out.
            pass

    def _init_mixin_sub_step(self) -> StateChangeResult:
        """Initialize a mixin sub-step whose params were pre-bound by
        ``MixinBlocksBeginHandler``.

        Skips call-arg evaluation (no stmt_def to pull args from).
        Applies facet defaults only for params the parent didn't bind
        — so ``with M(x = 1)`` keeps the explicit binding while ``y``,
        if it has a default in M's signature, gets that default.
        Returns with defaults are also seeded so the mixin body's
        first statement can reference ``$.return_name`` before any
        yield (per FFL attribute semantics — params and returns are
        both addressable from the body, with defaults populated at
        init time).
        """
        if not self.step.facet_name:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        facet_def = self.context.get_facet_definition(self.step.facet_name)
        if not facet_def:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # The mixin sub-step's own scope is its already-bound params —
        # default expressions are evaluated against that, NOT against
        # workflow root (per the mixin-scope-isolation rule).
        try:
            scope_inputs = {name: attr.value for name, attr in self.step.attributes.params.items()}
            mixin_ctx = EvaluationContext(
                inputs=scope_inputs,
                get_step_output=lambda *_: None,
                step_id=self.step.id,
            )
            expr_eval = ExpressionEvaluator()

            for param in facet_def.get("params", []):
                name = param.get("name", "")
                if not name or name in self.step.attributes.params:
                    continue
                if "default" in param:
                    self.step.set_attribute(name, expr_eval.evaluate(param["default"], mixin_ctx))

            for ret in facet_def.get("returns", []):
                name = ret.get("name", "")
                if not name:
                    continue
                if "default" in ret:
                    self.step.set_attribute(
                        name,
                        expr_eval.evaluate(ret["default"], mixin_ctx),
                        is_return=True,
                    )
        except Exception as e:
            return self.error(e)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class FacetInitializationEndHandler(StateHandler):
    """Handler for state.facet.initialization.End.

    Completes facet initialization phase.
    """

    def process_state(self) -> StateChangeResult:
        """Complete initialization and transition."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)
