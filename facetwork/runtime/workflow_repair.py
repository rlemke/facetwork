"""Workflow repair with resume — the one entry point every surface should call.

``MongoStore.repair_workflow`` performs checks 1-6 and *detects* check 7
(stranded block steps: every task terminal, nothing errored, yet block-level
steps never cascaded, so the runner stays ``running`` indefinitely). It cannot
*fix* check 7, because advancing a step needs the evaluator and the persistence
layer must not import it.

That split briefly meant only the CLI resumed stranded steps, while the dashboard
button and the ``fw_repair_workflow`` MCP tool reported them and moved on — a
repair surface that shows you the problem and declines to fix it. This module is
the shared layer above the store where the resume belongs, so all three behave
identically.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)

# How many passes over the remaining stranded steps to attempt. resume_step() is
# O(depth) and cascades to parents, so one pass usually clears the set; a few
# extra rounds cover a parent that only becomes resumable once its children have
# advanced.
_MAX_RESUME_ROUNDS = 5


def repair_workflow(
    store: Any,
    runner_id: str,
    *,
    dry_run: bool = False,
    stranded_min_age_ms: int = 900_000,
) -> dict:
    """Run every repair check and resume any stranded block steps.

    Returns the store's result dict plus:
      ``stranded_resumed``    — how many stranded steps were advanced
      ``stranded_unresolved`` — how many could not be (see ``stranded_error``)
      ``stranded_error``      — why, when nothing could be attempted

    ``dry_run`` reports without changing anything, including the resume.
    """
    result = store.repair_workflow(
        runner_id, dry_run=dry_run, stranded_min_age_ms=stranded_min_age_ms
    )
    stranded = result.get("stranded_block_steps") or []
    result["stranded_resumed"] = 0
    result["stranded_unresolved"] = len(stranded)
    result["stranded_error"] = ""

    if not stranded or dry_run:
        return result

    # The AST is needed to advance a step. Prefer the runner's own snapshot: it is
    # self-contained and immune to later flow edits or re-seeds.
    runner_doc = None
    if hasattr(store, "_db"):
        runner_doc = store._db.runners.find_one({"uuid": runner_id})
    workflow_ast = (runner_doc or {}).get("workflow_ast") or {}
    program_ast = (runner_doc or {}).get("compiled_ast")
    workflow_name = ((runner_doc or {}).get("workflow") or {}).get("name", "")

    if not workflow_ast:
        result["stranded_error"] = (
            "runner has no stored workflow_ast — cannot resume these steps here"
        )
        logger.warning(
            "Repair: %d stranded step(s) in workflow %s cannot be resumed (no AST)",
            len(stranded),
            result.get("workflow_id", "?"),
        )
        return result

    from .evaluator import Evaluator

    evaluator = Evaluator(persistence=store)
    workflow_id = result["workflow_id"]
    todo = [s["step_id"] for s in stranded]

    for _round in range(_MAX_RESUME_ROUNDS):
        if not todo:
            break
        failed: list[str] = []
        for step_id in todo:
            try:
                evaluator.resume_step(
                    workflow_id,
                    step_id,
                    workflow_ast,
                    program_ast,
                    runner_id=runner_id,
                    qualified_workflow_name=workflow_name,
                )
                result["stranded_resumed"] += 1
            except Exception:
                failed.append(step_id)
                logger.debug(
                    "Repair: resume failed for step %s", step_id[:12], exc_info=True
                )
        todo = failed

    result["stranded_unresolved"] = len(todo)

    # Resuming the steps is not the whole repair: the runner itself must reach a
    # terminal state or it keeps showing as an active 49-hour run and every
    # sweep keeps selecting it. `Evaluator.resume_step` advances steps; the
    # runner transition lives in the runner service, which is not in play here —
    # so do it, guarded the same way (never complete a runner that still has
    # non-terminal tasks or steps).
    result["runner_completed"] = False
    if not todo and hasattr(store, "_db"):
        nonterminal_tasks = store._db.tasks.count_documents({
            "workflow_id": workflow_id,
            "state": {"$nin": ["completed", "failed", "ignored", "canceled", "cancelled"]},
        })
        from .states import StepState

        nonterminal_steps = sum(
            1
            for d in store._db.steps.find({"workflow_id": workflow_id}, {"state": 1})
            if not StepState.is_terminal(d.get("state", ""))
        )
        if not nonterminal_tasks and not nonterminal_steps:
            store._db.runners.update_many(
                {"workflow_id": workflow_id, "state": {"$nin": ["completed", "failed"]}},
                {"$set": {"state": "completed", "end_time": _now_ms()}},
            )
            result["runner_completed"] = True
            logger.info(
                "Repair: workflow %s fully terminal after resume — runner completed",
                workflow_id,
            )

    if todo:
        result["stranded_error"] = (
            f"{len(todo)} stranded step(s) could not be resumed after "
            f"{_MAX_RESUME_ROUNDS} rounds"
        )
    logger.info(
        "Repair: resumed %d/%d stranded step(s) in workflow %s",
        result["stranded_resumed"],
        len(stranded),
        workflow_id,
    )
    return result
