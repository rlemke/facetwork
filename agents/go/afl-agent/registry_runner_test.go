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
	"time"
)

func TestRegistryRunnerEmptyTopics(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{"result": "ok"}, nil
	}

	poller.Register("ns.FacetA", handler)
	poller.Register("ns.FacetB", handler)
	poller.Register("ns.FacetC", handler)

	rr := NewRegistryRunner(poller)

	// No active topics — effective handlers should be empty
	effective := rr.effectiveHandlers()
	if len(effective) != 0 {
		t.Errorf("Expected 0 effective handlers with no active topics, got %d", len(effective))
	}
}

func TestRegistryRunnerIntersection(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{"result": "ok"}, nil
	}

	poller.Register("ns.FacetA", handler)
	poller.Register("ns.FacetB", handler)
	poller.Register("ns.FacetC", handler)

	rr := NewRegistryRunner(poller)

	// Simulate active topics: {FacetB, FacetD}
	rr.topicsMu.Lock()
	rr.activeTopics = map[string]bool{
		"ns.FacetB": true,
		"ns.FacetD": true,
	}
	rr.topicsMu.Unlock()

	effective := rr.effectiveHandlers()
	if len(effective) != 1 {
		t.Errorf("Expected 1 effective handler, got %d", len(effective))
	}
	if len(effective) > 0 && effective[0] != "ns.FacetB" {
		t.Errorf("Expected effective handler 'ns.FacetB', got '%s'", effective[0])
	}
}

func TestRegistryRunnerDefaultRefreshInterval(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)
	rr := NewRegistryRunner(poller)

	expected := 30 * time.Second
	if rr.RefreshInterval != expected {
		t.Errorf("Expected refresh interval %v, got %v", expected, rr.RefreshInterval)
	}
}

func TestRegistryRunnerTopicFilterIntegration(t *testing.T) {
	cfg := DefaultConfig()
	poller := NewAgentPoller(cfg)

	handler := func(params map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{}, nil
	}

	poller.Register("ns.FacetA", handler)
	poller.Register("ns.FacetB", handler)

	rr := NewRegistryRunner(poller)

	// EffectiveHandlers on the poller should use the topic filter
	pollerEffective := poller.EffectiveHandlers()
	if len(pollerEffective) != 0 {
		t.Errorf("Expected 0 effective handlers via poller topic filter, got %d", len(pollerEffective))
	}

	// Set active topics to include FacetA
	rr.topicsMu.Lock()
	rr.activeTopics = map[string]bool{"ns.FacetA": true}
	rr.topicsMu.Unlock()

	pollerEffective = poller.EffectiveHandlers()
	if len(pollerEffective) != 1 {
		t.Errorf("Expected 1 effective handler via poller topic filter, got %d", len(pollerEffective))
	}
}

func TestHandlerRegistrationsConstant(t *testing.T) {
	if CollectionHandlerRegistrations != "handler_registrations" {
		t.Errorf("CollectionHandlerRegistrations should be 'handler_registrations', got '%s'",
			CollectionHandlerRegistrations)
	}
}
