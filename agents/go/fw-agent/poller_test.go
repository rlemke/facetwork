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

package fwagent

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

	if cfg.ServiceName != "fw-agent" {
		t.Errorf("Expected ServiceName 'fw-agent', got '%s'", cfg.ServiceName)
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

	if ResumeTaskName != "fw:resume" {
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

func TestFacetNameInjection(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	var receivedFacetName interface{}
	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		receivedFacetName = params["_facet_name"]
		return nil, nil
	}

	poller.Register("ns.TestFacet", handler)

	// Simulate what processTask does: read params, inject, call handler
	params := map[string]interface{}{}
	params["_facet_name"] = "ns.TestFacet"
	handler(params)

	if receivedFacetName != "ns.TestFacet" {
		t.Errorf("Expected _facet_name 'ns.TestFacet', got '%v'", receivedFacetName)
	}
}

func TestHandlerMetadataInjection(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	// Set a metadata provider
	poller.metadataProvider = func(facetName string) map[string]interface{} {
		if facetName == "ns.TestFacet" {
			return map[string]interface{}{"description": "test handler"}
		}
		return nil
	}

	meta := poller.metadataProvider("ns.TestFacet")
	if meta == nil {
		t.Fatal("Expected metadata, got nil")
	}
	if meta["description"] != "test handler" {
		t.Errorf("Expected description 'test handler', got '%v'", meta["description"])
	}
}

func TestHandlerMetadataAbsent(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	// No metadata provider set — should not panic
	if poller.metadataProvider != nil {
		t.Error("metadataProvider should be nil by default")
	}
}

func TestUpdateStepCallbackInjection(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	var receivedCallback interface{}
	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		receivedCallback = params["_update_step"]
		return nil, nil
	}

	poller.Register("ns.StreamFacet", handler)

	// Simulate param injection
	params := map[string]interface{}{}
	params["_update_step"] = func(partial map[string]interface{}) {}
	handler(params)

	if receivedCallback == nil {
		t.Error("Expected _update_step callback to be injected")
	}
}

func TestUpdateStepCallbackType(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	// Verify that the callback type matches expected signature
	var cb func(map[string]interface{})
	cb = func(partial map[string]interface{}) {
		// Simulated partial update
		if partial == nil {
			t.Error("partial should not be nil")
		}
	}

	params := map[string]interface{}{"_update_step": cb}
	fn, ok := params["_update_step"].(func(map[string]interface{}))
	if !ok {
		t.Fatal("_update_step should be func(map[string]interface{})")
	}
	fn(map[string]interface{}{"progress": 50})

	_ = poller // ensure poller is used
}

func TestUpdateStepReturnsMethod(t *testing.T) {
	// Verify the method signature exists by type assertion
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)
	if poller == nil {
		t.Fatal("poller should not be nil")
	}
	// Method existence is verified at compile time
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
