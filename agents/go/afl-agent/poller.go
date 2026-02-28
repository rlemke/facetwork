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
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

// Handler is a callback function for processing events.
// It receives the step parameters and returns the result to write back.
type Handler func(params map[string]interface{}) (map[string]interface{}, error)

// AgentPoller polls for tasks and dispatches to registered handlers.
type AgentPoller struct {
	cfg      Config
	serverID string
	db       *mongo.Database
	client   *mongo.Client

	handlers map[string]Handler
	mu       sync.RWMutex

	ops          *MongoOps
	registration *ServerRegistration

	stopCh   chan struct{}
	wg       sync.WaitGroup
	sem      chan struct{} // semaphore for concurrency control
	running  bool
	runMu    sync.Mutex
}

// NewAgentPoller creates a new AgentPoller with the given configuration.
func NewAgentPoller(cfg Config) *AgentPoller {
	return &AgentPoller{
		cfg:      cfg,
		serverID: uuid.New().String(),
		handlers: make(map[string]Handler),
		stopCh:   make(chan struct{}),
		sem:      make(chan struct{}, cfg.MaxConcurrent),
	}
}

// Register registers a handler for a qualified facet name.
// The facet name can be either qualified (ns.FacetName) or short (FacetName).
func (p *AgentPoller) Register(facetName string, handler Handler) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.handlers[facetName] = handler
}

// RegisteredHandlers returns a list of registered handler names.
func (p *AgentPoller) RegisteredHandlers() []string {
	p.mu.RLock()
	defer p.mu.RUnlock()

	names := make([]string, 0, len(p.handlers))
	for name := range p.handlers {
		names = append(names, name)
	}
	return names
}

// Start connects to MongoDB and begins the poll loop.
// This method blocks until Stop is called.
func (p *AgentPoller) Start(ctx context.Context) error {
	p.runMu.Lock()
	if p.running {
		p.runMu.Unlock()
		return nil
	}
	p.running = true
	p.runMu.Unlock()

	// Connect to MongoDB
	clientOpts := options.Client().ApplyURI(p.cfg.MongoURL)
	client, err := mongo.Connect(ctx, clientOpts)
	if err != nil {
		return err
	}
	p.client = client
	p.db = client.Database(p.cfg.Database)
	p.ops = NewMongoOps(p.db)
	p.registration = NewServerRegistration(p.db)

	// Register server
	handlers := p.RegisteredHandlers()
	if err := p.registration.Register(ctx, p.serverID, p.cfg, handlers); err != nil {
		return err
	}

	// Start heartbeat goroutine
	p.wg.Add(1)
	go p.heartbeatLoop(ctx)

	// Run poll loop
	p.pollLoop(ctx)

	return nil
}

// Stop signals the poller to stop and waits for cleanup.
func (p *AgentPoller) Stop(ctx context.Context) error {
	p.runMu.Lock()
	if !p.running {
		p.runMu.Unlock()
		return nil
	}
	p.running = false
	p.runMu.Unlock()

	close(p.stopCh)
	p.wg.Wait()

	// Deregister server
	if p.registration != nil {
		if err := p.registration.Deregister(ctx, p.serverID); err != nil {
			log.Printf("Failed to deregister server: %v", err)
		}
	}

	// Disconnect from MongoDB
	if p.client != nil {
		if err := p.client.Disconnect(ctx); err != nil {
			return err
		}
	}

	return nil
}

// PollOnce performs a single poll cycle. Useful for testing.
func (p *AgentPoller) PollOnce(ctx context.Context) error {
	if p.db == nil {
		// Connect if not already connected
		clientOpts := options.Client().ApplyURI(p.cfg.MongoURL)
		client, err := mongo.Connect(ctx, clientOpts)
		if err != nil {
			return err
		}
		p.client = client
		p.db = client.Database(p.cfg.Database)
		p.ops = NewMongoOps(p.db)
		p.registration = NewServerRegistration(p.db)
	}

	handlers := p.RegisteredHandlers()
	task, err := p.ops.ClaimTask(ctx, handlers, p.cfg.TaskList)
	if err != nil {
		return err
	}
	if task == nil {
		return nil // No task available
	}

	// Process synchronously for PollOnce
	p.processTask(ctx, task)
	return nil
}

func (p *AgentPoller) pollLoop(ctx context.Context) {
	ticker := time.NewTicker(p.cfg.PollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-p.stopCh:
			return
		case <-ctx.Done():
			return
		case <-ticker.C:
			p.pollCycle(ctx)
		}
	}
}

