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

"""Type-checking-only base for the ``MongoStore`` mixins.

``MongoStore`` is assembled from several mixins (``StepMixin``, ``TaskMixin``,
``RunnerMixin``, …).  Each mixin freely uses ``self._db`` and a handful of
helpers/CRUD methods that are actually contributed by *other* mixins (or by
``BaseMixin`` / ``PersistenceAPI``).  To let a type checker see that shared
surface without disturbing the runtime MRO, every mixin inherits this stub at
type-check time and plain ``object`` at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.database import Database

    from ..entities import TaskDefinition, User, WorkflowDefinition
    from ..persistence import PersistenceAPI

    class _StoreInternals(PersistenceAPI):
        """Shared surface the mixins rely on — declared here, implemented elsewhere."""

        _db: Database
        _client: Any
        DEFAULT_LEASE_MS: int

        def _lease_ms(self) -> int: ...
        @staticmethod
        def _find_decoded(collection: Any, query: dict, decoder: Any) -> Any: ...
        @staticmethod
        def _upsert_by_uuid(collection: Any, doc: dict) -> None: ...
        def _task_to_doc(self, task: TaskDefinition) -> dict: ...
        def _workflow_to_doc(self, workflow: WorkflowDefinition) -> dict: ...
        def _doc_to_workflow(self, doc: dict) -> WorkflowDefinition: ...
        def _doc_to_user(self, doc: dict) -> User: ...
        def delete_runner(self, runner_id: str) -> dict: ...

    _MixinBase = _StoreInternals
else:
    _MixinBase = object


__all__ = ["_MixinBase"]
