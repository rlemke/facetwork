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
CONTINUATION_TASK_LIST = "_fw_continue"
CONTINUATION_TASK_NAME = "_fw_continue"

# States a step transitions INTO in place whose next action is the step's own
# handler (not a parent's re-evaluation), so the step needs a self-continuation
# to be re-processed. Today these are the catch-recovery states; a new such
# state in the machine should be registered here. See generate_continuation_events.
_SELF_REPROCESS_STATES = frozenset({StepState.CATCH_BEGIN, StepState.CATCH_CONTINUE})


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

    # And for steps that transitioned IN PLACE into a self-blocking state
    # whose next action is the STEP'S OWN handler — today the CATCH_* states.
    # Ordinary updated steps only seed continuations for their PARENTS (to
    # re-check children); a step that enters CATCH_BEGIN needs *itself*
    # re-processed (to create/observe its recovery sub-block), which no
    # parent re-evaluation performs. Without a self-continuation it stranded
    # until the stuck-step sweep's ~5-min cycle reached it (the liveness-stall
    # residual). This closes that latency at the source. Bounded: one per such
    # step per iteration, deduped below and by add_continuation_task; the
    # continuation drives the step FORWARD (no failure feedback), so unlike a
    # per-conflict retrigger it cannot loop.
    self_reprocess_ids: set[str] = set()
    for step in changes.updated_steps:
        if step.state in _SELF_REPROCESS_STATES:
            sid = str(step.id)
            target_ids.add(sid)
            self_reprocess_ids.add(sid)

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
            data={
                "step_id": target_id,
                "reason": "catch_reprocess" if target_id in self_reprocess_ids
                else "child_progress",
            },
        )
        changes.add_continuation_task(task)

    if changes.continuation_tasks:
        logger.debug(
            "Generated %d continuation events for workflow %s",
            len(changes.continuation_tasks),
            workflow_id,
        )
