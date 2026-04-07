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

"""Step CRUD operations mixin for MongoStore."""

import logging
from collections.abc import Sequence
from typing import Any

try:
    from pymongo import ASCENDING
    from pymongo.errors import DuplicateKeyError
except ImportError:
    ASCENDING = 1
    try:
        from mongomock.collection import DuplicateKeyError  # type: ignore[no-redef]
    except ImportError:
        class DuplicateKeyError(Exception):  # type: ignore[no-redef]
            pass

from ..entities import LogDefinition, StepLogEntry
from ..persistence import IterationChanges
from ..states import StepState
from ..step import StepDefinition
from ..types import BlockId, StepId, VersionInfo, WorkflowId

from .base import _current_time_ms

logger = logging.getLogger(__name__)


class StepMixin:
    """Step, step log, and log CRUD operations."""

    # =========================================================================
    # Step Operations (PersistenceAPI)
    # =========================================================================

    def get_step(self, step_id: str) -> StepDefinition | None:
        """Fetch a step by ID."""
        doc = self._db.steps.find_one({"uuid": step_id})
        return self._doc_to_step(doc) if doc else None

    def get_steps_by_block(self, block_id: StepId | BlockId) -> Sequence[StepDefinition]:
        """Fetch all steps in a block."""
        docs = self._db.steps.find({"block_id": block_id})
        return [self._doc_to_step(doc) for doc in docs]

    def get_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a workflow."""
        docs = self._db.steps.find({"workflow_id": workflow_id})
        return [self._doc_to_step(doc) for doc in docs]

    def get_actionable_steps_by_workflow(self, workflow_id: str) -> Sequence[StepDefinition]:
        """Fetch steps that need processing — DB-level filtering.

        Excludes terminal steps (Complete/Error) and EventTransmit steps
        that do not have ``request_transition`` set.
        """
        from ..states import StepState

        docs = self._db.steps.find(
            {
                "workflow_id": workflow_id,
                "$nor": [
                    {"state": StepState.STATEMENT_COMPLETE},
                    {"state": StepState.STATEMENT_ERROR},
                    {"state": StepState.EVENT_TRANSMIT, "request_transition": {"$ne": True}},
                ],
            }
        )
        return [self._doc_to_step(doc) for doc in docs]

    def get_pending_resume_workflow_ids(self) -> list[str]:
        """Get workflow IDs with steps that need resume processing.

        Finds workflows with steps at EventTransmit (awaiting result) or
        at intermediate states like StatementBlocksBegin/Continue that
        need the evaluator to advance them to completion.
        """
        return self._db.steps.distinct(
            "workflow_id",
            {
                "state": {
                    "$in": [
                        StepState.EVENT_TRANSMIT,
                        StepState.STATEMENT_BLOCKS_BEGIN,
                        StepState.STATEMENT_BLOCKS_CONTINUE,
                        StepState.BLOCK_EXECUTION_BEGIN,
                        StepState.BLOCK_EXECUTION_CONTINUE,
                    ]
                },
            },
        )

    def get_steps_by_state(self, state: str) -> Sequence[StepDefinition]:
        """Fetch all steps in a given state."""
        docs = self._db.steps.find({"state": state})
        return [self._doc_to_step(doc) for doc in docs]

    def get_steps_by_container(self, container_id: str) -> Sequence[StepDefinition]:
        """Fetch all steps with a given container."""
        docs = self._db.steps.find({"container_id": container_id})
        return [self._doc_to_step(doc) for doc in docs]

    def save_step(self, step: StepDefinition) -> None:
        """Save a step to the store."""
        now = _current_time_ms()
        if not step.start_time:
            step.start_time = now
        step.last_modified = now
        doc = self._step_to_doc(step)
        self._db.steps.replace_one({"uuid": step.id}, doc, upsert=True)

    def delete_steps(self, step_ids: Sequence[str]) -> int:
        """Delete steps by their UUIDs."""
        if not step_ids:
            return 0
        result = self._db.steps.delete_many({"uuid": {"$in": list(step_ids)}})
        return result.deleted_count

    def delete_tasks_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete tasks associated with the given step IDs."""
        if not step_ids:
            return 0
        result = self._db.tasks.delete_many({"step_id": {"$in": list(step_ids)}})
        return result.deleted_count

    def delete_step_logs_for_steps(self, step_ids: Sequence[str]) -> int:
        """Delete step log entries for the given step IDs."""
        if not step_ids:
            return 0
        result = self._db.step_logs.delete_many({"step_id": {"$in": list(step_ids)}})
        return result.deleted_count

    def get_blocks_by_step(self, step_id: str) -> Sequence[StepDefinition]:
        """Fetch all block steps for a containing step."""
        docs = self._db.steps.find(
            {
                "container_id": step_id,
                "object_type": {"$in": ["AndThen", "AndMap", "AndMatch", "Block"]},
            }
        )
        return [self._doc_to_step(doc) for doc in docs]

    def get_workflow_root(self, workflow_id: str) -> StepDefinition | None:
        """Get the root step of a workflow."""
        doc = self._db.steps.find_one(
            {"workflow_id": workflow_id, "root_id": None, "container_id": None}
        )
        return self._doc_to_step(doc) if doc else None

    def step_exists(self, statement_id: str, block_id: StepId | BlockId | None) -> bool:
        """Check if a step already exists for a statement in a block."""
        query: dict[str, str | None] = {"statement_id": statement_id}
        if block_id:
            query["block_id"] = block_id
        else:
            query["block_id"] = None
        return self._db.steps.count_documents(query, limit=1) > 0

    def block_step_exists(self, statement_id: str, container_id: StepId) -> bool:
        """Check if a block step already exists for a statement in a container."""
        return (
            self._db.steps.count_documents(
                {"statement_id": statement_id, "container_id": container_id}, limit=1
            )
            > 0
        )

    # =========================================================================
    # Atomic Commit (PersistenceAPI)
    # =========================================================================

    def commit(self, changes: IterationChanges) -> None:
        """Atomically commit all iteration changes.

        Uses a MongoDB transaction when the server supports it (replica set
        or mongos). Falls back to non-transactional writes for standalone
        servers and mongomock.
        """
        try:
            session = self._client.start_session()
        except (NotImplementedError, Exception):
            # mongomock raises NotImplementedError;
            # standalone servers may raise ConfigurationError
            self._commit_changes(changes, session=None)
            return

        try:
            with session:
                with session.start_transaction():
                    self._commit_changes(changes, session=session)
        except Exception as exc:
            # Standalone MongoDB raises OperationFailure (code 20) for
            # transactions. Fall back to non-transactional writes.
            exc_msg = str(exc).lower()
            if "transaction" in exc_msg or "replica set" in exc_msg:
                self._commit_changes(changes, session=None)
            else:
                raise

    def _commit_changes(self, changes: IterationChanges, session: Any = None) -> None:
        """Write all iteration changes to MongoDB."""
        kwargs: dict[str, Any] = {}
        if session is not None:
            kwargs["session"] = session

        skipped_step_ids: set[str] = set()

        for step in changes.created_steps:
            doc = self._step_to_doc(step)
            try:
                self._db.steps.insert_one(doc, **kwargs)
            except DuplicateKeyError:
                skipped_step_ids.add(str(step.id))
                logger.debug(
                    "Skipping duplicate step: statement_id=%s block_id=%s container_id=%s",
                    doc.get("statement_id"),
                    doc.get("block_id"),
                    doc.get("container_id"),
                )

        for step in changes.updated_steps:
            doc = self._step_to_doc(step)
            # Optimistic concurrency: if the step has a non-zero sequence,
            # only update if the DB version matches (previous sequence).
            seq = step.version.sequence if step.version else 0
            if seq > 0:
                prev_seq = seq - 1
                result = self._db.steps.replace_one(
                    {"uuid": step.id, "version.sequence": prev_seq},
                    doc,
                    **kwargs,
                )
                if result.matched_count == 0:
                    # Fallback: unconditional write (step may have been
                    # created before version tracking was added, or
                    # another server already advanced it).
                    self._db.steps.replace_one({"uuid": step.id}, doc, **kwargs)
            else:
                self._db.steps.replace_one({"uuid": step.id}, doc, **kwargs)

        for task in changes.created_tasks:
            if task.step_id and task.step_id in skipped_step_ids:
                logger.debug(
                    "Skipping orphan task: name=%s step_id=%s",
                    task.name,
                    task.step_id,
                )
                continue
            doc = self._task_to_doc(task)
            self._db.tasks.insert_one(doc, **kwargs)

        # Continuation tasks — persisted atomically with step changes
        for task in changes.continuation_tasks:
            doc = self._task_to_doc(task)
            try:
                self._db.tasks.insert_one(doc, **kwargs)
            except DuplicateKeyError:
                logger.debug(
                    "Skipping duplicate continuation task: step_id=%s",
                    task.step_id,
                )

    # =========================================================================
    # Log Operations
    # =========================================================================

    def get_logs_by_runner(self, runner_id: str) -> Sequence[LogDefinition]:
        """Get logs for a runner."""
        docs = self._db.logs.find({"runner_id": runner_id}).sort("order", ASCENDING)
        return [self._doc_to_log(doc) for doc in docs]

    def save_log(self, log: LogDefinition) -> None:
        """Save a log entry."""
        doc = self._log_to_doc(log)
        self._db.logs.insert_one(doc)

    # =========================================================================
    # Step Log Operations
    # =========================================================================

    def save_step_log(self, entry: StepLogEntry) -> None:
        """Save a step log entry."""
        doc = self._step_log_to_doc(entry)
        self._db.step_logs.insert_one(doc)

    def get_step_logs_by_step(self, step_id: str) -> Sequence[StepLogEntry]:
        """Get step logs for a step, ordered by time ascending."""
        docs = self._db.step_logs.find({"step_id": step_id}).sort("time", ASCENDING)
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_step_logs_by_workflow(self, workflow_id: str) -> Sequence[StepLogEntry]:
        """Get step logs for a workflow, ordered by time ascending."""
        docs = self._db.step_logs.find({"workflow_id": workflow_id}).sort("time", ASCENDING)
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_step_logs_since(self, step_id: str, since_time: int) -> Sequence[StepLogEntry]:
        """Get step logs for a step newer than the given timestamp."""
        docs = self._db.step_logs.find({"step_id": step_id, "time": {"$gt": since_time}}).sort(
            "time", ASCENDING
        )
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_workflow_logs_since(self, workflow_id: str, since_time: int) -> Sequence[StepLogEntry]:
        """Get step logs for a workflow newer than the given timestamp."""
        docs = self._db.step_logs.find(
            {"workflow_id": workflow_id, "time": {"$gt": since_time}}
        ).sort("time", ASCENDING)
        return [self._doc_to_step_log(doc) for doc in docs]

    def get_step_logs_by_facet(self, facet_name: str, limit: int = 20) -> Sequence[StepLogEntry]:
        """Get recent step logs for a facet, ordered by time descending."""
        docs = self._db.step_logs.find({"facet_name": facet_name}).sort("time", -1).limit(limit)
        return [self._doc_to_step_log(doc) for doc in docs]

    # =========================================================================
    # Serialization Helpers — Steps, Logs, Step Logs
    # =========================================================================

    def _step_to_doc(self, step: StepDefinition) -> dict:
        """Convert StepDefinition to MongoDB document."""
        doc = {
            "uuid": step.id,
            "workflow_id": step.workflow_id,
            "object_type": step.object_type,
            "state": step.state,
            "statement_id": step.statement_id,
            "statement_name": step.statement_name,
            "container_id": step.container_id,
            "root_id": step.root_id,
            "block_id": step.block_id,
            "facet_name": step.facet_name or None,
            "is_block": step.is_block,
            "is_starting_step": getattr(step, "is_starting_step", False),
            "start_time": step.start_time,
            "last_modified": step.last_modified,
            "version": {
                "workflow_version": step.version.workflow_version,
                "step_schema_version": step.version.step_schema_version,
                "runtime_version": step.version.runtime_version,
                "sequence": step.version.sequence,
            }
            if step.version
            else {},
        }

        if step.foreach_var is not None:
            doc["foreach_var"] = step.foreach_var
            doc["foreach_value"] = step.foreach_value

        if step.attributes:
            doc["attributes"] = {
                "params": {
                    k: {"name": v.name, "value": v.value, "type_hint": v.type_hint}
                    for k, v in step.attributes.params.items()
                },
                "returns": {
                    k: {"name": v.name, "value": v.value, "type_hint": v.type_hint}
                    for k, v in step.attributes.returns.items()
                },
            }

        # Persist transition flags so round-tripped steps retain their
        # state-machine control signals (e.g. continue_step setting
        # request_transition=True for an EventTransmit-blocked step).
        if hasattr(step, "transition"):
            doc["request_transition"] = step.transition.request_transition

        error = getattr(step, "error", None)
        if error is None and hasattr(step, "transition"):
            error = getattr(step.transition, "error", None)
        if error:
            doc["error"] = str(error)

        return doc

    def _doc_to_step(self, doc: dict) -> StepDefinition:
        """Convert MongoDB document to StepDefinition."""
        from ..types import AttributeValue, FacetAttributes

        step = StepDefinition(
            id=StepId(doc["uuid"]),
            workflow_id=WorkflowId(doc["workflow_id"]),
            object_type=doc["object_type"],
            state=doc["state"],
        )

        # Restore the persisted request_transition flag.  Old documents
        # without the field default to False — the safe choice that lets
        # each handler decide when to advance.  StepTransition.initial()
        # sets request_transition=True which would cause the state changer
        # to skip the current state's handler and advance immediately,
        # bypassing EventTransmit blocking and block completion checks.
        step.transition.request_transition = doc.get("request_transition", False)

        step.statement_id = doc.get("statement_id")
        step.statement_name = doc.get("statement_name", "")
        step.container_id = StepId(doc["container_id"]) if doc.get("container_id") else None
        step.root_id = StepId(doc["root_id"]) if doc.get("root_id") else None
        step.block_id = BlockId(doc["block_id"]) if doc.get("block_id") else None
        step.facet_name = doc.get("facet_name") or ""
        # is_block is a computed property on StepDefinition; skip setting it.
        # is_starting_step may not exist on all StepDefinition versions.
        if hasattr(step, "is_starting_step"):
            step.is_starting_step = doc.get("is_starting_step", False)
        version_doc = doc.get("version")
        if isinstance(version_doc, dict):
            step.version = VersionInfo(
                workflow_version=version_doc.get("workflow_version", "1.0"),
                step_schema_version=version_doc.get("step_schema_version", "1.0"),
                runtime_version=version_doc.get("runtime_version", "0.1.0"),
                sequence=version_doc.get("sequence", 0),
            )
        else:
            step.version = VersionInfo()

        if "attributes" in doc:
            attrs = doc["attributes"]
            step.attributes = FacetAttributes()
            for k, v in attrs.get("params", {}).items():
                step.attributes.params[k] = AttributeValue(
                    v["name"], v["value"], v.get("type_hint", "Any")
                )
            for k, v in attrs.get("returns", {}).items():
                step.attributes.returns[k] = AttributeValue(
                    v["name"], v["value"], v.get("type_hint", "Any")
                )

        step.foreach_var = doc.get("foreach_var")
        step.foreach_value = doc.get("foreach_value")

        step.start_time = doc.get("start_time", 0)
        step.last_modified = doc.get("last_modified", 0)

        if doc.get("error") and hasattr(step, "transition"):
            step.transition.error = Exception(doc["error"])

        return step

    def _log_to_doc(self, log: LogDefinition) -> dict:
        """Convert LogDefinition to MongoDB document."""
        return {
            "uuid": log.uuid,
            "order": log.order,
            "runner_id": log.runner_id,
            "step_id": log.step_id,
            "object_id": log.object_id,
            "object_type": log.object_type,
            "note_originator": log.note_originator,
            "note_type": log.note_type,
            "note_importance": log.note_importance,
            "message": log.message,
            "state": log.state,
            "line": log.line,
            "file": log.file,
            "details": log.details,
            "time": log.time,
        }

    def _doc_to_log(self, doc: dict) -> LogDefinition:
        """Convert MongoDB document to LogDefinition."""
        return LogDefinition(
            uuid=doc["uuid"],
            order=doc["order"],
            runner_id=doc["runner_id"],
            step_id=doc.get("step_id"),
            object_id=doc.get("object_id", ""),
            object_type=doc.get("object_type", ""),
            note_originator=doc.get("note_originator", "workflow"),
            note_type=doc.get("note_type", "info"),
            note_importance=doc.get("note_importance", 5),
            message=doc.get("message", ""),
            state=doc.get("state", ""),
            line=doc.get("line", 0),
            file=doc.get("file", ""),
            details=doc.get("details", {}),
            time=doc.get("time", 0),
        )

    def _step_log_to_doc(self, entry: StepLogEntry) -> dict:
        """Convert StepLogEntry to MongoDB document."""
        return {
            "uuid": entry.uuid,
            "step_id": entry.step_id,
            "workflow_id": entry.workflow_id,
            "runner_id": entry.runner_id,
            "facet_name": entry.facet_name,
            "source": entry.source,
            "level": entry.level,
            "message": entry.message,
            "details": entry.details,
            "time": entry.time,
        }

    def _doc_to_step_log(self, doc: dict) -> StepLogEntry:
        """Convert MongoDB document to StepLogEntry."""
        return StepLogEntry(
            uuid=doc["uuid"],
            step_id=doc["step_id"],
            workflow_id=doc["workflow_id"],
            runner_id=doc.get("runner_id", ""),
            facet_name=doc.get("facet_name", ""),
            source=doc.get("source", "framework"),
            level=doc.get("level", "info"),
            message=doc.get("message", ""),
            details=doc.get("details", {}),
            time=doc.get("time", 0),
        )
