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

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ...dependencies import get_store

router = APIRouter(prefix="/steps")


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

    from facetwork.ast_utils import find_all_workflows

    if flow.compiled_ast:
        program_dict = flow.compiled_ast
    elif flow.compiled_sources:
        from facetwork.emitter import JSONEmitter
        from facetwork.parser import FFLParser

        parser = FFLParser()
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


def _get_heartbeat_ts(step, store) -> int | None:
    """Return the last heartbeat timestamp (ms) for a running step's task, or None."""
    try:
        task = store.get_task_for_step(step.id)
    except Exception:
        return None
    if not task or task.state != "running":
        return None
    return task.task_heartbeat or task.updated or task.created


@router.post("/{step_id}/retry")
def retry_step(step_id: str, force: bool = False, store=Depends(get_store)):
    """Retry a failed step by resetting it to EVENT_TRANSMIT.

    By default refuses to proceed if the step's own task is currently
    RUNNING — resetting it to pending while a handler is in flight races
    with the live runner. Pass ``force=true`` to clear the running task
    anyway.
    """
    import time as _time

    from fastapi.responses import JSONResponse

    from facetwork.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
        TaskState,
    )
    from facetwork.runtime.states import StepState
    from facetwork.runtime.types import generate_id

    step = store.get_step(step_id)
    if not step:
        return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)

    prev_state = step.state
    task = store.get_task_for_step(step_id)

    # Safety: refuse to reset a step whose handler is currently running.
    if task is not None and task.state == TaskState.RUNNING and not force:
        return JSONResponse(
            status_code=409,
            content={
                "error": "step_task_running",
                "message": (
                    "This step's task is currently RUNNING. Pass ?force=true to reset it anyway."
                ),
                "blockers": [
                    {
                        "step_id": step_id,
                        "facet_name": step.facet_name or "",
                        "task_uuid": task.uuid,
                    }
                ],
            },
        )

    # Mixin sub-steps walk the full STEP_TRANSITIONS lifecycle starting
    # from CREATED (FACET_INIT re-binds params, body re-executes, capture
    # rewrites returns).  Resetting to EVENT_TRANSMIT — the default for
    # regular event-facet steps — would skip the body re-execution.
    # Resetting to CREATED makes the retry semantics match the runtime's
    # own creation path written by MixinBlocksBeginHandler.
    if _is_mixin_sub_step(step):
        step.state = StepState.CREATED
        step.transition.current_state = StepState.CREATED
        step.attributes.returns = {}
    else:
        # Reset step state to EVENT_TRANSMIT (matches evaluator.retry_step logic)
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
    step.transition.clear_error()
    step.transition.request_transition = False
    step.transition.changed = True
    store.save_step(step)

    # Reset associated task to pending
    if task is not None:
        forced_running = force and task.state == TaskState.RUNNING
        task.state = "pending"
        task.error = None
        store.save_task(task)
        if forced_running:
            forced_entry = StepLogEntry(
                uuid=generate_id(),
                step_id=step_id,
                workflow_id=step.workflow_id,
                facet_name=step.facet_name or "",
                source=StepLogSource.FRAMEWORK,
                level=StepLogLevel.WARNING,
                message="Forcibly retried while task was RUNNING",
                time=int(_time.time() * 1000),
            )
            store.save_step_log(forced_entry)

    # Reset errored ancestor blocks/containers so execution resumes
    _reset_errored_ancestors(step, store)

    # Emit step log entry for the manual retry
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
    return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)