func (p *AgentPoller) pollCycle(ctx context.Context) {
	handlers := p.RegisteredHandlers()
	if len(handlers) == 0 {
		return
	}

	// Try to claim a task
	task, err := p.ops.ClaimTask(ctx, handlers, p.cfg.TaskList)
	if err != nil {
		log.Printf("Error claiming task: %v", err)
		return
	}
	if task == nil {
		return // No task available
	}

	// Acquire semaphore slot
	select {
	case p.sem <- struct{}{}:
		// Got slot, process in goroutine
		p.wg.Add(1)
		go func() {
			defer p.wg.Done()
			defer func() { <-p.sem }()
			p.processTask(ctx, task)
		}()
	default:
		// All slots busy, skip this cycle
		// Task will be picked up next cycle or by another instance
		log.Printf("Max concurrency reached, skipping task %s", task.UUID)
	}
}

// emitStepLog writes a step log entry (best-effort).
func (p *AgentPoller) emitStepLog(ctx context.Context, stepID, workflowID, facetName, level, message string) {
	p.ops.InsertStepLog(ctx, stepID, workflowID, p.serverID, facetName,
		StepLogSourceFramework, level, message)
}

func (p *AgentPoller) processTask(ctx context.Context, task *TaskDocument) {
	// 1. Task claimed
	p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
		StepLogLevelInfo, fmt.Sprintf("Task claimed: %s", task.Name))

	// Find handler - try qualified name first, then short name
	handler := p.findHandler(task.Name)
	if handler == nil {
		// 2. No handler found
		errMsg := fmt.Sprintf("No handler registered for: %s", task.Name)
		p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
			StepLogLevelError, "Handler error: "+errMsg)
		log.Printf("No handler for task: %s", task.Name)
		if err := p.ops.MarkTaskFailed(ctx, task, "no handler registered"); err != nil {
			log.Printf("Failed to mark task as failed: %v", err)
		}
		return
	}

	// 3. Dispatching handler
	p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
		StepLogLevelInfo, fmt.Sprintf("Dispatching handler: %s", task.Name))

	dispatchStart := time.Now()

	// Read step parameters
	params, err := p.ops.ReadStepParams(ctx, task.StepID)
	if err != nil {
		log.Printf("Failed to read step params: %v", err)
		if err := p.ops.MarkTaskFailed(ctx, task, err.Error()); err != nil {
			log.Printf("Failed to mark task as failed: %v", err)
		}
		return
	}

	// Invoke handler
	result, err := handler(params)
	if err != nil {
		// 5. Handler error
		p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
			StepLogLevelError, fmt.Sprintf("Handler error: %v", err))
		log.Printf("Handler error for %s: %v", task.Name, err)
		if err := p.ops.MarkTaskFailed(ctx, task, err.Error()); err != nil {
			log.Printf("Failed to mark task as failed: %v", err)
		}
		return
	}

	// Write returns to step
	if result != nil {
		if err := p.ops.WriteStepReturns(ctx, task.StepID, result); err != nil {
			log.Printf("Failed to write step returns: %v", err)
			if err := p.ops.MarkTaskFailed(ctx, task, err.Error()); err != nil {
				log.Printf("Failed to mark task as failed: %v", err)
			}
			return
		}
	}

	// Insert resume task for Python RunnerService
	if err := p.ops.InsertResumeTask(ctx, task.StepID, task.WorkflowID, task.TaskListName); err != nil {
		log.Printf("Failed to insert resume task: %v", err)
		if err := p.ops.MarkTaskFailed(ctx, task, err.Error()); err != nil {
			log.Printf("Failed to mark task as failed: %v", err)
		}
		return
	}

	// Mark task completed
	if err := p.ops.MarkTaskCompleted(ctx, task); err != nil {
		log.Printf("Failed to mark task completed: %v", err)
	}

	// 4. Handler completed
	durationMs := time.Since(dispatchStart).Milliseconds()
	p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
		StepLogLevelSuccess, fmt.Sprintf("Handler completed: %s (%dms)", task.Name, durationMs))
}

func (p *AgentPoller) findHandler(taskName string) Handler {
	p.mu.RLock()
	defer p.mu.RUnlock()

	// Try exact match first
	if h, ok := p.handlers[taskName]; ok {
		return h
	}

	// Try short name fallback (ns.Facet -> Facet)
	if idx := strings.LastIndex(taskName, "."); idx >= 0 {
		shortName := taskName[idx+1:]
		if h, ok := p.handlers[shortName]; ok {
			return h
		}
	}

	return nil
}

func (p *AgentPoller) heartbeatLoop(ctx context.Context) {
	defer p.wg.Done()

	ticker := time.NewTicker(p.cfg.HeartbeatInterval)
	defer ticker.Stop()

	for {
		select {
		case <-p.stopCh:
			return
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := p.registration.Heartbeat(ctx, p.serverID); err != nil {
				log.Printf("Heartbeat error: %v", err)
			}
		}
	}
}
