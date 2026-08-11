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

"""D3 delegation: submit to Ray, park, resume on completion.

The D2 adapter (``handlers/ray_handlers.py``) blocks: one runner worker slot is
held for the entire external run, so three concurrent four-hour jobs saturate a
two-worker runner. D3 removes that — and needs **no new runtime concept**, which
is the central claim of external-engine-delegation.md §3. This is the polyglot
agent protocol (multi-language-handlers.md) with step 3 split in two:

    1. claim an event task                         ] submit phase
    2. read step params                            ]
    3a. submit to Ray, record the handle, park     ]
        ... minutes or hours, holding nothing ...
    3b. observe the job reach a terminal state     ] completion phase
    4. write return attributes onto the step       ]
    5. mark the task completed                     ]
    6. insert fw:resume                            ]

One process watches many jobs. Nothing is held between 3a and 3b but a dict
entry, so N concurrent external runs cost N dict entries rather than N worker
slots.

**Restart safety.** The watcher keeps no durable state of its own. On restart it
re-derives every external id from the step ids of the tasks it still owns, so a
job submitted by a previous incarnation is picked back up rather than
double-submitted — the same property that makes redelivery safe (§6).

Run it::

    FW_RAY_ADDRESS=http://localhost:8265 python watcher.py --task-list ray
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import Any

from facetwork.runtime.continuation import (
    CONTINUATION_TASK_LIST,  # noqa: F401  (documented sibling)
)
from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.mongo_store import MongoStore
from facetwork.runtime.types import generate_id

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "handlers"))

from ray_handlers import (  # noqa: E402
    NAMESPACE,
    _client,
    _external_id,
    _logs_tail,
    _stop_quietly,
    _submit_or_attach,
)

log = logging.getLogger("ray-watcher")

FACET = f"{NAMESPACE}.SubmitJob"
RESUME_TASK_NAME = "fw:resume"
POLL_S = float(os.environ.get("FW_RAY_WATCHER_POLL_S", "2"))
HEARTBEAT_EVERY_S = 30.0


class RayWatcher:
    """Claims SubmitJob tasks, submits to Ray, and completes them out of band."""

    def __init__(self, store: MongoStore, address: str, task_list: str):
        self.store = store
        self.address = address
        self.task_list = task_list
        self.server_id = generate_id()
        self.client = _client(address)
        # step_id -> {task, submission_id, deadline, last_heartbeat}
        self.parked: dict[str, dict[str, Any]] = {}
        self._stopping = False

    # -- phase 1: claim and submit --------------------------------------------

    def _claim_and_submit(self) -> None:
        task = self.store.claim_task(
            task_names=[FACET], task_list=self.task_list, server_id=self.server_id
        )
        if task is None:
            return
        step = self.store.get_step(task.step_id)
        if step is None:
            log.warning("claimed a task whose step is gone: %s", task.step_id)
            self._fail(task, "step no longer exists")
            return

        payload = self._params(step, task)
        try:
            submission_id = _submit_or_attach(
                self.client, _external_id(task.step_id), payload, self._log_fn(task)
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("submit failed for step %s", task.step_id)
            self._fail(task, f"Ray submit failed: {exc}")
            return

        timeout_min = int(payload.get("run_timeout_minutes") or 120)
        self.store.update_task_stage_budget(
            task.uuid, int(time.time() * 1000) + timeout_min * 60_000, stage_name="ray-job"
        )
        self.parked[task.step_id] = {
            "task": task,
            "submission_id": submission_id,
            "deadline": time.monotonic() + timeout_min * 60,
            "last_heartbeat": 0.0,
        }
        log.info("parked step=%s ray=%s (holding no worker slot)", task.step_id, submission_id)

    def _params(self, step: Any, task: Any) -> dict:
        """Step params + the payload keys the shared adapter code expects."""
        params = {k: v.value for k, v in step.attributes.params.items()}
        params.setdefault("_retry_count", getattr(task, "retry_count", 0) or 0)
        return params

    # -- phase 2: observe and complete ----------------------------------------

    def _poll_parked(self) -> None:
        from ray.job_submission import JobStatus

        for step_id, entry in list(self.parked.items()):
            task, submission_id = entry["task"], entry["submission_id"]

            reason = self.store.task_cancellation_reason(task.uuid, self.server_id)
            if reason:
                # §7.2 — the external run must not outlive the workflow.
                _stop_quietly(self.client, submission_id, self._log_fn(task))
                log.info("cancelled step=%s (%s) — stopped %s", step_id, reason, submission_id)
                self.parked.pop(step_id, None)
                continue

            if time.monotonic() > entry["deadline"]:
                _stop_quietly(self.client, submission_id, self._log_fn(task))
                self._fail(task, f"Ray job {submission_id} exceeded its run timeout")
                self.parked.pop(step_id, None)
                continue

            status = self.client.get_job_status(submission_id)
            if not status.is_terminal():
                now = time.monotonic()
                if now - entry["last_heartbeat"] > HEARTBEAT_EVERY_S:
                    entry["last_heartbeat"] = now
                    self.store.update_task_heartbeat(
                        task.uuid,
                        int(time.time() * 1000),
                        progress_message=f"ray {submission_id}: {status}",
                        expected_server_id=self.server_id,
                    )
                continue

            self.parked.pop(step_id, None)
            logs = _logs_tail(self.client, submission_id)
            if status == JobStatus.SUCCEEDED:
                self._complete(
                    task,
                    {
                        "submission_id": submission_id,
                        "status": str(status),
                        "logs_tail": logs,
                    },
                )
            else:
                self._fail(task, f"Ray job {submission_id} ended {status}: {logs[-400:]}")

    # -- protocol steps 4-6 ----------------------------------------------------

    def _complete(self, task: Any, returns: dict) -> None:
        """Write returns onto the step, complete the task, insert fw:resume."""
        step = self.store.get_step(task.step_id)
        if step is None:
            return
        for name, value in returns.items():
            step.set_attribute(name, value, is_return=True)
        self.store.save_step(step)

        task.state = TaskState.COMPLETED
        task.updated = int(time.time() * 1000)
        if not self.store.save_task_if_owned(task, self.server_id):
            # Reclaimed while we watched: the new owner finishes it, not us.
            log.warning("dropped completion for %s — task was reclaimed", task.uuid)
            return
        self._insert_resume(task)
        log.info("completed step=%s and inserted fw:resume", task.step_id)

    def _insert_resume(self, task: Any) -> None:
        now = int(time.time() * 1000)
        self.store.save_task(
            TaskDefinition(
                uuid=generate_id(),
                name=RESUME_TASK_NAME,
                runner_id=task.runner_id,
                workflow_id=task.workflow_id,
                flow_id=task.flow_id,
                step_id=task.step_id,
                state=TaskState.PENDING,
                created=now,
                updated=now,
                task_list_name=task.task_list_name,
                data={"step_id": task.step_id, "workflow_id": task.workflow_id},
            )
        )

    def _fail(self, task: Any, message: str) -> None:
        """Release for retry, or dead-letter once the budget is spent.

        Deliberately mirrors the runtime's shared contract rather than inventing
        one: a retry gets a fresh Ray id via _submit_or_attach, so it does not
        attach to the failed run.
        """
        task.retry_count += 1
        task.updated = int(time.time() * 1000)
        task.error = {"message": message}
        if task.max_retries > 0 and task.retry_count >= task.max_retries:
            task.state = TaskState.DEAD_LETTER
        else:
            task.state = TaskState.PENDING
            task.server_id = ""
        self.store.save_task(task)
        log.warning("step=%s failed: %s", task.step_id, message)

    # -- restart safety --------------------------------------------------------

    def reattach(self) -> None:
        """Re-adopt jobs a previous incarnation submitted.

        The watcher stores nothing durable: every external id is *derived* from
        the step id, so re-deriving is enough to find the job again.
        """
        for task in self.store.get_tasks_by_state(TaskState.RUNNING):
            if task.name != FACET or task.step_id in self.parked:
                continue
            submission_id = _external_id(task.step_id)
            try:
                status = self.client.get_job_status(submission_id)
            except Exception:  # noqa: BLE001 - unknown id: not ours
                continue
            if status.is_terminal():
                continue
            task.server_id = self.server_id
            self.store.save_task(task)
            self.parked[task.step_id] = {
                "task": task,
                "submission_id": submission_id,
                "deadline": time.monotonic() + 120 * 60,
                "last_heartbeat": 0.0,
            }
            log.info("re-attached to in-flight ray job %s", submission_id)

    # -- loop ------------------------------------------------------------------

    def _log_fn(self, task: Any):
        def _log(message: str, level: str = "info") -> None:
            log.info("[step=%s] %s", task.step_id, message)

        return _log

    def run(self) -> None:
        self.reattach()
        log.info("ray watcher up: facet=%s list=%s ray=%s", FACET, self.task_list, self.address)
        while not self._stopping:
            try:
                self._claim_and_submit()
                self._poll_parked()
            except Exception:  # noqa: BLE001 - a watcher must not die on one bad cycle
                log.exception("watcher cycle failed")
            time.sleep(POLL_S)

    def stop(self, *_a) -> None:
        self._stopping = True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Ray delegation watcher (D3)")
    ap.add_argument("--task-list", default="ray")
    ap.add_argument("--ray-address", default=os.environ.get("FW_RAY_ADDRESS", "http://localhost:8265"))
    ap.add_argument("--mongo", default=os.environ.get("FW_MONGODB_URL", "mongodb://localhost:27017"))
    ap.add_argument("--database", default=os.environ.get("FW_MONGODB_DATABASE", "facetwork"))
    args = ap.parse_args(argv)

    store = MongoStore(connection_string=args.mongo, database_name=args.database)
    watcher = RayWatcher(store, args.ray_address, args.task_list)
    signal.signal(signal.SIGINT, watcher.stop)
    signal.signal(signal.SIGTERM, watcher.stop)
    watcher.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
