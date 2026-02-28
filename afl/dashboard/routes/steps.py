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

"""Step routes — list and detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..dependencies import get_store

router = APIRouter(prefix="/steps")


@router.get("/{step_id}")
def step_detail(step_id: str, request: Request, store=Depends(get_store)):
    """Show step detail: state, attributes, retry action."""
    step = store.get_step(step_id)
    names = _resolve_step_names(step, store) if step else {}
    step_logs = store.get_step_logs_by_step(step_id) if step else []
    return request.app.state.templates.TemplateResponse(
        request,
        "steps/detail.html",
        {"step": step, "names": names, "step_logs": step_logs},
    )


def _resolve_step_names(step, store) -> dict:
    """Resolve human-readable names for step hierarchy fields."""
    names: dict[str, str] = {}

    # Workflow name: find a runner whose workflow_id matches
    runner = None
    if step.workflow_id:
        try:
            runners = store.get_all_runners()
            for r in runners:
                if r.workflow_id == step.workflow_id:
                    runner = r
                    names["workflow"] = r.workflow.name
                    break
        except Exception:
            pass

    # Facet name on this step
    if step.facet_name:
        names["facet"] = step.facet_name

    # Statement name: prefer persisted statement_name, fall back to AST resolution
    if step.statement_name:
        names["statement"] = step.statement_name
    elif step.statement_id and runner:
        try:
            stmt_name = _resolve_statement_name(step.statement_id, runner, store)
            if stmt_name:
                names["statement"] = stmt_name
        except Exception:
            pass

    # Container: look up the container step
    if step.container_id:
        try:
            container = store.get_step(step.container_id)
            if container:
                parts = [container.object_type]
                if container.facet_name:
                    parts.append(container.facet_name)
                names["container"] = " — ".join(parts)
        except Exception:
            pass

    # Block: look up the block step
    if step.block_id:
        try:
            block = store.get_step(step.block_id)
            if block:
                parts = [block.object_type]
                if block.facet_name:
                    parts.append(block.facet_name)
                names["block"] = " — ".join(parts)
        except Exception:
            pass

    return names


def _resolve_statement_name(statement_id: str, runner, store) -> str | None:
    """Look up the statement name from the flow's compiled AST."""
    import json

    flow = store.get_flow(runner.workflow.flow_id)
    if not flow:
        return None

    from afl.ast_utils import find_all_workflows

    if flow.compiled_ast:
        program_dict = flow.compiled_ast
    elif flow.compiled_sources:
        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser

        parser = AFLParser()
        ast = parser.parse(flow.compiled_sources[0].content)
        emitter = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter.emit(ast))
    else:
        return None

    for wf in find_all_workflows(program_dict):
        body = wf.get("body")
        if not body:
            continue
        result = _find_statement_in_block(statement_id, body)
        if result:
            return result

    return None


def _find_statement_in_block(statement_id: str, block: dict) -> str | None:
    """Recursively search a block AST for a statement by ID."""
    for stmt in block.get("steps", []):
        if stmt.get("id") == statement_id:
            name = stmt.get("name", "")
            facet = stmt.get("call", {}).get("target", "")
            if name and facet:
                return f"{name} = {facet}()"
            elif name:
                return name
            elif facet:
                return facet
        # Check nested bodies
        nested = stmt.get("body")
        if nested:
            result = _find_statement_in_block(statement_id, nested)
            if result:
                return result

    # Check yield
    yield_stmt = block.get("yield")
    if yield_stmt and yield_stmt.get("id") == statement_id:
        target = yield_stmt.get("call", {}).get("target", "")
        return f"yield {target}()" if target else "yield"

    return None


@router.get("/{step_id}/partial")
def step_detail_partial(step_id: str, request: Request, store=Depends(get_store)):
    """HTMX partial for auto-refresh of step detail content."""
    step = store.get_step(step_id)
    names = _resolve_step_names(step, store) if step else {}
    step_logs = store.get_step_logs_by_step(step_id) if step else []
    return request.app.state.templates.TemplateResponse(
        request,
        "steps/_detail_content.html",
        {"step": step, "names": names, "step_logs": step_logs},
    )


@router.post("/{step_id}/retry")
def retry_step(step_id: str, store=Depends(get_store)):
    """Retry a failed step by resetting it to EVENT_TRANSMIT."""
    from afl.runtime.states import StepState

    step = store.get_step(step_id)
    if step:
        # Reset step state to EVENT_TRANSMIT (matches evaluator.retry_step logic)
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.clear_error()
        step.transition.request_transition = False
        step.transition.changed = True
        store.save_step(step)

        # Reset associated task to pending
        task = store.get_task_for_step(step_id)
        if task is not None:
            task.state = "pending"
            task.error = None
            store.save_task(task)
    return RedirectResponse(url=f"/steps/{step_id}", status_code=303)
