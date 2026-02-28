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

package afl.agent;

/**
 * Protocol constants matching agents/protocol/constants.json
 */
public final class Protocol {

    private Protocol() {
        // Prevent instantiation
    }

    // Collection names
    public static final String COLLECTION_STEPS = "steps";
    public static final String COLLECTION_EVENTS = "events";
    public static final String COLLECTION_TASKS = "tasks";
    public static final String COLLECTION_SERVERS = "servers";
    public static final String COLLECTION_LOCKS = "locks";
    public static final String COLLECTION_LOGS = "logs";
    public static final String COLLECTION_FLOWS = "flows";
    public static final String COLLECTION_WORKFLOWS = "workflows";
    public static final String COLLECTION_RUNNERS = "runners";
    public static final String COLLECTION_STEP_LOGS = "step_logs";
    public static final String COLLECTION_HANDLER_REGISTRATIONS = "handler_registrations";

    // Task states
    public static final String TASK_STATE_PENDING = "pending";
    public static final String TASK_STATE_RUNNING = "running";
    public static final String TASK_STATE_COMPLETED = "completed";
    public static final String TASK_STATE_FAILED = "failed";
    public static final String TASK_STATE_IGNORED = "ignored";
    public static final String TASK_STATE_CANCELED = "canceled";

    // Step states
    public static final String STEP_STATE_EVENT_TRANSMIT = "state.facet.execution.EventTransmit";
    public static final String STEP_STATE_CREATED = "state.facet.initialization.Created";
    public static final String STEP_STATE_STATEMENT_ERROR = "state.facet.execution.StatementError";
    public static final String STEP_STATE_COMPLETED = "state.facet.completion.Completed";

    // Server states
    public static final String SERVER_STATE_STARTUP = "startup";
    public static final String SERVER_STATE_RUNNING = "running";
    public static final String SERVER_STATE_SHUTDOWN = "shutdown";
    public static final String SERVER_STATE_ERROR = "error";

    // Step log levels
    public static final String STEP_LOG_LEVEL_INFO = "info";
    public static final String STEP_LOG_LEVEL_WARNING = "warning";
    public static final String STEP_LOG_LEVEL_ERROR = "error";
    public static final String STEP_LOG_LEVEL_SUCCESS = "success";

    // Step log sources
    public static final String STEP_LOG_SOURCE_FRAMEWORK = "framework";
    public static final String STEP_LOG_SOURCE_HANDLER = "handler";

    // Protocol task names
    public static final String RESUME_TASK_NAME = "afl:resume";
    public static final String EXECUTE_TASK_NAME = "afl:execute";
}
