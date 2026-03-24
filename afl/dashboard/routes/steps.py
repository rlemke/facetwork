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
    import time as _time

    from afl.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
    )
    from afl.runtime.states import StepState

    step = store.get_step(step_id)
    if step:
        prev_state = step.state

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

        # Reset errored ancestor blocks/containers so execution resumes
        _reset_errored_ancestors(step, store)

        # Emit step log entry for the manual retry
        from afl.runtime.types import generate_id

        entry = StepLogEntry(
            uuid=generate_id(),
            step_id=step_id,
            workflow_id=step.workflow_id,
            facet_name=step.facet_name or "",
            source=StepLogSource.FRAMEWORK,
            level=StepLogLevel.WARNING,
            message=f"Step manually restarted via dashboard (was {prev_state})",
            time=int(_time.time() * 1000),
        )
        store.save_step_log(entry)
    return RedirectResponse(url=f"/steps/{step_id}", status_code=303)


@router.post("/{step_id}/retry-block")
def retry_block(step_id: str, store=Depends(get_store)):
    """Retry all errored leaf steps under a block recursively."""
    import time as _time

    from afl.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
    )
    from afl.runtime.states import StepState
    from afl.runtime.types import generate_id

    root = store.get_step(step_id)
    if not root:
        return RedirectResponse(url=f"/steps/{step_id}", status_code=303)

    # Collect all steps in the workflow
    all_steps = list(store.get_steps_by_workflow(root.workflow_id))

    # Build parent->children maps
    by_block: dict[str, list] = {}
    by_container: dict[str, list] = {}
    step_by_id: dict[str, object] = {}
    for s in all_steps:
        step_by_id[s.id] = s
        if s.block_id:
            by_block.setdefault(s.block_id, []).append(s)
        if s.container_id:
            by_container.setdefault(s.container_id, []).append(s)

    # Walk down to find errored leaf steps
    errored_leaves = []
    stack = [step_id]
    seen: set[str] = set()
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        children = by_block.get(sid, []) + by_container.get(sid, [])
        if not children:
            s = step_by_id.get(sid)
            if s and s.state == StepState.STATEMENT_ERROR:
                errored_leaves.append(s)
        else:
            for child in children:
                if child.state == StepState.STATEMENT_ERROR:
                    stack.append(child.id)

    # Retry each leaf
    for leaf in errored_leaves:
        leaf.state = StepState.EVENT_TRANSMIT
        leaf.transition.current_state = StepState.EVENT_TRANSMIT
        leaf.transition.clear_error()
        leaf.transition.request_transition = False
        leaf.transition.changed = True
        store.save_step(leaf)

        task = store.get_task_for_step(leaf.id)
        if task is not None:
            task.state = "pending"
            task.error = None
            store.save_task(task)

        _reset_errored_ancestors(leaf, store)

    # Log the bulk retry
    entry = StepLogEntry(
        uuid=generate_id(),
        step_id=step_id,
        workflow_id=root.workflow_id,
        facet_name=root.facet_name or "",
        source=StepLogSource.FRAMEWORK,
        level=StepLogLevel.WARNING,
        message=f"Block retry: {len(errored_leaves)} errored step(s) restarted",
        time=int(_time.time() * 1000),
    )
    store.save_step_log(entry)

    return RedirectResponse(url=f"/steps/{step_id}", status_code=303)