@router.post("/{step_id}/retry-block")
def retry_block(step_id: str, force: bool = False, store=Depends(get_store)):
    """Retry all errored leaf steps under a block recursively.

    By default refuses if any errored leaf's task is currently RUNNING.
    Pass ``force=true`` to reset those tasks alongside the rest.
    """
    import time as _time

    from fastapi.responses import JSONResponse

    from facetwork.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
        TaskState,
    )
    from facetwork.runtime.states import StepState
    from facetwork.runtime.types import generate_id

    root = store.get_step(step_id)
    if not root:
        return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)

    # Collect all steps in the workflow
    all_steps = list(store.get_steps_by_workflow(root.workflow_id))

    # Build parent->children maps
    by_block: dict[str, list[Any]] = {}
    by_container: dict[str, list[Any]] = {}
    step_by_id: dict[str, Any] = {}
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

    # Safety: refuse if any errored leaf still has a RUNNING task — its
    # handler is in flight and resetting would race the live runner.
    if errored_leaves and not force:
        running_blockers: list[dict] = []
        for leaf in errored_leaves:
            t = store.get_task_for_step(leaf.id)
            if t is not None and t.state == TaskState.RUNNING:
                running_blockers.append(
                    {
                        "step_id": leaf.id,
                        "facet_name": leaf.facet_name or "",
                        "task_uuid": t.uuid,
                    }
                )
        if running_blockers:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "leaf_task_running",
                    "message": (
                        f"{len(running_blockers)} errored leaf(s) have running tasks. "
                        "Pass ?force=true to reset them anyway."
                    ),
                    "blockers": running_blockers,
                },
            )

    # Retry each leaf.  Mixin sub-steps reset to CREATED (so they walk
    # the full STEP_TRANSITIONS lifecycle and re-execute their body)
    # with cleared returns; regular leaves reset to EVENT_TRANSMIT.  The
    # ancestor-walk below picks MIXIN_BLOCKS_CONTINUE vs
    # STATEMENT_BLOCKS_CONTINUE per the leaf's mixin status.
    for leaf in errored_leaves:
        if _is_mixin_sub_step(leaf):
            leaf.state = StepState.CREATED
            leaf.transition.current_state = StepState.CREATED
            leaf.attributes.returns = {}
        else:
            leaf.state = StepState.EVENT_TRANSMIT
            leaf.transition.current_state = StepState.EVENT_TRANSMIT
        leaf.transition.clear_error()
        leaf.transition.request_transition = False
        leaf.transition.changed = True
        store.save_step(leaf)

        task = store.get_task_for_step(leaf.id)
        if task is not None:
            forced_running = force and task.state == TaskState.RUNNING
            task.state = "pending"
            task.error = None
            store.save_task(task)
            if forced_running:
                forced_entry = StepLogEntry(
                    uuid=generate_id(),
                    step_id=leaf.id,
                    workflow_id=leaf.workflow_id,
                    facet_name=leaf.facet_name or "",
                    source=StepLogSource.FRAMEWORK,
                    level=StepLogLevel.WARNING,
                    message="Forcibly retried during block retry while task was RUNNING",
                    time=int(_time.time() * 1000),
                )
                store.save_step_log(forced_entry)

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

    return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)


@router.post("/{step_id}/reset-block")
def reset_block(step_id: str, force: bool = False, store=Depends(get_store)):
    """Reset a block: delete all descendant steps and restart from scratch.

    By default refuses to proceed if any descendant step has a running task —
    deleting it would leave a runner mid-execution with no step to write back
    to. Pass ``force=true`` to stop and delete those running tasks anyway.
    """
    import time as _time

    from fastapi.responses import JSONResponse

    from facetwork.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
        TaskState,
    )
    from facetwork.runtime.states import StepState
    from facetwork.runtime.types import generate_id

    block = store.get_step(step_id)
    if not block:
        return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)

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

    # Safety: refuse to delete descendants that have a running task unless
    # the caller explicitly forces.  Wiping a step while its handler is in
    # flight orphans the runner (its step row would vanish).
    if descendant_ids and not force:
        running_blockers: list[dict] = []
        for ds_id in descendant_ids:
            ds_task = store.get_task_for_step(ds_id)
            if ds_task is not None and ds_task.state == TaskState.RUNNING:
                ds_step = store.get_step(ds_id)
                running_blockers.append(
                    {
                        "step_id": ds_id,
                        "facet_name": (ds_step.facet_name if ds_step else "") or "",
                        "task_uuid": ds_task.uuid,
                    }
                )
        if running_blockers:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "running_descendants",
                    "message": (
                        f"{len(running_blockers)} descendant step(s) have running tasks. "
                        "Pass ?force=true to stop and delete them."
                    ),
                    "blockers": running_blockers,
                },
            )

    # Delete descendants
    if descendant_ids:
        if force:
            for ds_id in descendant_ids:
                ds_task = store.get_task_for_step(ds_id)
                if ds_task is not None and ds_task.state == TaskState.RUNNING:
                    ds_step = store.get_step(ds_id)
                    forced_entry = StepLogEntry(
                        uuid=generate_id(),
                        step_id=ds_id,
                        workflow_id=(ds_step.workflow_id if ds_step else block.workflow_id),
                        facet_name=(ds_step.facet_name if ds_step else "") or "",
                        source=StepLogSource.FRAMEWORK,
                        level=StepLogLevel.WARNING,
                        message=(
                            f"Forcibly removed by reset of block {step_id} while task was RUNNING"
                        ),
                        time=int(_time.time() * 1000),
                    )
                    store.save_step_log(forced_entry)
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

    return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)


