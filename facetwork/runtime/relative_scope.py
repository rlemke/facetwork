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

"""Relative ``$``-scoping support for the runtime (flag-gated).

Mirrors the compile-time model in ``docs/architecture/ffl-relative-scoping.md``:
``$`` = the immediate lexical container, ``$$`` one up, … Each container's
attribute surface is its params ∪ returns. This module builds the innermost-first
stack of those surfaces from a step's ancestry so the expression evaluator can
resolve ``$.field`` / ``$$.field`` by ``up_levels``.

Gated by ``FW_FFL_RELATIVE_SCOPING`` (default off). When off, the runtime uses
the legacy flat ``inputs`` resolution and never builds a stack.
"""

from __future__ import annotations

import os
from typing import Any


def relative_scoping_enabled() -> bool:
    """True when relative ``$``-scoping is enabled (default on).

    Disable with ``FW_FFL_RELATIVE_SCOPING=0``/``off`` for the legacy resolver.
    """
    return os.environ.get("FW_FFL_RELATIVE_SCOPING", "on").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _declared_return_names(context: Any, step: Any) -> set[str]:
    """Declared return names of the facet/workflow the step calls.

    Sourced from the definition (not the step's produced attributes, which are
    empty until the step completes) so a body can name a return before it is
    produced — the value read then defers until completion.
    """
    names: set[str] = set(step.attributes.returns.keys())
    if step.facet_name:
        fdef = context.get_facet_definition(step.facet_name)
        if fdef:
            for ret in fdef.get("returns") or []:
                if isinstance(ret, dict) and ret.get("name"):
                    names.add(ret["name"])
    return names


def _frame_for(context: Any, step: Any) -> dict[str, Any]:
    """A container frame: static param values + the set of return names.

    Params are call arguments — bound when the step initializes, so they are
    always ready and captured by value. Returns are produced when the step
    *completes*; a body may run before that, so returns are resolved lazily
    (with deferral) via the step's name rather than snapshotted here.
    """
    params: dict[str, Any] = {name: attr.value for name, attr in step.attributes.params.items()}
    return {
        "params": params,
        "returns": _declared_return_names(context, step),
        "name": step.statement_name,
    }


def build_scope_stack(context: Any, step: Any) -> list[dict[str, Any]]:
    """Innermost-first container-scope stack for ``step``.

    Index 0 is the step's immediate container (the step that owns its block),
    index 1 is that container's container (``$$``), … ending at the workflow
    root. Each entry carries the container's static params, its return-name set,
    and the container's step name (for deferring return lookups).
    """
    frames: list[dict[str, Any]] = []
    seen: set[str] = set()
    block_id = step.block_id
    while block_id:
        block_step = context._find_step(block_id)
        if block_step is None or block_step.container_id is None:
            break
        container = context._find_step(block_step.container_id)
        if container is None or container.id in seen:
            break
        seen.add(container.id)
        frames.append(_frame_for(context, container))
        block_id = container.block_id
    # Guarantee the workflow root is the outermost frame even if the walk above
    # stopped early (defensive; normally the loop already reaches it).
    root = context.get_workflow_root()
    if root is not None and root.id not in seen:
        frames.append(_frame_for(context, root))
    return frames
