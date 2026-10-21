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
	"context"
	"fmt"
	"log"
	"strings"
	"sync"
	"sync/atomic"
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

	stopCh  chan struct{}
	wg      sync.WaitGroup
	sem     chan struct{} // semaphore for concurrency control
	running bool
	runMu   sync.Mutex

	// topicFilter, if set, overrides RegisteredHandlers() for poll cycles.
	// Used by RegistryRunner to restrict to DB-registered topics.
	topicFilter func() []string

	// metadataProvider, if set, returns handler metadata for a given facet name.
	// Used by RegistryRunner to inject _handler_metadata into handler params.
	metadataProvider func(facetName string) map[string]interface{}

	// active maps the uuid of each task running here to an int32 "still owned"
	// flag (1/0), for lease renewal and _is_cancelled.
	active sync.Map
	// lastReclaimScan is when an expired-lease scan last came back empty (ms).
	lastReclaimScan int64

	// claimProxy is the host's claimer (FW_CLAIM_PROXY_URL), or nil to claim
	// directly. proxyDownUntil (ms) skips it after it was unreachable;
	// workFinished (1/0) asks it to query now because a task just finished.
	claimProxy     *ClaimProxy
	proxyDownUntil int64
	workFinished   int32
}

// NewAgentPoller creates a new AgentPoller with the given configuration.
func NewAgentPoller(cfg Config) *AgentPoller {
	return &AgentPoller{
		cfg:      cfg,
		serverID: uuid.New().String(),
		handlers: make(map[string]Handler),
		stopCh:   make(chan struct{}),
		sem:      make(chan struct{}, cfg.MaxConcurrent),
		// A fresh poller may have missed work: the first ask kicks.
		claimProxy:   ClaimProxyFromEnv(),
		workFinished: 1,
	}
}

// SetClaimProxy claims through this claimer instead of polling MongoDB (nil
// claims directly). Defaults to FW_CLAIM_PROXY_URL.
func (p *AgentPoller) SetClaimProxy(proxy *ClaimProxy) {
	p.claimProxy = proxy
	atomic.StoreInt64(&p.proxyDownUntil, 0)
}

// ServerID is the server id this poller claims under.
func (p *AgentPoller) ServerID() string { return p.serverID }

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

	handlers := p.EffectiveHandlers()
	task, err := p.claimNext(ctx, handlers)
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

// EffectiveHandlers returns the handler names to poll for.
// If a topicFilter is set (e.g., by RegistryRunner), it uses that;
// otherwise it returns all registered handlers.
func (p *AgentPoller) EffectiveHandlers() []string {
	if p.topicFilter != nil {
		return p.topicFilter()
	}
	return p.RegisteredHandlers()
}

func (p *AgentPoller) pollCycle(ctx context.Context) {
	handlers := p.EffectiveHandlers()
	if len(handlers) == 0 {
		return
	}

	// Take a slot BEFORE claiming: a task claimed with no slot to run it would
	// sit `running` under this server until its lease expired.
	select {
	case p.sem <- struct{}{}:
	default:
		return
	}

	task, err := p.claimNext(ctx, handlers)
	if err != nil || task == nil {
		<-p.sem
		if err != nil {
			log.Printf("Error claiming task: %v", err)
		}
		return
	}

	p.wg.Add(1)
	go func() {
		defer p.wg.Done()
		defer func() { <-p.sem }()
		defer p.taskDone()
		p.processTask(ctx, task)
	}()
}

// claimNext makes one claim (protocol v1.2): records this server and a lease,
// offers the memory free now, and every LeaseReclaimScanMS also looks for a
// running task whose lease expired.
func (p *AgentPoller) claimNext(ctx context.Context, handlers []string) (*TaskDocument, error) {
	now := NowMillis()
	if proxy := p.claimProxy; proxy != nil && now >= atomic.LoadInt64(&p.proxyDownUntil) {
		task, err := p.claimViaProxy(ctx, proxy, handlers)
		if err == nil {
			return task, nil
		}
		atomic.StoreInt64(&p.proxyDownUntil, now+ClaimProxyRetryMS)
		log.Printf("Claimer %s unreachable (%v); claiming directly for %ds",
			proxy.BaseURL(), err, ClaimProxyRetryMS/1000)
	}
	reclaim := now-atomic.LoadInt64(&p.lastReclaimScan) >= LeaseReclaimScanMS
	task, err := p.ops.ClaimTaskFor(ctx, handlers, []string{p.cfg.TaskList}, p.serverID, ClaimResources(), reclaim)
	if err == nil && task == nil && reclaim {
		atomic.StoreInt64(&p.lastReclaimScan, now)
	}
	return task, err
}

