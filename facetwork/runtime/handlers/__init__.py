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

"""AFL state handlers module.

State handlers implement the logic for each state in the state machine.
Each state has Begin/End or just process handlers.
"""

from typing import TYPE_CHECKING

from ..states import StepState
from .base import StateHandler

if TYPE_CHECKING:
    from ..evaluator import ExecutionContext
    from ..step import StepDefinition

# Handler imports
from .block_execution import (
    BlockExecutionBeginHandler,
    BlockExecutionContinueHandler,
    BlockExecutionEndHandler,
)
from .blocks import (
    MixinBlocksBeginHandler,
    MixinBlocksContinueHandler,
    MixinBlocksEndHandler,
    StatementBlocksBeginHandler,
    StatementBlocksContinueHandler,
    StatementBlocksEndHandler,
)
from .capture import (
    MixinCaptureBeginHandler,
    MixinCaptureEndHandler,
    StatementCaptureBeginHandler,
    StatementCaptureEndHandler,
)
from .catch_execution import (
    CatchBeginHandler,
    CatchContinueHandler,
    CatchEndHandler,
)
from .completion import (
    EventTransmitHandler,
    StatementCompleteHandler,
    StatementEndHandler,
)
from .initialization import (
    FacetInitializationBeginHandler,
    FacetInitializationEndHandler,
    StatementBeginHandler,
)
from .scripts import (
    FacetScriptsBeginHandler,
    FacetScriptsEndHandler,
    StatementScriptsBeginHandler,
    StatementScriptsEndHandler,
)

__all__ = [
    "StateHandler",
    "get_handler",
    # Initialization
    "StatementBeginHandler",
    "FacetInitializationBeginHandler",
    "FacetInitializationEndHandler",
    # Scripts
    "FacetScriptsBeginHandler",
    "FacetScriptsEndHandler",
    "StatementScriptsBeginHandler",
    "StatementScriptsEndHandler",
    # Blocks
    "MixinBlocksBeginHandler",
    "MixinBlocksContinueHandler",
    "MixinBlocksEndHandler",
    "StatementBlocksBeginHandler",
    "StatementBlocksContinueHandler",
    "StatementBlocksEndHandler",
    # Capture
    "MixinCaptureBeginHandler",
    "MixinCaptureEndHandler",
    "StatementCaptureBeginHandler",
    "StatementCaptureEndHandler",
    # Completion
    "StatementEndHandler",
    "StatementCompleteHandler",
    "EventTransmitHandler",
    # Block execution
    "BlockExecutionBeginHandler",
    "BlockExecutionContinueHandler",
    "BlockExecutionEndHandler",
    # Catch execution
    "CatchBeginHandler",
    "CatchContinueHandler",
    "CatchEndHandler",
]


# Registry mapping states to handler classes
STATE_HANDLERS: dict[str, type[StateHandler]] = {
    StepState.CREATED: StatementBeginHandler,
    StepState.FACET_INIT_BEGIN: FacetInitializationBeginHandler,
    StepState.FACET_INIT_END: FacetInitializationEndHandler,
    StepState.FACET_SCRIPTS_BEGIN: FacetScriptsBeginHandler,
    StepState.FACET_SCRIPTS_END: FacetScriptsEndHandler,
    StepState.STATEMENT_SCRIPTS_BEGIN: StatementScriptsBeginHandler,
    StepState.STATEMENT_SCRIPTS_END: StatementScriptsEndHandler,
    StepState.MIXIN_BLOCKS_BEGIN: MixinBlocksBeginHandler,
    StepState.MIXIN_BLOCKS_CONTINUE: MixinBlocksContinueHandler,
    StepState.MIXIN_BLOCKS_END: MixinBlocksEndHandler,
    StepState.MIXIN_CAPTURE_BEGIN: MixinCaptureBeginHandler,
    StepState.MIXIN_CAPTURE_END: MixinCaptureEndHandler,
    StepState.EVENT_TRANSMIT: EventTransmitHandler,
    StepState.STATEMENT_BLOCKS_BEGIN: StatementBlocksBeginHandler,
    StepState.STATEMENT_BLOCKS_CONTINUE: StatementBlocksContinueHandler,
    StepState.STATEMENT_BLOCKS_END: StatementBlocksEndHandler,
    StepState.STATEMENT_CAPTURE_BEGIN: StatementCaptureBeginHandler,
    StepState.STATEMENT_CAPTURE_END: StatementCaptureEndHandler,
    StepState.STATEMENT_END: StatementEndHandler,
    StepState.STATEMENT_COMPLETE: StatementCompleteHandler,
    # Block execution states
    StepState.BLOCK_EXECUTION_BEGIN: BlockExecutionBeginHandler,
    StepState.BLOCK_EXECUTION_CONTINUE: BlockExecutionContinueHandler,
    StepState.BLOCK_EXECUTION_END: BlockExecutionEndHandler,
    # Catch execution states
    StepState.CATCH_BEGIN: CatchBeginHandler,
    StepState.CATCH_CONTINUE: CatchContinueHandler,
    StepState.CATCH_END: CatchEndHandler,
}


def get_handler(
    state: str,
    step: "StepDefinition",
    context: "ExecutionContext",
) -> StateHandler | None:
    """Get the appropriate handler for a state.

    Args:
        state: The state to get a handler for
        step: The step being processed
        context: Execution context

    Returns:
        StateHandler instance, or None if no handler for state
    """
    handler_class = STATE_HANDLERS.get(state)
    if handler_class is None:
        return None
    return handler_class(step, context)
