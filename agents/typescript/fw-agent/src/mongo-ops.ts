// Copyright 2025 Ralph Lemke
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { Db, Document, Filter } from "mongodb";
import { v4 as uuidv4 } from "uuid";
import {
  CollectionSteps,
  CollectionStepLogs,
  CollectionTasks,
  DefaultMaxRetries,
  LeaseMs,
  ResumeTaskName,
  StepStateEventTransmit,
  TaskStateCanceled,
  TaskStateCompleted,
  TaskStateDeadLetter,
  TaskStateFailed,
  TaskStateIgnored,
  TaskStatePending,
  TaskStateRunning,
  retryBackoffMs,
} from "./protocol";
import {
  TaskDocument,
  StepDocument,
  StepAttribute,
  nowMillis,
  inferTypeHint,
} from "./models";

/**
 * MongoDB operations for the AFL agent protocol.
 */
export class MongoOps {
  constructor(private readonly db: Db) {}

  /** Reads one task by uuid, or null when it does not exist. */
  async getTask(taskId: string): Promise<TaskDocument | null> {
    return this.db.collection<TaskDocument>(CollectionTasks).findOne(
      { uuid: taskId } as Filter<TaskDocument>
    ) as Promise<TaskDocument | null>;
  }

  /**
   * Claims one task (protocol v1.2 `claim_task`, then optionally
   * `reclaim_expired_task`). Records `serverId` and a lease.
   *
   * Besides name and task list the filter skips tasks in their retry cooldown,
   * tasks that need a script environment or FFL feature (an agent runs no
   * evaluator, so it declines both), and tasks whose `requires` floor exceeds
   * `resources`. A dimension the task names that is not in `resources`
   * declines the claim.
   *
   * @param reclaimExpired also try to take over a running task whose lease
   *   expired; callers pace this (see `LeaseReclaimScanMs`)
   */
  async claimTask(
    taskNames: string[],
    taskLists: string | string[],
    serverId: string = "",
    resources: Record<string, number> = {},
    reclaimExpired: boolean = false
  ): Promise<TaskDocument | null> {
    if (taskNames.length === 0) {
      return null;
    }
    const collection = this.db.collection<TaskDocument>(CollectionTasks);
    const now = nowMillis();
    const set: Document = {
      state: TaskStateRunning,
      updated: now,
      server_id: serverId,
      lease_expires: now + LeaseMs,
    };
    const lists = Array.isArray(taskLists) ? taskLists : [taskLists];
    const listFilter = lists.length === 1 ? lists[0] : { $in: lists };

    const pending: Document = {
      state: TaskStatePending,
      task_list_name: listFilter,
      $and: [
        nameFilter(taskNames),
        {
          $or: [
            { next_retry_after: { $exists: false } },
            { next_retry_after: 0 },
            { next_retry_after: { $lte: now } },
          ],
        },
        noEnvironment(),
        {
          $or: [
            { required_features: { $exists: false } },
            { required_features: [] },
          ],
        },
        resourceFilter(resources),
      ],
    };
    const doc = await collection.findOneAndUpdate(
      pending as Filter<TaskDocument>,
      { $set: set },
      { returnDocument: "after" }
    );
    if (doc) {
      return doc;
    }
    if (!reclaimExpired) {
      return null;
    }

    // A running task whose holder stopped renewing its lease. It is a RETRY, so
    // retry_count is bumped, and only while budget remains: an exhausted one is
    // left for the runtime's reaper, which dead-letters it.
    const expired: Document = {
      state: TaskStateRunning,
      task_list_name: listFilter,
      lease_expires: { $lt: now, $gt: 0 },
      $and: [
        nameFilter(taskNames),
        noEnvironment(),
        {
          $or: [
            { max_retries: { $lte: 0 } },
            {
              $expr: {
                $lt: [
                  { $ifNull: ["$retry_count", 0] },
                  { $ifNull: ["$max_retries", DefaultMaxRetries] },
                ],
              },
            },
          ],
        },
      ],
    };
    const reclaimed = await collection.findOneAndUpdate(
      expired as Filter<TaskDocument>,
      { $set: set, $inc: { retry_count: 1 } },
      { returnDocument: "after" }
    );
    return reclaimed ?? null;
  }

  /**
   * Renews a running task's lease (protocol `renew_task_lease`).
   * Ownership-gated: returns false when the task is no longer this server's.
   */
  async renewLease(
    taskId: string,
    serverId: string,
    progressPct?: number,
    progressMessage?: string
  ): Promise<boolean> {
    const now = nowMillis();
    const set: Document = { task_heartbeat: now, lease_expires: now + LeaseMs };
    if (progressPct !== undefined && progressPct !== null) {
      set.progress_pct = Math.max(0, Math.min(100, progressPct));
    }
    if (progressMessage !== undefined && progressMessage !== null) {
      set.progress_message = progressMessage;
    }
    const r = await this.db.collection(CollectionTasks).updateOne(
      { uuid: taskId, state: TaskStateRunning, server_id: serverId },
      { $set: set }
    );
    return r.matchedCount > 0;
  }