@router.post("/{step_id}/rerun")
def rerun_step(step_id: str, force: bool = False, store=Depends(get_store)):
    """Re-run a step: reset it to EventTransmit and delete downstream dependents.

    By default refuses to proceed if any dependent step has a running task —
    deleting it would leave a runner mid-execution with no step to write back
    to. Pass ``force=true`` to stop and delete those running tasks anyway.
    """
    import time as _time

    from fastapi.responses import JSONResponse

    from facetwork.runtime.entities import (
        StepLogEntry,
        StepLogLevel,
        StepLogSource,
        TaskState,
    )
    from facetwork.runtime.states import StepState
    from facetwork.runtime.types import generate_id

    step = store.get_step(step_id)
    if not step:
        return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)

    prev_state = step.state
    block_id = step.block_id

    # Mixin sub-steps don't live in a block, so the normal
    # find-downstream-in-the-same-block path skips them entirely.  We
    # re-target the search at the parent step (the mixin's container)
    # so the rerun picks up everything that depended on the parent's
    # output — typically the natural intent of "re-run this mixin".
    # The mixin sub-step itself is reset to CREATED below in the same
    # path as a regular target, just with a different reset state.
    search_step = step
    if _is_mixin_sub_step(step) and step.container_id:
        parent = store.get_step(step.container_id)
        if parent is not None:
            search_step = parent
            block_id = parent.block_id

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

    # Find downstream sibling steps in the same block of search_step
    # (the parent step when target is a mixin sub-step, otherwise the
    # target itself).  A step is downstream if it was created after
    # search_step's statement and depends on its results.
    target_stmt_id = str(search_step.statement_id) if search_step.statement_id else ""
    downstream_step_ids: list[str] = []

    if block_id and target_stmt_id:
        # Get sibling step names for dependency inference
        stmt_name_to_id: dict[str, str] = {}
        for s in all_steps:
            if s.block_id == block_id and s.statement_name:
                stmt_name_to_id[s.statement_name] = str(s.statement_id)

        # Find steps that transitively depend on search_step, from the real
        # reference graph in the compiled AST (not a start_time heuristic).
        target_name = search_step.statement_name or ""
        compiled_ast = _compiled_ast_for_workflow(step.workflow_id, store)
        downstream_stmts = _find_downstream_by_name(
            target_name,
            all_steps,
            block_id,
            stmt_name_to_id,
            compiled_ast,
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

    # When re-running a mixin sub-step we also need to clear the
    # parent's body so the parent's andThen re-executes fresh against
    # the new mixin returns.  Otherwise the body's already-completed
    # child steps stay put and the parent skips them on the second
    # pass.  Delete the parent's body block(s) and their descendants;
    # the parent will re-create them when it walks STATEMENT_BLOCKS
    # again.
    if _is_mixin_sub_step(step) and step.container_id:
        body_block_ids = [
            s.id
            for s in all_steps
            if s.container_id == step.container_id
            and s.id != step.id  # don't delete the mixin sub-step itself
            and not s.statement_name  # skip sibling mixin sub-steps
        ]
        for bid in body_block_ids:
            if bid not in downstream_step_ids:
                downstream_step_ids.append(bid)
            stack = [bid]
            seen = {bid}
            while stack:
                sid = stack.pop()
                for child_id in by_block.get(sid, []) + by_container.get(sid, []):
                    if child_id not in seen:
                        seen.add(child_id)
                        if child_id not in downstream_step_ids:
                            downstream_step_ids.append(child_id)
                        stack.append(child_id)

    # Safety: refuse to delete dependents that have a running task unless
    # the caller explicitly forces.  Re-running a step while a dependent is
    # mid-handler would orphan that handler (its step row would vanish).
    if downstream_step_ids and not force:
        running_blockers: list[dict] = []
        for ds_id in downstream_step_ids:
            ds_task = store.get_task_for_step(ds_id)
            if ds_task is not None and ds_task.state == TaskState.RUNNING:
                ds_step = store.get_step(ds_id)
                running_blockers.append(
                    {
                        "step_id": ds_id,
                        "facet_name": (ds_step.facet_name if ds_step else "") or "",
                        "task_uuid": ds_task.uuid,
                    }
                )
        if running_blockers:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "running_dependents",
                    "message": (
                        f"{len(running_blockers)} dependent step(s) have running tasks. "
                        "Pass ?force=true to stop and delete them."
                    ),
                    "blockers": running_blockers,
                },
            )

    # Delete downstream steps
    if downstream_step_ids:
        if force:
            forced_running = [
                ds_id
                for ds_id in downstream_step_ids
                if (t := store.get_task_for_step(ds_id)) is not None
                and t.state == TaskState.RUNNING
            ]
            for ds_id in forced_running:
                ds_step = store.get_step(ds_id)
                forced_entry = StepLogEntry(
                    uuid=generate_id(),
                    step_id=ds_id,
                    workflow_id=(ds_step.workflow_id if ds_step else step.workflow_id),
                    facet_name=(ds_step.facet_name if ds_step else "") or "",
                    source=StepLogSource.FRAMEWORK,
                    level=StepLogLevel.WARNING,
                    message=(
                        f"Forcibly removed by re-run of upstream step {step_id} "
                        f"while task was RUNNING"
                    ),
                    time=int(_time.time() * 1000),
                )
                store.save_step_log(forced_entry)
        store.delete_step_logs_for_steps(downstream_step_ids)
        store.delete_tasks_for_steps(downstream_step_ids)
        store.delete_steps(downstream_step_ids)

    # Reset the target step.  Mixin sub-steps reset to CREATED so they
    # walk the whole STEP_TRANSITIONS lifecycle again (re-bind params,
    # re-run body, re-capture); other steps reset to EVENT_TRANSMIT,
    # matching the regular evaluator.retry_step semantics.
    if _is_mixin_sub_step(step):
        step.state = StepState.CREATED
        step.transition.current_state = StepState.CREATED
    else:
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
    step.transition.clear_error()
    step.transition.request_transition = False
    step.transition.changed = True
    # Clear old return values so downstream steps get fresh data
    step.attributes.returns = {}
    store.save_step(step)

    # When the rerun target is a mixin sub-step, also clear the parent's
    # returns so the parent's body re-yields fresh results.  The parent's
    # body was deleted above (via downstream_step_ids), so capture will
    # rebuild returns from scratch on the second pass.
    if _is_mixin_sub_step(step) and step.container_id:
        parent = store.get_step(step.container_id)
        if parent is not None:
            parent.attributes.returns = {}
            store.save_step(parent)

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
            f"Step re-run (was {prev_state}): {len(downstream_step_ids)} downstream step(s) deleted"
        ),
        time=int(_time.time() * 1000),
    )
    store.save_step_log(entry)

    return RedirectResponse(url=f"/v3/steps/{step_id}", status_code=303)


