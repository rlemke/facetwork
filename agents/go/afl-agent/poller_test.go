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

package aflagent

import (
	"testing"
)

func TestNewAgentPoller(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	if poller == nil {
		t.Fatal("NewAgentPoller returned nil")
	}

	if poller.serverID == "" {
		t.Error("serverID should not be empty")
	}

	if poller.handlers == nil {
		t.Error("handlers map should be initialized")
	}
}

func TestRegisterHandler(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{"result": "ok"}, nil
	}

	poller.Register("ns.TestFacet", handler)

	handlers := poller.RegisteredHandlers()
	if len(handlers) != 1 {
		t.Errorf("Expected 1 handler, got %d", len(handlers))
	}

	if handlers[0] != "ns.TestFacet" {
		t.Errorf("Expected handler name 'ns.TestFacet', got '%s'", handlers[0])
	}
}

func TestFindHandler(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{"result": "ok"}, nil
	}

	// Register with short name
	poller.Register("TestFacet", handler)

	// Test exact match
	h := poller.findHandler("TestFacet")
	if h == nil {
		t.Error("Should find handler by exact match")
	}

	// Test short name fallback
	h = poller.findHandler("ns.TestFacet")
	if h == nil {
		t.Error("Should find handler by short name fallback")
	}

	// Test not found
	h = poller.findHandler("ns.Unknown")
	if h != nil {
		t.Error("Should return nil for unknown handler")
	}
}

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()

	if cfg.ServiceName != "afl-agent" {
		t.Errorf("Expected ServiceName 'afl-agent', got '%s'", cfg.ServiceName)
	}

	if cfg.ServerGroup != "default" {
		t.Errorf("Expected ServerGroup 'default', got '%s'", cfg.ServerGroup)
	}

	if cfg.TaskList != "default" {
		t.Errorf("Expected TaskList 'default', got '%s'", cfg.TaskList)
	}

	if cfg.MaxConcurrent != 5 {
		t.Errorf("Expected MaxConcurrent 5, got %d", cfg.MaxConcurrent)
	}

	if cfg.MongoURL != "mongodb://localhost:27017" {
		t.Errorf("Expected MongoURL 'mongodb://localhost:27017', got '%s'", cfg.MongoURL)
	}

	if cfg.Database != "afl" {
		t.Errorf("Expected Database 'afl', got '%s'", cfg.Database)
	}
}

func TestProtocolConstants(t *testing.T) {
	// Verify constants match protocol
	if CollectionTasks != "tasks" {
		t.Errorf("CollectionTasks should be 'tasks', got '%s'", CollectionTasks)
	}

	if CollectionSteps != "steps" {
		t.Errorf("CollectionSteps should be 'steps', got '%s'", CollectionSteps)
	}

	if CollectionServers != "servers" {
		t.Errorf("CollectionServers should be 'servers', got '%s'", CollectionServers)
	}

	if TaskStatePending != "pending" {
		t.Errorf("TaskStatePending should be 'pending', got '%s'", TaskStatePending)
	}

	if TaskStateRunning != "running" {
		t.Errorf("TaskStateRunning should be 'running', got '%s'", TaskStateRunning)
	}

	if TaskStateCompleted != "completed" {
		t.Errorf("TaskStateCompleted should be 'completed', got '%s'", TaskStateCompleted)
	}

	if ResumeTaskName != "afl:resume" {
		t.Errorf("ResumeTaskName should be 'afl:resume', got '%s'", ResumeTaskName)
	}
}

func TestStepLogConstants(t *testing.T) {
	// Step log levels
	if StepLogLevelInfo != "info" {
		t.Errorf("StepLogLevelInfo should be 'info', got '%s'", StepLogLevelInfo)
	}
	if StepLogLevelWarning != "warning" {
		t.Errorf("StepLogLevelWarning should be 'warning', got '%s'", StepLogLevelWarning)
	}
	if StepLogLevelError != "error" {
		t.Errorf("StepLogLevelError should be 'error', got '%s'", StepLogLevelError)
	}
	if StepLogLevelSuccess != "success" {
		t.Errorf("StepLogLevelSuccess should be 'success', got '%s'", StepLogLevelSuccess)
	}

	// Step log sources
	if StepLogSourceFramework != "framework" {
		t.Errorf("StepLogSourceFramework should be 'framework', got '%s'", StepLogSourceFramework)
	}
	if StepLogSourceHandler != "handler" {
		t.Errorf("StepLogSourceHandler should be 'handler', got '%s'", StepLogSourceHandler)
	}

	// Step logs collection
	if CollectionStepLogs != "step_logs" {
		t.Errorf("CollectionStepLogs should be 'step_logs', got '%s'", CollectionStepLogs)
	}

	// Handler registrations collection
	if CollectionHandlerRegistrations != "handler_registrations" {
		t.Errorf("CollectionHandlerRegistrations should be 'handler_registrations', got '%s'",
			CollectionHandlerRegistrations)
	}
}

func TestInferTypeHint(t *testing.T) {
	tests := []struct {
		value    interface{}
		expected string
	}{
		{true, "Boolean"},
		{false, "Boolean"},
		{42, "Long"},
		{int32(42), "Long"},
		{int64(42), "Long"},
		{3.14, "Double"},
		{float32(3.14), "Double"},
		{"hello", "String"},
		{[]interface{}{1, 2, 3}, "List"},
		{map[string]interface{}{"a": 1}, "Map"},
		{struct{}{}, "Any"},
	}

	for _, tt := range tests {
		result := inferTypeHint(tt.value)
		if result != tt.expected {
			t.Errorf("inferTypeHint(%v) = %s, expected %s", tt.value, result, tt.expected)
		}
	}
}
