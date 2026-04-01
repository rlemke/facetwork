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

import { Db } from "mongodb";
import { v4 as uuidv4 } from "uuid";
import {
  CollectionSteps,
  CollectionStepLogs,
  CollectionTasks,
  ResumeTaskName,
  StepStateEventTransmit,
  TaskStateCompleted,
  TaskStateFailed,
  TaskStatePending,
  TaskStateRunning,
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

  /**
   * Atomically claims a pending task for processing.
   * Returns null if no task is available.
   */
  async claimTask(
    taskNames: string[],
    taskList: string
  ): Promise<TaskDocument | null> {
    const collection = this.db.collection<TaskDocument>(CollectionTasks);

    const filter = {
      state: TaskStatePending,
      name: { $in: taskNames },
      task_list_name: taskList,
    };

    const update = {
      $set: {
        state: TaskStateRunning,
        updated: nowMillis(),
      },
    };

    const result = await collection.findOneAndUpdate(filter, update, {
      returnDocument: "after",
    });

    return result ?? null;
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
   * Marks a task as completed.
   */
  async markTaskCompleted(task: TaskDocument): Promise<void> {
    const collection = this.db.collection<TaskDocument>(CollectionTasks);

    await collection.updateOne(
      { uuid: task.uuid },
      {
        $set: {
          state: TaskStateCompleted,
          updated: nowMillis(),
        },
      }
    );
  }

  /**
   * Marks a task as failed with an error message.
   */
  async markTaskFailed(task: TaskDocument, errorMsg: string): Promise<void> {
    const collection = this.db.collection<TaskDocument>(CollectionTasks);

    await collection.updateOne(
      { uuid: task.uuid },
      {
        $set: {
          state: TaskStateFailed,
          updated: nowMillis(),
          error: { message: errorMsg },
        },
      }
    );
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
   * Inserts an afl:resume task for the Python RunnerService.
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
