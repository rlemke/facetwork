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

import { MongoClient, Db } from "mongodb";
import { v4 as uuidv4 } from "uuid";
import { AgentPollerConfig } from "./config";
import { MongoOps } from "./mongo-ops";
import { ServerRegistration } from "./server-registration";
import { TaskDocument } from "./models";
import { LeaseReclaimScanMs, TaskStateDeadLetter } from "./protocol";
import { claimResources } from "./resources";
import { PermanentError } from "./errors";

/**
 * Handler callback type for processing events.
 */
export type Handler = (
  params: Record<string, unknown>
) => Promise<Record<string, unknown>>;

/**
 * AgentPoller polls for tasks and dispatches to registered handlers.
 */
export class AgentPoller {
  private readonly config: AgentPollerConfig;
  private readonly serverId: string;
  private readonly handlers: Map<string, Handler> = new Map();

  private client: MongoClient | null = null;
  private db: Db | null = null;
  private ops: MongoOps | null = null;
  private registration: ServerRegistration | null = null;

  public metadataProvider: ((facetName: string) => Record<string, unknown> | undefined) | null = null;

  private running = false;
  private pollInterval: NodeJS.Timeout | null = null;
  private heartbeatInterval: NodeJS.Timeout | null = null;
  private activeCount = 0;
  // Tasks this server is running, for lease renewal; value = still owned.
  private readonly activeTasks: Map<string, { owned: boolean }> = new Map();
  private lastReclaimScanMs = 0;

  constructor(config: AgentPollerConfig) {
    this.config = config;
    this.serverId = uuidv4();
  }

  /**
   * Registers a handler for a qualified facet name.
   */
  register(facetName: string, handler: Handler): void {
    this.handlers.set(facetName, handler);
  }

  /**
   * Returns a list of registered handler names.
   */
  registeredHandlers(): string[] {
    return Array.from(this.handlers.keys());
  }

  /**
   * Connects to MongoDB and begins the poll loop.
   */
  async start(): Promise<void> {
    if (this.running) {
      return;
    }
    this.running = true;

    // Connect to MongoDB
    this.client = new MongoClient(this.config.mongoUrl);
    await this.client.connect();
    this.db = this.client.db(this.config.database);
    this.ops = new MongoOps(this.db);
    this.registration = new ServerRegistration(this.db);

    // Register server
    const handlers = this.registeredHandlers();
    await this.registration.register(this.serverId, this.config, handlers);

    // Start heartbeat
    this.heartbeatInterval = setInterval(() => {
      this.heartbeat().catch((err) => console.error("Heartbeat error:", err));
    }, this.config.heartbeatIntervalMs);

    // Start poll loop
    this.pollInterval = setInterval(() => {
      this.pollCycle().catch((err) => console.error("Poll cycle error:", err));
    }, this.config.pollIntervalMs);
  }

  /**
   * Stops the poller and cleans up.
   */
  async stop(): Promise<void> {
    if (!this.running) {
      return;
    }
    this.running = false;

    // Stop intervals
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }

