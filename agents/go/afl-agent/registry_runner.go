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
	"context"
	"log"
	"sync"
	"time"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
)

// RegistryRunner wraps an AgentPoller and restricts polling to only those
// handler names that also appear in MongoDB's handler_registrations collection.
// This provides DB-driven topic filtering without requiring dynamic module loading.
type RegistryRunner struct {
	Poller            *AgentPoller
	RefreshInterval   time.Duration

	activeTopics map[string]bool
	topicsMu     sync.RWMutex
	stopCh       chan struct{}
}

// NewRegistryRunner creates a RegistryRunner wrapping the given poller.
// The default refresh interval is 30 seconds.
func NewRegistryRunner(poller *AgentPoller) *RegistryRunner {
	rr := &RegistryRunner{
		Poller:          poller,
		RefreshInterval: 30 * time.Second,
		activeTopics:    make(map[string]bool),
		stopCh:          make(chan struct{}),
	}

	// Set the topic filter on the poller
	poller.topicFilter = rr.effectiveHandlers

	return rr
}

// EffectiveHandlers returns the intersection of registered handlers and active topics.
func (rr *RegistryRunner) effectiveHandlers() []string {
	rr.topicsMu.RLock()
	defer rr.topicsMu.RUnlock()

	registered := rr.Poller.RegisteredHandlers()
	var result []string
	for _, name := range registered {
		if rr.activeTopics[name] {
			result = append(result, name)
		}
	}
	return result
}

// RefreshTopics reads handler_registrations from MongoDB and updates activeTopics.
func (rr *RegistryRunner) RefreshTopics(ctx context.Context, db *mongo.Database) {
	coll := db.Collection(CollectionHandlerRegistrations)
	cursor, err := coll.Find(ctx, bson.D{})
	if err != nil {
		log.Printf("RegistryRunner: failed to refresh topics: %v", err)
		return
	}
	defer cursor.Close(ctx)

	topics := make(map[string]bool)
	for cursor.Next(ctx) {
		var doc bson.M
		if err := cursor.Decode(&doc); err != nil {
			continue
		}
		if name, ok := doc["facet_name"].(string); ok {
			topics[name] = true
		}
	}

	rr.topicsMu.Lock()
	rr.activeTopics = topics
	rr.topicsMu.Unlock()

	log.Printf("RegistryRunner: refreshed %d active topics from DB", len(topics))
}

// Start connects to MongoDB, starts the topic refresh loop, and delegates to the poller.
func (rr *RegistryRunner) Start(ctx context.Context) error {
	return rr.Poller.Start(ctx)
}

// Stop signals the runner and poller to stop.
func (rr *RegistryRunner) Stop(ctx context.Context) error {
	close(rr.stopCh)
	return rr.Poller.Stop(ctx)
}
