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

"""Task-list routing by namespace.

A task's queue label is the **top-level namespace** of its facet/workflow name,
derived identically on both sides of the queue:

- **Producing side** — a task for facet ``osm.cache.Download`` is tagged with
  list ``osm`` (:func:`namespace_of`). Submission tags the bootstrap with the
  workflow's namespace.
- **Consuming side** — a runner polls the namespaces of the handlers it actually
  loaded (:func:`namespaces_for`), e.g. a runner serving ``osm.*`` facets polls
  ``osm``.

Because both sides compute the label from the same intrinsic fact (the qualified
name), it can never desync from where the handler lives: a task is claimed only
if it is on one of the runner's namespace lists **and** the runner has its
handler. This preserves per-domain queue isolation (osm work on ``osm``,
anthropic on ``anthropic``) without any per-runner configuration.

Unqualified names (no dot) and internal protocol names (``fw:…``, ``_fw…``) have
no namespace and route to :data:`DEFAULT_TASK_LIST`; protocol tasks are claimed
on their own dedicated lists.
"""

from __future__ import annotations

DEFAULT_TASK_LIST = "default"


def namespace_of(qualified_name: str, *, default: str = DEFAULT_TASK_LIST) -> str:
    """Top-level namespace segment of a qualified facet/workflow name.

    ``osm.cache.Download`` -> ``osm``; ``anthropic.Messages.Create`` ->
    ``anthropic``; an unqualified name (no dot) -> *default*. Internal/protocol
    names (``fw:…``, ``_fw…``) have no namespace and return *default*.
    """
    name = qualified_name or ""
    if name.startswith("fw:") or name.startswith("_fw"):
        return default
    head, sep, _ = name.partition(".")
    return head if sep and head else default


def namespaces_for(names) -> list[str]:
    """Distinct top-level namespaces a runner serves, from its facet names.

    The set of queue labels a runner should poll, derived from the handlers it
    actually loaded — no per-runner configuration. Unqualified/protocol names
    contribute nothing (those are claimed on their own dedicated lists).
    """
    out: set[str] = set()
    for n in names or ():
        ns = namespace_of(n, default="")
        if ns:
            out.add(ns)
    return sorted(out)


#: Namespace prefix of the facets that ship with the framework itself
#: (``fw.file``, ``fw.http``, ``fw.archive``, ``fw.exec``).
AMBIENT_PREFIX = "fw."


def is_ambient(facet_name: str) -> bool:
    """Whether *facet_name* is one of the framework's own built-in facets.

    These are **ambient**: every runner registers their handlers at startup and
    any runner can execute them, exactly like the ``fw:execute`` / ``fw:resume``
    protocol tasks. So every place that narrows what a runner will serve —
    ``--topics`` scoping above all — has to let them through, or their tasks are
    claimable by nobody.

    That failure is silent and fleet-wide: with all thirteen runners scoped to
    their own domain (``--topics census.*``), ``fw.http.Fetch`` sat pending with
    ``server_id=''`` while every runner logged "Registered 21 built-in
    handler(s)" and looked healthy. The scoping is applied in FOUR independent
    places (registry preload, advertise, event-name derivation, step polling),
    and fixing them one at a time took three deploys to find them all — hence
    one predicate rather than four copies of ``startswith``.
    """
    return (facet_name or "").startswith(AMBIENT_PREFIX)