  /**
   * Why an in-flight execution should stop, or null (protocol
   * `check_cancellation`): the task is gone, canceled, already terminal (e.g.
   * the runtime's timeout watchdog), or reclaimed by another server.
   */
  async cancellationReason(taskId: string, serverId: string): Promise<string | null> {
    const doc = await this.db
      .collection(CollectionTasks)
      .findOne({ uuid: taskId }, { projection: { state: 1, server_id: 1, _id: 0 } });
    if (!doc) {
      return "task record no longer exists";
    }
    const state = String(doc.state);
    if (state === TaskStateCanceled) {
      return "task was canceled (terminate-workflow)";
    }
    if ([TaskStateCompleted, TaskStateFailed, TaskStateDeadLetter, TaskStateIgnored].includes(state)) {
      return `task already terminal (${state})`;
    }
    if ((doc.server_id ?? "") !== serverId) {
      return "task was reclaimed by another runner";
    }
    return null;
  }

  /**
   * Reads the params attribute from a step.
   */
  async readStepParams(stepId: string): Promise<Record<string, unknown>> {
    const collection = this.db.collection<StepDocument>(CollectionSteps);

    const step = await collection.findOne({ uuid: stepId });
    if (!step) {
      throw new Error(`Step not found: ${stepId}`);
    }

    const result: Record<string, unknown> = {};
    const params = step.attributes?.params;
    if (params) {
      for (const [name, attr] of Object.entries(params)) {
        result[name] = attr.value;
      }
    }

    return result;
  }

  /**
   * Fetches the full snapshot of a referenced step.  Mirrors Python
   * `HandlerContext.fetch_step`: given a tagged JSON FacetRef
   * (`{_facet_ref: true, step_id, ...}`), return a snapshot dict
   * containing the upstream step's id, workflow_id, facet_name,
   * params, and returns.  The snapshot is read-only by contract.
   */
  async fetchStep(ref: Record<string, unknown>): Promise<Record<string, unknown>> {
    const stepId = typeof ref.step_id === 'string' ? ref.step_id : '';
    if (!stepId) {
      throw new Error("fetch_step: ref missing 'step_id'");
    }
    const collection = this.db.collection<StepDocument>(CollectionSteps);
    const step = await collection.findOne({ uuid: stepId });
    if (!step) {
      throw new Error(`Step not found: ${stepId}`);
    }
    const params: Record<string, unknown> = {};
    for (const [name, attr] of Object.entries(step.attributes?.params || {})) {
      params[name] = attr.value;
    }
    const returns: Record<string, unknown> = {};
    for (const [name, attr] of Object.entries(step.attributes?.returns || {})) {
      returns[name] = attr.value;
    }
    return {
      step_id: step.uuid,
      workflow_id: step.workflow_id,
      facet_name: step.facet_name,
      params,
      returns,
    };
  }

  /**
   * Writes return attributes to a step.
   */
  async writeStepReturns(
    stepId: string,
    returns: Record<string, unknown>
  ): Promise<void> {
    const collection = this.db.collection<StepDocument>(CollectionSteps);

    const setFields: Record<string, StepAttribute> = {};
    for (const [name, value] of Object.entries(returns)) {
      setFields[`attributes.returns.${name}`] = {
        name,
        value,
        type_hint: inferTypeHint(value),
      };
    }

    await collection.updateOne(
      {
        uuid: stepId,
        state: StepStateEventTransmit,
      },
      { $set: setFields }
    );
  }

  /**
   * Merges partial return attributes into a step.
   * Unlike writeStepReturns, this does NOT require the step to be in EVENT_TRANSMIT state,
   * allowing handlers to stream partial results during execution.
   */
  async updateStepReturns(
    stepId: string,
    partial: Record<string, unknown>
  ): Promise<void> {
    const collection = this.db.collection<StepDocument>(CollectionSteps);

    const setFields: Record<string, StepAttribute> = {};
    for (const [name, value] of Object.entries(partial)) {
      setFields[`attributes.returns.${name}`] = {
        name,
        value,
        type_hint: inferTypeHint(value),
      };
    }

    await collection.updateOne(
      { uuid: stepId },
      { $set: setFields }
    );
  }

  /**
   * Marks a task completed (protocol `complete_task`), only while this server
   * still holds it. Returns false when the task was reclaimed or terminated:
   * another execution owns the outcome, so do NOT insert a resume.
   */
  async completeTask(task: TaskDocument, serverId: string): Promise<boolean> {
    const r = await this.db.collection(CollectionTasks).updateOne(
      { uuid: task.uuid, state: TaskStateRunning, server_id: serverId },
      { $set: { state: TaskStateCompleted, updated: nowMillis() } }
    );
    return r.matchedCount > 0;
  }