@router.post("/{step_id}/reset-block")
def reset_block(step_id: str, store=Depends(get_store)):
    """Reset a block: delete all descendant steps and restart from scratch."""
    import time as _time

    from afl.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
    )
    from afl.runtime.states import StepState
    from afl.runtime.types import generate_id

    block = store.get_step(step_id)
    if not block:
        return RedirectResponse(url=f"/steps/{step_id}", status_code=303)

    # Collect all descendant step IDs recursively
    all_steps = list(store.get_steps_by_workflow(block.workflow_id))
    by_block: dict[str, list[str]] = {}
    by_container: dict[str, list[str]] = {}
    for s in all_steps:
        if s.block_id:
            by_block.setdefault(s.block_id, []).append(s.id)
        if s.container_id:
            by_container.setdefault(s.container_id, []).append(s.id)

    descendant_ids: list[str] = []
    stack = [step_id]
    seen: set[str] = set()
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        children = by_block.get(sid, []) + by_container.get(sid, [])
        for child_id in children:
            descendant_ids.append(child_id)
            stack.append(child_id)

    # Delete descendants
    if descendant_ids:
        store.delete_step_logs_for_steps(descendant_ids)
        store.delete_tasks_for_steps(descendant_ids)
        store.delete_steps(descendant_ids)

    # Reset the block to BLOCK_EXECUTION_BEGIN
    block.state = StepState.BLOCK_EXECUTION_BEGIN
    block.transition.current_state = StepState.BLOCK_EXECUTION_BEGIN
    block.transition.clear_error()
    block.transition.request_transition = False
    block.transition.changed = True
    store.save_step(block)

    # Reset errored ancestors
    _reset_errored_ancestors(block, store)

    # Log
    entry = StepLogEntry(
        uuid=generate_id(),
        step_id=step_id,
        workflow_id=block.workflow_id,
        facet_name=block.facet_name or "",
        source=StepLogSource.FRAMEWORK,
        level=StepLogLevel.WARNING,
        message=f"Block reset: {len(descendant_ids)} step(s) deleted, block restarted",
        time=int(_time.time() * 1000),
    )
    store.save_step_log(entry)

    return RedirectResponse(url=f"/steps/{step_id}", status_code=303)


@router.post("/{step_id}/rerun")
def rerun_step(step_id: str, store=Depends(get_store)):
    """Re-run a step: reset it to EventTransmit and delete downstream dependents."""
    import time as _time

    from afl.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
    )
    from afl.runtime.states import StepState
    from afl.runtime.types import generate_id

    step = store.get_step(step_id)
    if not step:
        return RedirectResponse(url=f"/steps/{step_id}", status_code=303)

    prev_state = step.state
    block_id = step.block_id

    # Find all steps in the workflow
    all_steps = list(store.get_steps_by_workflow(step.workflow_id))

    # Build parent->children maps
    by_block: dict[str, list[str]] = {}
    by_container: dict[str, list[str]] = {}
    for s in all_steps:
        if s.block_id:
            by_block.setdefault(s.block_id, []).append(s.id)
        if s.container_id:
            by_container.setdefault(s.container_id, []).append(s.id)

    # Find downstream sibling steps in the same block
    # A step is downstream if it was created after this step's statement
    # and depends on this step's results (inferred from statement ordering)
    target_stmt_id = str(step.statement_id) if step.statement_id else ""
    downstream_step_ids: list[str] = []

    if block_id and target_stmt_id:
        # Get sibling step names for dependency inference
        stmt_name_to_id: dict[str, str] = {}
        for s in all_steps:
            if s.block_id == block_id and s.statement_name:
                stmt_name_to_id[s.statement_name] = str(s.statement_id)

        # Find steps that reference this step's statement_name in their params
        target_name = step.statement_name or ""
        downstream_stmts = _find_downstream_by_name(
            target_name, all_steps, block_id, stmt_name_to_id,
        )

        # Collect downstream step IDs and all their descendants
        for s in all_steps:
            if s.block_id == block_id and str(s.statement_id) in downstream_stmts:
                downstream_step_ids.append(s.id)
                # Collect descendants
                stack = [s.id]
                seen: set[str] = {s.id}
                while stack:
                    sid = stack.pop()
                    for child_id in by_block.get(sid, []) + by_container.get(sid, []):
                        if child_id not in seen:
                            seen.add(child_id)
                            downstream_step_ids.append(child_id)
                            stack.append(child_id)

    # Delete downstream steps
    if downstream_step_ids:
        store.delete_step_logs_for_steps(downstream_step_ids)
        store.delete_tasks_for_steps(downstream_step_ids)
        store.delete_steps(downstream_step_ids)

    # Reset the target step
    step.state = StepState.EVENT_TRANSMIT
    step.transition.current_state = StepState.EVENT_TRANSMIT
    step.transition.clear_error()
    step.transition.request_transition = False
    step.transition.changed = True
    # Clear old return values so downstream steps get fresh data
    step.attributes.returns = {}
    store.save_step(step)

    # Reset associated task
    task = store.get_task_for_step(step_id)
    if task is not None:
        task.state = "pending"
        task.error = None
        store.save_task(task)

    # Reset ancestor blocks to continue state
    _reset_ancestors_to_continue(step, store)

    # Log
    entry = StepLogEntry(
        uuid=generate_id(),
        step_id=step_id,
        workflow_id=step.workflow_id,
        facet_name=step.facet_name or "",
        source=StepLogSource.FRAMEWORK,
        level=StepLogLevel.WARNING,
        message=(
            f"Step re-run (was {prev_state}): "
            f"{len(downstream_step_ids)} downstream step(s) deleted"
        ),
        time=int(_time.time() * 1000),
    )
    store.save_step_log(entry)

    return RedirectResponse(url=f"/steps/{step_id}", status_code=303)


