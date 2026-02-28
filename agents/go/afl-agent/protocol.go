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

// Package aflagent provides an AFL agent poller library for Go.
//
// This package implements the AFL agent protocol for building event handlers
// that process AFL workflow tasks. It mirrors the Python AgentPoller API.
package aflagent

// Collection names matching agents/protocol/constants.json
const (
	CollectionSteps     = "steps"
	CollectionEvents    = "events"
	CollectionTasks     = "tasks"
	CollectionServers   = "servers"
	CollectionLocks     = "locks"
	CollectionLogs      = "logs"
	CollectionFlows     = "flows"
	CollectionWorkflows = "workflows"
	CollectionRunners   = "runners"
	CollectionStepLogs              = "step_logs"
	CollectionHandlerRegistrations = "handler_registrations"
)

// Task states
const (
	TaskStatePending   = "pending"
	TaskStateRunning   = "running"
	TaskStateCompleted = "completed"
	TaskStateFailed    = "failed"
	TaskStateIgnored   = "ignored"
	TaskStateCanceled  = "canceled"
)

// Step states
const (
	StepStateEventTransmit  = "state.facet.execution.EventTransmit"
	StepStateCreated        = "state.facet.initialization.Created"
	StepStateStatementError = "state.facet.execution.StatementError"
	StepStateCompleted      = "state.facet.completion.Completed"
)

// Server states
const (
	ServerStateStartup  = "startup"
	ServerStateRunning  = "running"
	ServerStateShutdown = "shutdown"
	ServerStateError    = "error"
)

// Step log levels
const (
	StepLogLevelInfo    = "info"
	StepLogLevelWarning = "warning"
	StepLogLevelError   = "error"
	StepLogLevelSuccess = "success"
)

// Step log sources
const (
	StepLogSourceFramework = "framework"
	StepLogSourceHandler   = "handler"
)

// Protocol task names
const (
	ResumeTaskName  = "afl:resume"
	ExecuteTaskName = "afl:execute"
)
