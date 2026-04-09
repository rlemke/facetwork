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

"""Build a hierarchical tree from a flat list of steps."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from facetwork.runtime.step import StepDefinition


@dataclass
class StepNode:
    """A node in the step hierarchy tree."""

    step: StepDefinition
    children: list[StepNode] = field(default_factory=list)
    depth: int = 0


def build_step_tree(steps: Sequence[StepDefinition]) -> list[StepNode]:
    """Build a hierarchical tree from a flat list of steps.

    The algorithm indexes steps by their hierarchy fields:
    - Roots have no container_id and no root_id
    - Block steps (is_block=True) belong to a container via container_id
    - Statement steps belong to a block via block_id

    Args:
        steps: Flat sequence of StepDefinition objects.

    Returns:
        List of root StepNode trees.
    """
    if not steps:
        return []

    # Index by id for quick lookup
    _by_id: dict[str, StepDefinition] = {s.id: s for s in steps}

    # Group block steps by container_id
    blocks_by_container: dict[str, list[StepDefinition]] = {}
    # Group statement steps by block_id
    stmts_by_block: dict[str, list[StepDefinition]] = {}

    roots: list[StepDefinition] = []

    for s in steps:
        if s.container_id is None and s.root_id is None:
            roots.append(s)
        elif s.is_block and s.container_id:
            blocks_by_container.setdefault(s.container_id, []).append(s)
        elif s.block_id:
            stmts_by_block.setdefault(s.block_id, []).append(s)

    def _build(step: StepDefinition, depth: int) -> StepNode:
        node = StepNode(step=step, depth=depth)
        # Attach block children (andThen blocks belonging to this step)
        for block in blocks_by_container.get(step.id, []):
            node.children.append(_build(block, depth + 1))
        # Attach statement children (statements inside this block)
        for stmt in stmts_by_block.get(step.id, []):
            node.children.append(_build(stmt, depth + 1))
        return node

    return [_build(r, 0) for r in roots]