// claimViaProxy asks the host's claimer, long-polling up to ClaimProxyWaitS.
// The claimer also does the lease-expiry reclaim scan for its waiters. A task
// it returns is already held by this server; it is re-read and run only if
// that is still true (it may have been reaped between hand-over and read).
// Only an unreachable claimer is an error: a failed re-read is a database
// problem the direct claim would hit too.
func (p *AgentPoller) claimViaProxy(ctx context.Context, proxy *ClaimProxy, handlers []string) (*TaskDocument, error) {
	kick := atomic.SwapInt32(&p.workFinished, 0) == 1
	uid, err := proxy.Next(ctx, p.serverID, handlers, []string{p.cfg.TaskList}, ClaimResources(),
		ClaimProxyWaitS, kick)
	if err != nil {
		return nil, err
	}
	if uid == "" {
		return nil, nil
	}
	task, err := p.ops.GetTask(ctx, uid)
	if err != nil || task == nil {
		if err != nil {
			log.Printf("Reading handed-over task %s: %v", uid, err)
		}
		return nil, nil
	}
	if task.ServerID != p.serverID || task.State != TaskStateRunning {
		return nil, nil
	}
	return task, nil
}

// taskDone records that a slot freed and wakes the claimer: the poll loop may
// be inside a long-poll, and follow-on work may now exist.
func (p *AgentPoller) taskDone() {
	atomic.StoreInt32(&p.workFinished, 1)
	if proxy := p.claimProxy; proxy != nil && NowMillis() >= atomic.LoadInt64(&p.proxyDownUntil) {
		proxy.Kick()
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
		// 2. No handler found: retry path (another server may have it).
		errMsg := fmt.Sprintf("No handler registered for: %s", task.Name)
		p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
			StepLogLevelError, "Handler error: "+errMsg)
		log.Printf("No handler for task: %s", task.Name)
		p.fail(ctx, task, fmt.Errorf("no handler registered"))
		return
	}

	owned := int32(1)
	p.active.Store(task.UUID, &owned)
	defer p.active.Delete(task.UUID)

	// 3. Dispatching handler
	p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
		StepLogLevelInfo, fmt.Sprintf("Dispatching handler: %s", task.Name))

	dispatchStart := time.Now()

	// Read step parameters
	params, err := p.ops.ReadStepParams(ctx, task.StepID)
	if err != nil {
		log.Printf("Failed to read step params: %v", err)
		p.fail(ctx, task, err)
		return
	}

	// Inject handler-level step_log callback
	params["_step_log"] = func(message string, level string) {
		p.ops.InsertStepLog(ctx, task.StepID, task.WorkflowID, p.serverID,
			task.Name, StepLogSourceHandler, level, message)
	}

	// Inject _facet_name
	params["_facet_name"] = task.Name

	// Inject _handler_metadata if provider is available
	if p.metadataProvider != nil {
		if meta := p.metadataProvider(task.Name); meta != nil {
			params["_handler_metadata"] = meta
		}
	}

	// Inject _update_step callback for streaming partial results
	params["_update_step"] = func(partial map[string]interface{}) {
		if err := p.ops.UpdateStepReturns(ctx, task.StepID, partial); err != nil {
			log.Printf("Failed to update step returns: %v", err)
		}
	}

	// Inject _task_heartbeat(progressPct, message): renews the lease now (the
	// heartbeat loop also renews it every interval). progressPct < 0 = none.
	params["_task_heartbeat"] = func(progressPct int, message string) {
		ok, err := p.ops.RenewLease(ctx, task.UUID, p.serverID, progressPct, message)
		if err == nil && !ok {
			atomic.StoreInt32(&owned, 0)
		}
	}

	// Inject _is_cancelled(): true once the task was canceled, finished by
	// someone else, or reclaimed. A long handler should check it at batch
	// boundaries and return early.
	params["_is_cancelled"] = func() bool {
		if atomic.LoadInt32(&owned) == 0 {
			return true
		}
		reason, err := p.ops.CancellationReason(ctx, task.UUID, p.serverID)
		return err == nil && reason != ""
	}

	// Invoke handler
	result, err := handler(params)
	if err != nil {
		p.fail(ctx, task, err)
		return
	}

	// Another execution may own this task now (reclaimed after a lost lease,
	// canceled, or finished by the timeout watchdog): write nothing.
	if reason, err := p.ops.CancellationReason(ctx, task.UUID, p.serverID); err == nil && reason != "" {
		p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
			StepLogLevelWarning, "Handler result discarded: "+reason)
		log.Printf("Discarding result of %s %s: %s", task.Name, task.UUID, reason)
		return
	}

	// Write returns to step
	if result != nil {
		if err := p.ops.WriteStepReturns(ctx, task.StepID, result); err != nil {
			log.Printf("Failed to write step returns: %v", err)
			p.fail(ctx, task, err)
			return
		}
	}

	// Complete (ownership-gated), then signal the Python RunnerService.
	done, err := p.ops.CompleteTask(ctx, task, p.serverID)
	if err != nil {
		log.Printf("Failed to mark task completed: %v", err)
		return
	}
	if !done {
		p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
			StepLogLevelWarning, "Handler result discarded: task was reclaimed before completion")
		return
	}
	if err := p.ops.InsertResumeTask(ctx, task.StepID, task.WorkflowID, task.TaskListName, task.Name); err != nil {
		// The task is completed but the workflow was not signalled; the
		// runtime's stuck-step sweep recovers the step.
		log.Printf("Failed to insert resume task: %v", err)
		return
	}

	// 4. Handler completed
	durationMs := time.Since(dispatchStart).Milliseconds()
	p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
		StepLogLevelSuccess, fmt.Sprintf("Handler completed: %s (%dms)", task.Name, durationMs))
}

