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

"""Compute a DAG layout from a flat list of steps for SVG rendering."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from afl.runtime.step import StepDefinition

# Node dimensions and spacing
NODE_W = 160
NODE_H = 40
H_GAP = 60  # horizontal gap between layers
V_GAP = 40  # vertical gap between siblings


@dataclass
class DagNode:
    """A node in the DAG layout with absolute coordinates."""

    step_id: str
    label: str
    state: str
    x: int = 0
    y: int = 0
    w: int = NODE_W
    h: int = NODE_H


@dataclass
class DagEdge:
    """A directed edge between two DAG nodes."""

    source_id: str
    target_id: str
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0


@dataclass
class DagLayout:
    """Complete DAG layout with positioned nodes and edges."""

    nodes: list[DagNode] = field(default_factory=list)
    edges: list[DagEdge] = field(default_factory=list)
    width: int = 0
    height: int = 0


def compute_dag_layout(steps: Sequence[StepDefinition]) -> DagLayout | None:
    """Compute a layered DAG layout from a flat list of steps.

    Uses the step hierarchy (container_id, block_id) to determine parent-child
    relationships.  Layers are assigned via BFS from root steps.

    Returns ``None`` if there are no steps to layout.
    """
    if not steps:
        return None

    # Index steps by id
    by_id: dict[str, StepDefinition] = {s.id: s for s in steps}

    # Determine parent for each step
    parent_of: dict[str, str | None] = {}
    children_of: dict[str, list[str]] = {}

    for s in steps:
        parent_id: str | None = None
        if s.block_id and s.block_id in by_id:
            parent_id = s.block_id
        elif s.container_id and s.container_id in by_id:
            parent_id = s.container_id
        parent_of[s.id] = parent_id
        children_of.setdefault(s.id, [])
        if parent_id:
            children_of.setdefault(parent_id, []).append(s.id)

    # Find root nodes (no parent)
    roots = [s.id for s in steps if parent_of.get(s.id) is None]

    # Assign layers via BFS
    layers: dict[str, int] = {}
    queue: list[str] = list(roots)
    for r in roots:
        layers[r] = 0
    while queue:
        current = queue.pop(0)
        for child_id in children_of.get(current, []):
            if child_id not in layers:
                layers[child_id] = layers[current] + 1
                queue.append(child_id)

    # Handle any orphan steps not reached by BFS
    for s in steps:
        if s.id not in layers:
            layers[s.id] = 0

    # Group by layer
    layer_groups: dict[int, list[str]] = {}
    for sid, layer in layers.items():
        layer_groups.setdefault(layer, []).append(sid)

    # Sort within each layer for deterministic output
    for layer in layer_groups:
        layer_groups[layer].sort()

    # Position nodes: x by layer depth, y by position within layer
    nodes: dict[str, DagNode] = {}
    for layer_idx in sorted(layer_groups.keys()):
        members = layer_groups[layer_idx]
        for pos, sid in enumerate(members):
            s = by_id[sid]
            label = s.statement_name or s.facet_name or s.id[:8]
            if len(label) > 20:
                label = label[:18] + ".."
            x = 20 + layer_idx * (NODE_W + H_GAP)
            y = 20 + pos * (NODE_H + V_GAP)
            nodes[sid] = DagNode(
                step_id=sid,
                label=label,
                state=s.state,
                x=x,
                y=y,
            )

    # Create edges
    edges: list[DagEdge] = []
    for sid, parent_id in parent_of.items():
        if parent_id and parent_id in nodes and sid in nodes:
            src = nodes[parent_id]
            tgt = nodes[sid]
            edges.append(
                DagEdge(
                    source_id=parent_id,
                    target_id=sid,
                    x1=src.x + src.w,
                    y1=src.y + src.h // 2,
                    x2=tgt.x,
                    y2=tgt.y + tgt.h // 2,
                )
            )

    # Compute overall dimensions
    all_nodes = list(nodes.values())
    svg_width = max((n.x + n.w for n in all_nodes), default=0) + 40
    svg_height = max((n.y + n.h for n in all_nodes), default=0) + 40

    return DagLayout(
        nodes=all_nodes,
        edges=edges,
        width=svg_width,
        height=svg_height,
    )
