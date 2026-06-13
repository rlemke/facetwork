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

"""Dataclasses for the Claude workflow catalog."""

from __future__ import annotations

from dataclasses import dataclass, field

# Revision lifecycle / review gate.
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"

# Catalog entry kind.
KIND_WORKFLOW = "workflow"
KIND_LIBRARY = "library"


@dataclass
class DependencyPin:
    """A pinned dependency on a library revision.

    Immutable once recorded on a revision: it names the exact ``revision_id``
    (and its ``content_hash``) so a later change to the base library cannot
    alter an existing dependent revision.
    """

    slug: str
    revision_id: str
    version: int
    content_hash: str


@dataclass
class CatalogRevision:
    """An immutable, content-hashed snapshot of one workflow version.

    Stored in the ``claude_workflow_revisions`` collection. Never mutated after
    creation except for the ``status`` field flipping ``draft`` -> ``published``
    (the review gate) — the FFL, compiled flow, and hash are frozen.
    """

    revision_id: str
    slug: str
    version: int
    content_hash: str
    ffl_source: str  # this revision's OWN FFL (libraries are merged at compile time)
    flow_id: str  # uuid of the materialized (merged + compiled) FlowDefinition
    entry_workflow: str  # fully-qualified name of the workflow to run
    workflow_id: str  # uuid of the entry workflow's WorkflowDefinition
    workflow_names: list[str] = field(default_factory=list)
    param_schema: list[dict] = field(default_factory=list)
    returns_schema: list[dict] = field(default_factory=list)
    facets_used: list[str] = field(default_factory=list)
    depends_on: list[DependencyPin] = field(default_factory=list)
    status: str = STATUS_DRAFT
    is_valid: bool = True
    warnings: list[str] = field(default_factory=list)
    author: str = "claude"
    teams: list[str] = field(default_factory=list)  # from the entry workflow's Teams mixin
    note: str = ""
    # Free-text (markdown) narrative of *why* this revision exists — the request
    # it was built from and how the workflow addresses it. Recorded by the author
    # (Claude) so the catalog/UI can explain the workflow, not just show its FFL.
    summary: str = ""
    created_at: int = 0


@dataclass
class CatalogEntry:
    """The stable, discoverable index for one logical workflow or library.

    Stored in the ``claude_workflows`` collection. Mutable metadata; the
    runnable bodies live in immutable ``CatalogRevision``s.
    """

    slug: str
    kind: str = KIND_WORKFLOW
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)  # teams this workflow is listed for
    latest_version: int = 0
    published_version: int | None = None  # highest published version, if any
    author: str = "claude"
    created_at: int = 0
    updated_at: int = 0