// fail records a handler error: retry with backoff, or dead-letter (a
// PermanentError at once). Never `failed`.
func (p *AgentPoller) fail(ctx context.Context, task *TaskDocument, cause error) {
	permanent := IsPermanent(cause)
	t, err := p.ops.FailTask(ctx, task, p.serverID, cause.Error(), permanent)
	var what string
	switch {
	case err != nil:
		what = "not recorded: " + err.Error()
	case t == nil:
		what = "not recorded: task no longer held by this server"
	case t.DeadLettered() && permanent:
		what = "dead-lettered (permanent)"
	case t.DeadLettered():
		what = fmt.Sprintf("dead-lettered after %d attempts", t.RetryCount)
	default:
		what = fmt.Sprintf("retry %d/%d in %ds", t.RetryCount, task.EffectiveMaxRetries(),
			(t.NextRetryAfter-NowMillis())/1000)
	}
	p.emitStepLog(ctx, task.StepID, task.WorkflowID, task.Name,
		StepLogLevelError, fmt.Sprintf("Handler error (%s): %v", what, cause))
	log.Printf("Handler error for %s (%s): %v", task.Name, what, cause)
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
			p.heartbeat(ctx)
		}
	}
}

// heartbeat pings the server record (re-registering it when a reaper pruned it
// or marked it shutdown) and renews the lease of every task running here.
func (p *AgentPoller) heartbeat(ctx context.Context) {
	live, err := p.registration.Ping(ctx, p.serverID)
	if err != nil {
		log.Printf("Heartbeat error: %v", err)
	} else if !live {
		log.Printf("Server record %s missing or marked shutdown -- registering again", p.serverID)
		if err := p.registration.Register(ctx, p.serverID, p.cfg, p.RegisteredHandlers()); err != nil {
			log.Printf("Re-register failed: %v", err)
		}
	}
	p.active.Range(func(key, value interface{}) bool {
		ok, err := p.ops.RenewLease(ctx, key.(string), p.serverID, -1, "")
		if err == nil && !ok {
			atomic.StoreInt32(value.(*int32), 0)
		}
		return true
	})
}