def _compiled_ast_for_workflow(workflow_id: str, store) -> dict | None:
    """The compiled AST for a workflow run (from its runner), or None."""
    runner = store.get_runner(workflow_id)
    if runner is None or not getattr(runner, "compiled_ast", None):
        runner = next(
            (
                r
                for r in store.get_runners_by_workflow(workflow_id)
                if getattr(r, "compiled_ast", None)
            ),
            None,
        )
    return getattr(runner, "compiled_ast", None) if runner else None


def _iter_ast_blocks(node):
    """Yield every andThen-block dict (has a 'steps' list) in the compiled AST."""
    if isinstance(node, dict):
        if isinstance(node.get("steps"), list):
            yield node
        for v in node.values():
            yield from _iter_ast_blocks(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_ast_blocks(item)


def _find_downstream_by_name(
    target_name: str,
    all_steps: list,
    block_id: str,
    stmt_name_to_id: dict[str, str],
    compiled_ast: dict | None,
) -> set[str]:
    """Runtime statement IDs (in this block) that TRANSITIVELY depend on
    ``target_name``, computed from the compiled AST's real ``step.field``
    references — not by start_time.

    The old timestamp heuristic deleted every sibling that *started* after the
    target, destroying unrelated parallel-block work. We now build the block's
    real dependency graph (reusing the runtime's ``DependencyGraph``), reverse
    it, and BFS out from the target so only true dependents are removed. If the
    AST is unavailable or the block can't be located we return the empty set —
    a safe under-delete (only the target itself resets) rather than the previous
    over-delete.
    """
    if not compiled_ast or not target_name:
        return set()
    try:
        from facetwork.runtime.dependency import DependencyGraph
    except Exception:
        return set()

    # A statement name is unique within its block but MAY recur across blocks,
    # so among candidate AST blocks containing the target pick the one whose
    # statement names best overlap this runtime block's names.
    block_names = set(stmt_name_to_id.keys())
    best_graph = None
    best_overlap = -1
    for block_ast in _iter_ast_blocks(compiled_ast):
        try:
            graph = DependencyGraph.from_ast(block_ast, set(), compiled_ast)
        except Exception:
            continue
        if target_name not in graph.name_to_id:
            continue
        overlap = len(block_names & set(graph.name_to_id.keys()))
        if overlap > best_overlap:
            best_overlap, best_graph = overlap, graph

    if best_graph is None:
        return set()

    # Reverse the dependency edges (id -> dependents) and BFS from the target.
    reverse: dict[str, set[str]] = {}
    for sid, dep_ids in best_graph.dependencies.items():
        for d in dep_ids:
            reverse.setdefault(d, set()).add(sid)

    downstream_ids: set[str] = set()
    queue = [best_graph.name_to_id[target_name]]
    while queue:
        cur = queue.pop(0)
        for dependent in reverse.get(cur, set()):
            if dependent not in downstream_ids:
                downstream_ids.add(dependent)
                queue.append(dependent)

    # Map graph ids -> names -> this block's runtime statement IDs.
    id_to_name = {v: k for k, v in best_graph.name_to_id.items()}
    result: set[str] = set()
    for gid in downstream_ids:
        name = id_to_name.get(gid)
        rid = stmt_name_to_id.get(name) if name else None
        if rid:
            result.add(rid)
    return result


def _reset_ancestors_to_continue(step, store) -> None:
    """Reset ancestor blocks/containers to continue state (any terminal state).

    When the immediate descendant being re-run is a mixin sub-step, the
    container resumes at ``MIXIN_BLOCKS_CONTINUE`` so it re-waits on the
    aliased mixin instead of skipping past the mixin phase entirely.
    """
    from facetwork.runtime.states import StepState

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
    immediate_child = step
    while current_id and current_id not in seen:
        seen.add(current_id)
        ancestor = store.get_step(current_id)
        if ancestor is None:
            break
        if StepState.is_terminal(ancestor.state):
            resume_state = _reset_container_resume_state(ancestor, immediate_child)
            ancestor.state = resume_state
            ancestor.transition.current_state = resume_state
            ancestor.transition.clear_error()
            ancestor.transition.request_transition = False
            ancestor.transition.changed = True
            store.save_step(ancestor)
        next_id = ancestor.block_id or ancestor.container_id
        immediate_child = ancestor
        current_id = next_id


from facetwork.runtime.mixin_alias import is_mixin_sub_step as _is_mixin_sub_step


def _reset_container_resume_state(ancestor, child) -> str:
    """Return the resume state for an errored container step depending
    on whether the immediate child being restarted is a mixin sub-step
    (errored at the parent's mixin phase) or a regular body step
    (errored at the parent's statement-block phase)."""
    from facetwork.runtime.states import StepState

    if _is_mixin_sub_step(child):
        return StepState.MIXIN_BLOCKS_CONTINUE
    return StepState.STATEMENT_BLOCKS_CONTINUE


def _reset_errored_ancestors(step, store) -> None:
    """Reset errored ancestor blocks/containers so execution can resume."""
    from facetwork.runtime.states import StepState

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

    # Walk up container_id chain (statement containers).  When the
    # immediate descendant being restarted is a mixin sub-step, the
    # container errored at its mixin phase (MIXIN_BLOCKS_CONTINUE) —
    # not at its statement phase — so we resume the container there
    # instead, otherwise it would skip waiting on the mixin entirely.
    current_id = step.container_id
    immediate_child = step
    while current_id and current_id not in seen:
        seen.add(current_id)
        ancestor = store.get_step(current_id)
        if ancestor is None:
            break
        if ancestor.state == StepState.STATEMENT_ERROR:
            resume_state = _reset_container_resume_state(ancestor, immediate_child)
            ancestor.state = resume_state
            ancestor.transition.current_state = resume_state
            ancestor.transition.clear_error()
            ancestor.transition.request_transition = False
            ancestor.transition.changed = True
            store.save_step(ancestor)
        next_id = ancestor.block_id or ancestor.container_id
        immediate_child = ancestor
        current_id = next_id