    // Wait for active tasks to complete
    while (this.activeCount > 0) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }

    // Deregister server
    if (this.registration) {
      try {
        await this.registration.deregister(this.serverId);
      } catch (err) {
        console.error("Failed to deregister server:", err);
      }
    }

    // Disconnect from MongoDB
    if (this.client) {
      await this.client.close();
      this.client = null;
      this.db = null;
      this.ops = null;
      this.registration = null;
    }
  }

  /**
   * Performs a single poll cycle. Useful for testing.
   */
  async pollOnce(): Promise<void> {
    if (!this.db) {
      // Connect if not already connected
      this.client = new MongoClient(this.config.mongoUrl);
      await this.client.connect();
      this.db = this.client.db(this.config.database);
      this.ops = new MongoOps(this.db);
      this.registration = new ServerRegistration(this.db);
    }

    const handlers = this.registeredHandlers();
    if (handlers.length === 0) {
      return;
    }

    const task = await this.claimNext(handlers);
    if (task) {
      await this.processTask(task);
    }
  }

  private async pollCycle(): Promise<void> {
    const handlers = this.registeredHandlers();
    if (handlers.length === 0) {
      return;
    }

    // Check concurrency limit
    if (this.activeCount >= this.config.maxConcurrent) {
      return;
    }

    // Try to claim a task
    const task = await this.claimNext(handlers);
    if (!task) {
      return;
    }

    // Process task asynchronously
    this.activeCount++;
    this.processTask(task)
      .catch((err) => console.error("Task processing error:", err))
      .finally(() => {
        this.activeCount--;
      });
  }

  /**
   * One claim (protocol v1.2): records this server and a lease, offers the
   * memory free now, and every `LeaseReclaimScanMs` also looks for a running
   * task whose lease expired.
   */
  private async claimNext(handlers: string[]): Promise<TaskDocument | null> {
    const now = Date.now();
    const reclaim = now - this.lastReclaimScanMs >= LeaseReclaimScanMs;
    const task = await this.ops!.claimTask(
      handlers, [this.config.taskList], this.serverId, claimResources(), reclaim
    );
    if (!task && reclaim) {
      this.lastReclaimScanMs = now;
    }
    return task;
  }

  /** Server heartbeat (re-registering a pruned record) + lease renewal. */
  private async heartbeat(): Promise<void> {
    if (this.registration && !(await this.registration.heartbeat(this.serverId)) && this.running) {
      console.warn(`Server record ${this.serverId} missing or marked shutdown -- registering again`);
      await this.registration.register(this.serverId, this.config, this.registeredHandlers());
    }
    for (const [taskId, state] of this.activeTasks) {
      try {
        if (!(await this.ops!.renewLease(taskId, this.serverId))) {
          state.owned = false;
        }
      } catch {
        // best-effort; retried next heartbeat
      }
    }
  }

  private async emitStepLog(
    stepId: string,
    workflowId: string,
    facetName: string,
    level: string,
    message: string
  ): Promise<void> {
    try {
      await this.ops!.insertStepLog(
        stepId, workflowId, this.serverId, facetName,
        "framework", level, message
      );
    } catch {
      // best-effort
    }
  }

  private async processTask(task: TaskDocument): Promise<void> {
    // 1. Task claimed
    await this.emitStepLog(task.step_id, task.workflow_id, task.name,
      "info", `Task claimed: ${task.name}`);

    // Find handler
    const handler = this.findHandler(task.name);
    if (!handler) {
      // 2. No handler found
      await this.emitStepLog(task.step_id, task.workflow_id, task.name,
        "error", `Handler error: No handler registered for: ${task.name}`);
      console.log(`No handler for task: ${task.name}`);
      await this.ops!.failTask(task, this.serverId, "no handler registered", false);
      return;
    }

    const state = { owned: true };
    this.activeTasks.set(task.uuid, state);
    try {
      // 3. Dispatching handler
      await this.emitStepLog(task.step_id, task.workflow_id, task.name,
        "info", `Dispatching handler: ${task.name}`);

      const dispatchStart = Date.now();

      // Read step parameters
      const params = await this.ops!.readStepParams(task.step_id);

      // Inject handler-level step_log callback
      params["_step_log"] = async (message: string, level = "info") => {
        try {
          await this.ops!.insertStepLog(task.step_id, task.workflow_id,
            this.serverId, task.name, "handler", level, message);
        } catch { /* best-effort */ }
      };

      // Inject _facet_name
      params["_facet_name"] = task.name;

      // Inject _update_step callback for streaming partial results
      params["_update_step"] = async (partial: Record<string, unknown>) => {
        try {
          await this.ops!.updateStepReturns(task.step_id, partial);
        } catch { /* best-effort */ }
      };

      // Inject _task_heartbeat(progressPct?, progressMessage?): renews the lease
      // now (the heartbeat timer also renews it every interval).
      params["_task_heartbeat"] = async (progressPct?: number, progressMessage?: string) => {
        try {
          if (!(await this.ops!.renewLease(task.uuid, this.serverId, progressPct, progressMessage))) {
            state.owned = false;
          }
        } catch { /* best-effort */ }
      };

      // Inject _is_cancelled(): true once the task was canceled, finished by
      // someone else, or reclaimed. A long handler should check it at batch
      // boundaries and return early.
      params["_is_cancelled"] = async (): Promise<boolean> => {
        if (!state.owned) {
          return true;
        }
        try {
          return (await this.ops!.cancellationReason(task.uuid, this.serverId)) !== null;
        } catch {
          return false;
        }
      };

      // Inject _handler_metadata if provider is available
      if (this.metadataProvider) {
        const meta = this.metadataProvider(task.name);
        if (meta) {
          params["_handler_metadata"] = meta;
        }
      }

      // Invoke handler
      const result = await handler(params);

      const durationMs = Date.now() - dispatchStart;

      // Another execution may own this task now (reclaimed after a lost lease,
      // canceled, or finished by the timeout watchdog): write nothing.
      const stop = await this.ops!.cancellationReason(task.uuid, this.serverId);
      if (stop !== null) {
        await this.emitStepLog(task.step_id, task.workflow_id, task.name,
          "warning", `Handler result discarded: ${stop}`);
        console.warn(`Discarding result of ${task.name} ${task.uuid}: ${stop}`);
        return;
      }

      // Write returns to step
      if (result && Object.keys(result).length > 0) {
        await this.ops!.writeStepReturns(task.step_id, result);
      }

      // Complete (ownership-gated), then signal the Python RunnerService.
      if (!(await this.ops!.completeTask(task, this.serverId))) {
        await this.emitStepLog(task.step_id, task.workflow_id, task.name,
          "warning", "Handler result discarded: task was reclaimed before completion");
        return;
      }
      await this.ops!.insertResumeTask(
        task.step_id,
        task.workflow_id,
        task.task_list_name,
        task.name
      );

      // 4. Handler completed
      await this.emitStepLog(task.step_id, task.workflow_id, task.name,
        "success", `Handler completed: ${task.name} (${durationMs}ms)`);
    } catch (err) {
      // 5. Handler error: retry with backoff, or dead-letter (never 'failed').
      const errorMsg = err instanceof Error ? err.message : String(err);
      const permanent = err instanceof PermanentError;
      const t = await this.ops!.failTask(task, this.serverId, errorMsg, permanent);
      let what: string;
      if (t === null) {
        what = "not recorded: task no longer held by this server";
      } else if (t.state === TaskStateDeadLetter) {
        what = permanent ? "dead-lettered (permanent)" : `dead-lettered after ${t.retryCount} attempts`;
      } else {
        what = `retry ${t.retryCount}/${task.max_retries ?? 5} in ${Math.round((t.nextRetryAfter - Date.now()) / 1000)}s`;
      }
      await this.emitStepLog(task.step_id, task.workflow_id, task.name,
        "error", `Handler error (${what}): ${errorMsg}`);
      console.error(`Handler error for ${task.name} (${what}):`, err);
    } finally {
      this.activeTasks.delete(task.uuid);
    }
  }

  private findHandler(taskName: string): Handler | undefined {
    // Try exact match first
    const exact = this.handlers.get(taskName);
    if (exact) {
      return exact;
    }

    // Try short name fallback (ns.Facet -> Facet)
    const lastDot = taskName.lastIndexOf(".");
    if (lastDot >= 0) {
      const shortName = taskName.substring(lastDot + 1);
      return this.handlers.get(shortName);
    }

    return undefined;
  }
}
