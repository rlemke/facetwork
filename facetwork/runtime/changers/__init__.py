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

"""AFL state changers module.

State changers orchestrate the state machine for different step types:
- StepStateChanger: Full state machine for VariableAssignment
- BlockStateChanger: Simplified for AndThen blocks
- YieldStateChanger: Minimal for YieldAssignment
"""

from typing import TYPE_CHECKING

from .base import StateChanger
from .block_changer import BlockStateChanger
from .step_changer import StepStateChanger
from .yield_changer import YieldStateChanger

if TYPE_CHECKING:
    from ..evaluator import ExecutionContext
    from ..step import StepDefinition

__all__ = [
    "StateChanger",
    "StepStateChanger",
    "BlockStateChanger",
    "YieldStateChanger",
    "get_state_changer",
]


def get_state_changer(step: "StepDefinition", context: "ExecutionContext") -> StateChanger:
    """Factory function to get appropriate StateChanger for a step.

    Args:
        step: The step to get a changer for
        context: Execution context

    Returns:
        Appropriate StateChanger instance
    """
    from ..types import ObjectType

    if step.object_type == ObjectType.YIELD_ASSIGNMENT:
        return YieldStateChanger(step, context)
    elif ObjectType.is_block(step.object_type):
        return BlockStateChanger(step, context)
    else:
        return StepStateChanger(step, context)
