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
 * Protocol constants matching agents/protocol/constants.json
 */

// Collection names
export const CollectionSteps = "steps";
export const CollectionEvents = "events";
export const CollectionTasks = "tasks";
export const CollectionServers = "servers";
export const CollectionLocks = "locks";
export const CollectionLogs = "logs";
export const CollectionFlows = "flows";
export const CollectionWorkflows = "workflows";
export const CollectionRunners = "runners";
export const CollectionStepLogs = "step_logs";
export const CollectionHandlerRegistrations = "handler_registrations";

// Task states
export const TaskStatePending = "pending";
export const TaskStateRunning = "running";
export const TaskStateCompleted = "completed";
export const TaskStateFailed = "failed";
export const TaskStateIgnored = "ignored";
export const TaskStateCanceled = "canceled";

// Step states
export const StepStateEventTransmit = "state.facet.execution.EventTransmit";
export const StepStateCreated = "state.facet.initialization.Created";
export const StepStateStatementError = "state.facet.execution.StatementError";
export const StepStateCompleted = "state.facet.completion.Completed";

// Server states
export const ServerStateStartup = "startup";
export const ServerStateRunning = "running";
export const ServerStateShutdown = "shutdown";
export const ServerStateError = "error";

// Step log levels
export const StepLogLevelInfo = "info";
export const StepLogLevelWarning = "warning";
export const StepLogLevelError = "error";
export const StepLogLevelSuccess = "success";

// Step log sources
export const StepLogSourceFramework = "framework";
export const StepLogSourceHandler = "handler";

// Protocol task names
export const ResumeTaskName = "fw:resume";
export const ExecuteTaskName = "fw:execute";
