"""Continuation event generation for event-driven step processing.

When a step completes or progresses, continuation events notify parent
blocks so they can re-evaluate child completion.  This is the Python
equivalent of the Scala ContextCache.addContinuationEvents() pattern.

Continuation events are lightweight tasks on the ``_afl_continue`` task
list.  Any runner can claim and process them, enabling distributed
multi-server execution without per-workflow locks.
"""

import logging
import time as _time

from .entities import TaskDefinition, TaskState
from .persistence import IterationChanges
from .states import StepState
from .types import generate_id

logger = logging.getLogger(__name__)

# Internal task list for continuation events
CONTINUATION_TASK_LIST = "_afl_continue"
CONTINUATION_TASK_NAME = "_afl_continue"


def generate_continuation_events(
    changes: IterationChanges,
    dirty_blocks: set[str] | None = None,
) -> None:
    """Generate continuation tasks for parent blocks that need re-evaluation.

    Inspects updated and created steps to determine which parent blocks
    should be notified.  Continuation tasks are added directly to
    ``changes.continuation_tasks`` for atomic commit alongside step
    changes.

    Args:
        changes: The current iteration's accumulated changes.
        dirty_blocks: Set of block/container IDs that were marked dirty
            during step processing.  If provided, only these blocks get
            continuation events (more targeted).  If None, derives
            parents from the changed steps.
    """
    now = int(_time.time() * 1000)

    # Collect parent step IDs that need re-evaluation
    target_ids: set[str] = set()

    if dirty_blocks is not None:
        # Use the dirty set directly — these are block_id/container_id
        # values marked by _process_step when children progressed.
        target_ids.update(bid for bid in dirty_blocks if bid)
    else:
        # Derive from changed steps
        for step in changes.updated_steps:
            if step.block_id:
                target_ids.add(str(step.block_id))
            if step.container_id:
                target_ids.add(str(step.container_id))
        for step in changes.created_steps:
            if step.block_id:
                target_ids.add(str(step.block_id))
            if step.container_id:
                target_ids.add(str(step.container_id))

    # Also generate continuations for newly created steps that need
    # processing (they start in CREATED state with request_transition).
    for step in changes.created_steps:
        if not StepState.is_terminal(step.state):
            target_ids.add(str(step.id))

    if not target_ids:
        return

    # Determine workflow_id from the first available step
    workflow_id = ""
    runner_id = ""
    for step in changes.updated_steps or changes.created_steps:
        workflow_id = step.workflow_id
        break

    for target_id in target_ids:
        # Skip if we already have a continuation for this step
        if target_id in changes._continuation_step_ids:
            continue

        task = TaskDefinition(
            uuid=generate_id(),
            name=CONTINUATION_TASK_NAME,
            runner_id=runner_id,
            workflow_id=workflow_id,
            flow_id="",
            step_id=target_id,
            state=TaskState.PENDING,
            created=now,
            updated=now,
            task_list_name=CONTINUATION_TASK_LIST,
            data={"step_id": target_id, "reason": "child_progress"},
        )
        changes.add_continuation_task(task)

    if changes.continuation_tasks:
        logger.debug(
            "Generated %d continuation events for workflow %s",
            len(changes.continuation_tasks),
            workflow_id,
        )