  /**
   * Records a handler failure (protocol `fail_task`), only while this server
   * still holds the task. Returns the transition applied, or null if the task
   * was no longer this server's.
   *
   * Never writes `failed`: the runtime's stuck-step sweep sees no active and no
   * dead-lettered task for the step and creates a fresh one at retry_count 0,
   * so a failure would retry forever. A dead-lettered task makes the sweep fail
   * the step instead.
   */
  async failTask(
    task: TaskDocument,
    serverId: string,
    errorMsg: string,
    permanent: boolean
  ): Promise<FailTransition | null> {
    const now = nowMillis();
    const t = failTransition(
      task.retry_count ?? 0,
      task.max_retries ?? DefaultMaxRetries,
      permanent,
      now
    );
    const error: Document = { message: errorMsg ?? "" };
    const set: Document = { state: t.state, updated: now, retry_count: t.retryCount };
    if (t.state === TaskStateDeadLetter) {
      if (permanent) {
        error.permanent = true;
      }
    } else {
      set.server_id = "";
      set.next_retry_after = t.nextRetryAfter;
    }
    set.error = error;
    const r = await this.db.collection(CollectionTasks).updateOne(
      { uuid: task.uuid, state: TaskStateRunning, server_id: serverId },
      { $set: set }
    );
    return r.matchedCount > 0 ? t : null;
  }

  /**
   * Inserts a step log entry for dashboard observability.
   * Best-effort: errors are caught and logged.
   */
  async insertStepLog(
    stepId: string,
    workflowId: string,
    runnerId: string,
    facetName: string,
    source: string,
    level: string,
    message: string
  ): Promise<void> {
    try {
      const collection = this.db.collection(CollectionStepLogs);

      await collection.insertOne({
        uuid: uuidv4(),
        step_id: stepId,
        workflow_id: workflowId,
        runner_id: runnerId,
        facet_name: facetName,
        source,
        level,
        message,
        details: {},
        time: nowMillis(),
      });
    } catch (err) {
      console.debug(`Could not save step log for step ${stepId}:`, err);
    }
  }

  /**
   * Inserts an fw:resume task for the Python RunnerService.
   */
  async insertResumeTask(
    stepId: string,
    workflowId: string,
    taskList: string,
    facetName: string = ""
  ): Promise<void> {
    const collection = this.db.collection<TaskDocument>(CollectionTasks);

    const now = nowMillis();
    const resumeName = facetName ? `${ResumeTaskName}:${facetName}` : ResumeTaskName;
    const task: TaskDocument = {
      uuid: uuidv4(),
      name: resumeName,
      runner_id: "",
      workflow_id: workflowId,
      flow_id: "",
      step_id: stepId,
      state: TaskStatePending,
      created: now,
      updated: now,
      task_list_name: taskList,
      data_type: "resume",
      data: {
        step_id: stepId,
        workflow_id: workflowId,
      },
    };

    await collection.insertOne(task);
  }
}

/** The outcome of `failTransition`. */
export interface FailTransition {
  state: string;
  retryCount: number;
  nextRetryAfter: number;
}

/**
 * The protocol's failure transition (conformance/retry_cases.json). A
 * permanent error dead-letters without spending budget; otherwise the count
 * goes up and the task is either dead-lettered (budget spent) or returned to
 * pending after an exponential backoff.
 */
export function failTransition(
  retryCount: number,
  maxRetries: number,
  permanent: boolean,
  now: number
): FailTransition {
  if (permanent) {
    return { state: TaskStateDeadLetter, retryCount, nextRetryAfter: 0 };
  }
  const next = retryCount + 1;
  if (maxRetries > 0 && next >= maxRetries) {
    return { state: TaskStateDeadLetter, retryCount: next, nextRetryAfter: 0 };
  }
  return { state: TaskStatePending, retryCount: next, nextRetryAfter: now + retryBackoffMs(next) };
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^\${}()|[\]\\]/g, "\\$&");
}

/** Exact names, or `name:` prefixes (the prefix is a literal, so escaped). */
function nameFilter(taskNames: string[]): Document {
  const or: Document[] = [{ name: { $in: taskNames } }];
  for (const n of taskNames) {
    or.push({ name: { $regex: `^${escapeRegex(n)}:` } });
  }
  return { $or: or };
}

function noEnvironment(): Document {
  return {
    $or: [{ environment_hash: { $exists: false } }, { environment_hash: "" }],
  };
}

function resourceFilter(resources: Record<string, number>): Document {
  const or: Document[] = [{ requires: { $exists: false } }, { requires: {} }];
  const have = Object.entries(resources ?? {}).filter(([, v]) => typeof v === "number");
  if (have.length > 0) {
    const perDim: Document[] = [
      { requires_dims: { $not: { $elemMatch: { $nin: have.map(([k]) => k) } } } },
    ];
    for (const [dim, cap] of have) {
      perDim.push({
        $or: [{ [`requires.${dim}`]: { $exists: false } }, { [`requires.${dim}`]: { $lte: cap } }],
      });
    }
    or.push({ $and: perDim });
  }
  return { $or: or };
}
