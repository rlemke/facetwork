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

"""Runner CRUD operations mixin for MongoStore."""

from collections.abc import Sequence
from dataclasses import asdict

from ..entities import Parameter, RunnerDefinition
from ._internals import _MixinBase


class RunnerMixin(_MixinBase):
    """Runner CRUD operations."""

    def get_runner(self, runner_id: str) -> RunnerDefinition | None:
        """Get a runner by ID."""
        return self._find_decoded(self._db.runners, {"uuid": runner_id}, self._doc_to_runner)

    def get_runners_by_workflow(self, workflow_id: str) -> Sequence[RunnerDefinition]:
        """Get all runners for a workflow."""
        docs = self._db.runners.find({"workflow_id": workflow_id})
        return [self._doc_to_runner(doc) for doc in docs]

    def get_runners_by_state(self, state: str) -> Sequence[RunnerDefinition]:
        """Get runners by state."""
        docs = self._db.runners.find({"state": state})
        return [self._doc_to_runner(doc) for doc in docs]

    def save_runner(self, runner: RunnerDefinition) -> None:
        """Save a runner."""
        self._upsert_by_uuid(self._db.runners, self._runner_to_doc(runner))

    def update_runner_state(self, runner_id: str, state: str) -> None:
        """Update runner state."""
        self._db.runners.update_one({"uuid": runner_id}, {"$set": {"state": state}})

    def get_all_runners(self, limit: int = 100) -> Sequence[RunnerDefinition]:
        """Get all runners, most recent first."""
        docs = self._db.runners.find().sort("start_time", -1).limit(limit)
        return [self._doc_to_runner(doc) for doc in docs]

    def delete_runner(self, runner_id: str) -> dict:
        """Delete a runner and all of its execution data.

        Cascades to the run's steps, tasks, and step logs (keyed by
        ``workflow_id``) plus the workflow logs (keyed by ``runner_id``).
        A missing runner is a no-op returning ``found=False``.
        """
        runner = self.get_runner(runner_id)
        if runner is None:
            return {
                "found": False,
                "runners": 0,
                "steps": 0,
                "tasks": 0,
                "step_logs": 0,
                "logs": 0,
            }
        workflow_id = runner.workflow_id
        # A task/step-log belongs to the run by ``runner_id``; but continuation/
        # resume tasks (and agent-inserted ``fw:resume`` tasks) may carry only
        # ``workflow_id`` with no ``runner_id``. Match BOTH so nothing is orphaned.
        # (Guard the workflow_id clause when it's falsy so we never match every
        # document whose workflow_id is null/empty.)
        run_clauses = [{"runner_id": runner_id}]
        if workflow_id:
            run_clauses.append({"workflow_id": workflow_id})
        run_filter = {"$or": run_clauses} if len(run_clauses) > 1 else run_clauses[0]

        steps = self._db.steps.delete_many({"workflow_id": workflow_id}).deleted_count if workflow_id else 0
        tasks = self._db.tasks.delete_many(run_filter).deleted_count
        step_logs = self._db.step_logs.delete_many(run_filter).deleted_count
        logs = self._db.logs.delete_many({"runner_id": runner_id}).deleted_count
        runners = self._db.runners.delete_one({"uuid": runner_id}).deleted_count
        return {
            "found": True,
            "runners": runners,
            "steps": steps,
            "tasks": tasks,
            "step_logs": step_logs,
            "logs": logs,
        }

    # =========================================================================
    # Serialization Helpers — Runners
    # =========================================================================

    def _runner_to_doc(self, runner: RunnerDefinition) -> dict:
        """Convert RunnerDefinition to MongoDB document."""
        return {
            "uuid": runner.uuid,
            "workflow_id": runner.workflow_id,
            "workflow": self._workflow_to_doc(runner.workflow),
            "parameters": [asdict(p) for p in runner.parameters],
            "step_id": runner.step_id,
            "user": asdict(runner.user) if runner.user else None,
            "author": asdict(runner.author) if runner.author else None,
            "purpose": runner.purpose,
            "teams": runner.teams,
            "start_time": runner.start_time,
            "end_time": runner.end_time,
            "duration": runner.duration,
            "retain": runner.retain,
            "state": runner.state,
            "compiled_ast": runner.compiled_ast,
            "workflow_ast": runner.workflow_ast,
        }

    def _doc_to_runner(self, doc: dict) -> RunnerDefinition:
        """Convert MongoDB document to RunnerDefinition."""
        from ..entities import UserDefinition

        workflow = self._doc_to_workflow(doc["workflow"])
        user = None
        if doc.get("user"):
            user = UserDefinition(**doc["user"])
        author = None
        if doc.get("author"):
            author = UserDefinition(**doc["author"])

        return RunnerDefinition(
            uuid=doc["uuid"],
            workflow_id=doc["workflow_id"],
            workflow=workflow,
            parameters=[Parameter(**p) for p in doc.get("parameters", [])],
            step_id=doc.get("step_id"),
            user=user,
            author=author,
            purpose=doc.get("purpose", "none"),
            teams=doc.get("teams", []),
            start_time=doc.get("start_time", 0),
            end_time=doc.get("end_time", 0),
            duration=doc.get("duration", 0),
            retain=doc.get("retain", 0),
            state=doc.get("state", "created"),
            compiled_ast=doc.get("compiled_ast"),
            workflow_ast=doc.get("workflow_ast"),
        )