def _find_downstream_by_name(
    target_name: str,
    all_steps: list,
    block_id: str,
    stmt_name_to_id: dict[str, str],
) -> set[str]:
    """Find statement IDs that transitively depend on target_name."""
    # Get all sibling steps in the block
    siblings = [s for s in all_steps if s.block_id == block_id]

    # Build dependency map: for each step, which step names does it reference?
    # We check the attributes.params for values that look like step references
    deps: dict[str, set[str]] = {}
    for s in siblings:
        stmt_id = str(s.statement_id)
        dep_names: set[str] = set()
        for _, attr in s.attributes.params.items():
            # Attribute values that were resolved from step refs contain
            # data from other steps. We can't easily reverse this, so we
            # use statement ordering: if step B was created after step A
            # in the same block and they share the same container, B may
            # depend on A. For a more precise check, we'd need the AST.
            pass
        deps[stmt_id] = dep_names

    # Since we can't reliably extract deps from resolved attribute values,
    # use a conservative approach: delete all steps in the block that were
    # created AFTER the target step (by start_time)
    target_start = 0
    target_stmt_id = stmt_name_to_id.get(target_name, "")
    for s in siblings:
        if str(s.statement_id) == target_stmt_id:
            target_start = s.start_time or 0
            break

    downstream: set[str] = set()
    for s in siblings:
        sid = str(s.statement_id)
        if sid == target_stmt_id:
            continue
        # Steps created after the target are likely downstream
        if s.start_time and s.start_time >= target_start and sid != target_stmt_id:
            downstream.add(sid)

    return downstream


def _reset_ancestors_to_continue(step, store) -> None:
    """Reset ancestor blocks/containers to continue state (any terminal state)."""
    from afl.runtime.states import StepState

    seen: set[str] = set()

    current_id = step.block_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        ancestor = store.get_step(current_id)
        if ancestor is None:
            break
        if StepState.is_terminal(ancestor.state):
            ancestor.state = StepState.BLOCK_EXECUTION_CONTINUE
            ancestor.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
            ancestor.transition.clear_error()
            ancestor.transition.request_transition = False
            ancestor.transition.changed = True
            store.save_step(ancestor)
        current_id = ancestor.block_id

    current_id = step.container_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        ancestor = store.get_step(current_id)
        if ancestor is None:
            break
        if StepState.is_terminal(ancestor.state):
            ancestor.state = StepState.STATEMENT_BLOCKS_CONTINUE
            ancestor.transition.current_state = StepState.STATEMENT_BLOCKS_CONTINUE
            ancestor.transition.clear_error()
            ancestor.transition.request_transition = False
            ancestor.transition.changed = True
            store.save_step(ancestor)
        next_id = ancestor.block_id or ancestor.container_id
        current_id = next_id


def _reset_errored_ancestors(step, store) -> None:
    """Reset errored ancestor blocks/containers so execution can resume."""
    from afl.runtime.states import StepState

    seen: set[str] = set()

    # Walk up block_id chain (andThen blocks)
    current_id = step.block_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        ancestor = store.get_step(current_id)
        if ancestor is None:
            break
        if ancestor.state == StepState.STATEMENT_ERROR:
            ancestor.state = StepState.BLOCK_EXECUTION_CONTINUE
            ancestor.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
            ancestor.transition.clear_error()
            ancestor.transition.request_transition = False
            ancestor.transition.changed = True
            store.save_step(ancestor)
        current_id = ancestor.block_id

    # Walk up container_id chain (statement containers)
    current_id = step.container_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        ancestor = store.get_step(current_id)
        if ancestor is None:
            break
        if ancestor.state == StepState.STATEMENT_ERROR:
            ancestor.state = StepState.STATEMENT_BLOCKS_CONTINUE
            ancestor.transition.current_state = StepState.STATEMENT_BLOCKS_CONTINUE
            ancestor.transition.clear_error()
            ancestor.transition.request_transition = False
            ancestor.transition.changed = True
            store.save_step(ancestor)
        next_id = ancestor.block_id or ancestor.container_id
        current_id = next_id
