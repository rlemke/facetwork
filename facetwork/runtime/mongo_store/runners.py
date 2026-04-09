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


class RunnerMixin:
    """Runner CRUD operations."""

    def get_runner(self, runner_id: str) -> RunnerDefinition | None:
        """Get a runner by ID."""
        doc = self._db.runners.find_one({"uuid": runner_id})
        return self._doc_to_runner(doc) if doc else None

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
        doc = self._runner_to_doc(runner)
        self._db.runners.replace_one({"uuid": runner.uuid}, doc, upsert=True)

    def update_runner_state(self, runner_id: str, state: str) -> None:
        """Update runner state."""
        self._db.runners.update_one({"uuid": runner_id}, {"$set": {"state": state}})

    def get_all_runners(self, limit: int = 100) -> Sequence[RunnerDefinition]:
        """Get all runners, most recent first."""
        docs = self._db.runners.find().sort("start_time", -1).limit(limit)
        return [self._doc_to_runner(doc) for doc in docs]

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

        return RunnerDefinition(
            uuid=doc["uuid"],
            workflow_id=doc["workflow_id"],
            workflow=workflow,
            parameters=[Parameter(**p) for p in doc.get("parameters", [])],
            step_id=doc.get("step_id"),
            user=user,
            start_time=doc.get("start_time", 0),
            end_time=doc.get("end_time", 0),
            duration=doc.get("duration", 0),
            retain=doc.get("retain", 0),
            state=doc.get("state", "created"),
            compiled_ast=doc.get("compiled_ast"),
            workflow_ast=doc.get("workflow_ast"),
        )
