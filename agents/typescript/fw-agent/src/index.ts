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

/**
 * AFL Agent Library for TypeScript/Node.js
 *
 * @example
 * ```typescript
 * import { AgentPoller, resolveConfig, Handler } from "@afl/agent";
 *
 * const config = resolveConfig();
 * const poller = new AgentPoller(config);
 *
 * const myHandler: Handler = async (params) => {
 *   return { result: params.input + " processed" };
 * };
 *
 * poller.register("ns.MyFacet", myHandler);
 * await poller.start();
 * ```
 */

// Protocol constants
export {
  CollectionSteps,
  CollectionEvents,
  CollectionTasks,
  CollectionServers,
  CollectionLocks,
  CollectionLogs,
  CollectionFlows,
  CollectionWorkflows,
  CollectionRunners,
  CollectionStepLogs,
  CollectionHandlerRegistrations,
  TaskStatePending,
  TaskStateRunning,
  TaskStateCompleted,
  TaskStateFailed,
  TaskStateIgnored,
  TaskStateCanceled,
  TaskStateDeadLetter,
  StepStateEventTransmit,
  StepStateCreated,
  StepStateStatementError,
  StepStateCompleted,
  ServerStateStartup,
  ServerStateRunning,
  ServerStateShutdown,
  ServerStateError,
  ServerStateQuarantine,
  StepLogLevelInfo,
  StepLogLevelWarning,
  StepLogLevelError,
  StepLogLevelSuccess,
  StepLogSourceFramework,
  StepLogSourceHandler,
  ResumeTaskName,
  ExecuteTaskName,
  ProtocolVersion,
  LeaseMs,
  LeaseReclaimScanMs,
  DefaultMaxRetries,
  ClaimProxyWaitS,
  ClaimProxyRetryMs,
  retryBackoffMs,
} from "./protocol";

// Configuration
export {
  AgentPollerConfig,
  defaultConfig,
  loadConfig,
  resolveConfig,
  fromEnvironment,
} from "./config";

// Models
export {
  TaskDocument,
  StepAttribute,
  StepAttributes,
  StepDocument,
  ServerDocument,
  HandledStat,
  nowMillis,
  inferTypeHint,
} from "./models";

// MongoDB operations
export { MongoOps, FailTransition, failTransition } from "./mongo-ops";

// Measured capacity (resource-aware claim routing)
export { capacity, availableMemoryGb, claimResources, liveFree } from "./resources";

// Errors
export { PermanentError } from "./errors";

// Server registration
export { ServerRegistration } from "./server-registration";

// Poller
export { AgentPoller, Handler } from "./poller";

// Per-host claimer client
export { ClaimProxy, ClaimProxyEnv } from "./claim-proxy";

// RegistryRunner
export { RegistryRunner } from "./registry-runner";
