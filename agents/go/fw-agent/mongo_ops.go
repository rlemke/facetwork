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
	"regexp"
	"sort"

	"github.com/google/uuid"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

// MongoOps provides MongoDB operations for the AFL agent protocol.
type MongoOps struct {
	db *mongo.Database
}

// NewMongoOps creates a new MongoOps instance.
func NewMongoOps(db *mongo.Database) *MongoOps {
	return &MongoOps{db: db}
}

// ClaimTask claims a pending task by name on one task list.
//
// Deprecated: kept for source compatibility. It records no server id and
// offers no resources (so any task carrying a `requires` floor is declined)
// and never reclaims expired leases. Use ClaimTaskFor.
func (m *MongoOps) ClaimTask(ctx context.Context, taskNames []string, taskList string) (*TaskDocument, error) {
	return m.ClaimTaskFor(ctx, taskNames, []string{taskList}, "", nil, false)
}

// ClaimTaskFor claims one task (protocol v1.2 claim_task, then optionally
// reclaim_expired_task), recording serverID and a lease.
//
// Besides name and task list the filter skips tasks in their retry cooldown,
// tasks that need a script environment or FFL feature (an agent runs no
// evaluator, so it declines both), and tasks whose `requires` floor exceeds
// resources. A dimension the task names that is not in resources declines the
// claim. reclaimExpired also tries to take over a running task whose lease
// expired; callers pace it (LeaseReclaimScanMS). Returns nil when nothing matches.
func (m *MongoOps) ClaimTaskFor(ctx context.Context, taskNames, taskLists []string, serverID string,
	resources map[string]interface{}, reclaimExpired bool) (*TaskDocument, error) {
	if len(taskNames) == 0 || len(taskLists) == 0 {
		return nil, nil
	}
	collection := m.db.Collection(CollectionTasks)
	now := NowMillis()
	set := bson.M{
		"state":         TaskStateRunning,
		"updated":       now,
		"server_id":     serverID,
		"lease_expires": now + LeaseMS,
	}
	opts := options.FindOneAndUpdate().SetReturnDocument(options.After)

	pending := bson.M{
		"state":          TaskStatePending,
		"task_list_name": taskListFilter(taskLists),
		"$and": bson.A{
			nameFilter(taskNames),
			bson.M{"$or": bson.A{
				bson.M{"next_retry_after": bson.M{"$exists": false}},
				bson.M{"next_retry_after": 0},
				bson.M{"next_retry_after": bson.M{"$lte": now}},
			}},
			noEnvironment(),
			bson.M{"$or": bson.A{
				bson.M{"required_features": bson.M{"$exists": false}},
				bson.M{"required_features": bson.A{}},
			}},
			resourceFilter(resources),
		},
	}
	task, err := findOneAndUpdate(ctx, collection, pending, bson.M{"$set": set}, opts)
	if err != nil || task != nil || !reclaimExpired {
		return task, err
	}

	// A running task whose holder stopped renewing its lease. It is a RETRY, so
	// retry_count is bumped, and only while budget remains: an exhausted one is
	// left for the runtime's reaper, which dead-letters it.
	expired := bson.M{
		"state":          TaskStateRunning,
		"task_list_name": taskListFilter(taskLists),
		"lease_expires":  bson.M{"$lt": now, "$gt": 0},
		"$and": bson.A{
			nameFilter(taskNames),
			noEnvironment(),
			bson.M{"$or": bson.A{
				bson.M{"max_retries": bson.M{"$lte": 0}},
				bson.M{"$expr": bson.M{"$lt": bson.A{
					bson.M{"$ifNull": bson.A{"$retry_count", 0}},
					bson.M{"$ifNull": bson.A{"$max_retries", DefaultMaxRetries}},
				}}},
			}},
		},
	}
	return findOneAndUpdate(ctx, collection, expired,
		bson.M{"$set": set, "$inc": bson.M{"retry_count": 1}}, opts)
}

// GetTask reads one task by uuid, or nil when it does not exist.
func (m *MongoOps) GetTask(ctx context.Context, taskID string) (*TaskDocument, error) {
	var task TaskDocument
	err := m.db.Collection(CollectionTasks).FindOne(ctx, bson.M{"uuid": taskID}).Decode(&task)
	if err == mongo.ErrNoDocuments {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &task, nil
}

func findOneAndUpdate(ctx context.Context, c *mongo.Collection, filter, update interface{},
	opts *options.FindOneAndUpdateOptions) (*TaskDocument, error) {
	var task TaskDocument
	err := c.FindOneAndUpdate(ctx, filter, update, opts).Decode(&task)
	if err == mongo.ErrNoDocuments {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &task, nil
}

func taskListFilter(taskLists []string) interface{} {
	if len(taskLists) == 1 {
		return taskLists[0]
	}
	return bson.M{"$in": taskLists}
}

// nameFilter matches exact names or `name:` prefixes (the prefix is a literal,
// so it is quoted: "." is a regex wildcard).
func nameFilter(taskNames []string) bson.M {
	or := bson.A{bson.M{"name": bson.M{"$in": taskNames}}}
	for _, n := range taskNames {
		or = append(or, bson.M{"name": bson.M{"$regex": "^" + regexp.QuoteMeta(n) + ":"}})
	}
	return bson.M{"$or": or}
}

func noEnvironment() bson.M {
	return bson.M{"$or": bson.A{
		bson.M{"environment_hash": bson.M{"$exists": false}},
		bson.M{"environment_hash": ""},
	}}
}

func resourceFilter(resources map[string]interface{}) bson.M {
	or := bson.A{
		bson.M{"requires": bson.M{"$exists": false}},
		bson.M{"requires": bson.M{}},
	}
	dims := make([]string, 0, len(resources))
	for k, v := range resources {
		switch v.(type) {
		case int, int32, int64, float32, float64:
			dims = append(dims, k)
		}
	}
	sort.Strings(dims)
	if len(dims) > 0 {
		perDim := bson.A{bson.M{"requires_dims": bson.M{"$not": bson.M{"$elemMatch": bson.M{"$nin": dims}}}}}
		for _, dim := range dims {
			perDim = append(perDim, bson.M{"$or": bson.A{
				bson.M{"requires." + dim: bson.M{"$exists": false}},
				bson.M{"requires." + dim: bson.M{"$lte": resources[dim]}},
			}})
		}
		or = append(or, bson.M{"$and": perDim})
	}
	return bson.M{"$or": or}
}

// RenewLease renews a running task's lease (protocol renew_task_lease).
// Ownership-gated: returns false when the task is no longer this server's.
// progressPct < 0 and an empty progressMessage leave progress unchanged.
func (m *MongoOps) RenewLease(ctx context.Context, taskID, serverID string, progressPct int, progressMessage string) (bool, error) {
	now := NowMillis()
	set := bson.M{"task_heartbeat": now, "lease_expires": now + LeaseMS}
	if progressPct >= 0 {
		if progressPct > 100 {
			progressPct = 100
		}
		set["progress_pct"] = progressPct
	}
	if progressMessage != "" {
		set["progress_message"] = progressMessage
	}
	res, err := m.db.Collection(CollectionTasks).UpdateOne(ctx,
		bson.M{"uuid": taskID, "state": TaskStateRunning, "server_id": serverID},
		bson.M{"$set": set})
	if err != nil {
		return false, err
	}
	return res.MatchedCount > 0, nil
}

// CancellationReason says why an in-flight execution should stop, or "" when
// it should carry on (protocol check_cancellation): the task is gone, canceled,
// already terminal (e.g. the runtime's timeout watchdog), or reclaimed.
func (m *MongoOps) CancellationReason(ctx context.Context, taskID, serverID string) (string, error) {
	var doc struct {
		State    string `bson:"state"`
		ServerID string `bson:"server_id"`
	}
	err := m.db.Collection(CollectionTasks).FindOne(ctx, bson.M{"uuid": taskID},
		options.FindOne().SetProjection(bson.M{"state": 1, "server_id": 1, "_id": 0})).Decode(&doc)
	if err == mongo.ErrNoDocuments {
		return "task record no longer exists", nil
	}
	if err != nil {
		return "", err
	}
	switch doc.State {
	case TaskStateCanceled:
		return "task was canceled (terminate-workflow)", nil
	case TaskStateCompleted, TaskStateFailed, TaskStateDeadLetter, TaskStateIgnored:
		return "task already terminal (" + doc.State + ")", nil
	}
	if serverID != "" && doc.ServerID != serverID {
		return "task was reclaimed by another runner", nil
	}
	return "", nil
}

// ReadStepParams reads the params attribute from a step.
func (m *MongoOps) ReadStepParams(ctx context.Context, stepID string) (map[string]interface{}, error) {
	collection := m.db.Collection(CollectionSteps)

	var step StepDocument
	err := collection.FindOne(ctx, bson.M{"uuid": stepID}).Decode(&step)
	if err != nil {
		return nil, err
	}

	result := make(map[string]interface{})
	for name, attr := range step.Attributes.Params {
		result[name] = attr.Value
	}

	return result, nil
}

// FetchStep returns the full snapshot of a referenced step's
// persisted attributes.  Mirrors Python HandlerContext.fetch_step:
// given a tagged JSON FacetRef ({_facet_ref:true, step_id, ...}),
// fetch the step row and return a map shaped like:
//
//	{
//	  "step_id":     ...,
//	  "workflow_id": ...,
//	  "facet_name":  ...,
//	  "params":      {name: value, ...},
//	  "returns":     {name: value, ...},
//	}
//
// The snapshot is read-only by contract — mutating the returned map
// does not propagate to the upstream step.
func (m *MongoOps) FetchStep(ctx context.Context, ref map[string]interface{}) (map[string]interface{}, error) {
	stepID, _ := ref["step_id"].(string)
	if stepID == "" {
		return nil, fmt.Errorf("fetch_step: ref missing 'step_id'")
	}

	collection := m.db.Collection(CollectionSteps)
	var step StepDocument
	if err := collection.FindOne(ctx, bson.M{"uuid": stepID}).Decode(&step); err != nil {
		return nil, err
	}

	params := make(map[string]interface{}, len(step.Attributes.Params))
	for name, attr := range step.Attributes.Params {
		params[name] = attr.Value
	}
	returns := make(map[string]interface{}, len(step.Attributes.Returns))
	for name, attr := range step.Attributes.Returns {
		returns[name] = attr.Value
	}
	return map[string]interface{}{
		"step_id":     step.UUID,
		"workflow_id": step.WorkflowID,
		"facet_name":  step.FacetName,
		"params":      params,
		"returns":     returns,
	}, nil
}

// WriteStepReturns writes return attributes to a step.
func (m *MongoOps) WriteStepReturns(ctx context.Context, stepID string, returns map[string]interface{}) error {
	collection := m.db.Collection(CollectionSteps)

	// Build the $set update for each return field
	setFields := bson.M{}
	for name, value := range returns {
		setFields["attributes.returns."+name] = StepAttribute{
			Name:     name,
			Value:    value,
			TypeHint: inferTypeHint(value),
		}
	}

	filter := bson.M{
		"uuid":  stepID,
		"state": StepStateEventTransmit,
	}

	update := bson.M{"$set": setFields}

	_, err := collection.UpdateOne(ctx, filter, update)
	return err
}

// UpdateStepReturns merges partial return attributes into a step.
// Unlike WriteStepReturns, this does NOT require the step to be in EVENT_TRANSMIT state,
// allowing handlers to stream partial results during execution.
func (m *MongoOps) UpdateStepReturns(ctx context.Context, stepID string, partial map[string]interface{}) error {
	collection := m.db.Collection(CollectionSteps)

	setFields := bson.M{}
	for name, value := range partial {
		setFields["attributes.returns."+name] = StepAttribute{
			Name:     name,
			Value:    value,
			TypeHint: inferTypeHint(value),
		}
	}

	filter := bson.M{"uuid": stepID}
	update := bson.M{"$set": setFields}

	_, err := collection.UpdateOne(ctx, filter, update)
	return err
}

// MarkTaskCompleted marks a task as completed regardless of who holds it.
//
// Deprecated: not ownership-gated. Use CompleteTask.
func (m *MongoOps) MarkTaskCompleted(ctx context.Context, task *TaskDocument) error {
	collection := m.db.Collection(CollectionTasks)

	update := bson.M{
		"$set": bson.M{
			"state":   TaskStateCompleted,
			"updated": NowMillis(),
		},
	}

	_, err := collection.UpdateOne(ctx, bson.M{"uuid": task.UUID}, update)
	return err
}

// MarkTaskFailed marks a task as failed with an error message.
//
// Deprecated: a `failed` event task is recreated by the runtime's stuck-step
// sweep at retry_count 0, so the failure retries forever. Use FailTask.
func (m *MongoOps) MarkTaskFailed(ctx context.Context, task *TaskDocument, errorMsg string) error {
	collection := m.db.Collection(CollectionTasks)

	update := bson.M{
		"$set": bson.M{
			"state":   TaskStateFailed,
			"updated": NowMillis(),
			"error":   bson.M{"message": errorMsg},
		},
	}

	_, err := collection.UpdateOne(ctx, bson.M{"uuid": task.UUID}, update)
	return err
}

// CompleteTask marks a task completed (protocol complete_task), only while
// serverID still holds it. false means it was reclaimed or terminated: another
// execution owns the outcome, so do NOT insert a resume task.
func (m *MongoOps) CompleteTask(ctx context.Context, task *TaskDocument, serverID string) (bool, error) {
	res, err := m.db.Collection(CollectionTasks).UpdateOne(ctx,
		bson.M{"uuid": task.UUID, "state": TaskStateRunning, "server_id": serverID},
		bson.M{"$set": bson.M{"state": TaskStateCompleted, "updated": NowMillis()}})
	if err != nil {
		return false, err
	}
	return res.MatchedCount > 0, nil
}

// FailTransitionResult is the outcome of FailTransition.
type FailTransitionResult struct {
	State          string
	RetryCount     int
	NextRetryAfter int64
}

// DeadLettered reports whether the transition dead-letters the task.
func (t FailTransitionResult) DeadLettered() bool { return t.State == TaskStateDeadLetter }

// FailTransition is the protocol's failure transition
// (conformance/retry_cases.json). A permanent error dead-letters without
// spending budget; otherwise the count goes up and the task is either
// dead-lettered (budget spent) or returned to pending after a backoff.
func FailTransition(retryCount, maxRetries int, permanent bool, now int64) FailTransitionResult {
	if permanent {
		return FailTransitionResult{State: TaskStateDeadLetter, RetryCount: retryCount}
	}
	next := retryCount + 1
	if maxRetries > 0 && next >= maxRetries {
		return FailTransitionResult{State: TaskStateDeadLetter, RetryCount: next}
	}
	return FailTransitionResult{State: TaskStatePending, RetryCount: next, NextRetryAfter: now + RetryBackoffMS(next)}
}

// FailTask records a handler failure (protocol fail_task), only while serverID
// still holds the task. Returns the transition applied, or nil if the task was
// no longer this server's.
//
// It never writes `failed`: the runtime's stuck-step sweep sees no active and
// no dead-lettered task for the step and creates a fresh one at retry_count 0,
// so a failure would retry forever. A dead-lettered task makes the sweep fail
// the step instead.
func (m *MongoOps) FailTask(ctx context.Context, task *TaskDocument, serverID, errorMsg string, permanent bool) (*FailTransitionResult, error) {
	now := NowMillis()
	t := FailTransition(task.RetryCount, task.EffectiveMaxRetries(), permanent, now)
	errDoc := bson.M{"message": errorMsg}
	set := bson.M{"state": t.State, "updated": now, "retry_count": t.RetryCount, "error": errDoc}
	if t.DeadLettered() {
		if permanent {
			errDoc["permanent"] = true
		}
	} else {
		set["server_id"] = ""
		set["next_retry_after"] = t.NextRetryAfter
	}
	res, err := m.db.Collection(CollectionTasks).UpdateOne(ctx,
		bson.M{"uuid": task.UUID, "state": TaskStateRunning, "server_id": serverID},
		bson.M{"$set": set})
	if err != nil {
		return nil, err
	}
	if res.MatchedCount == 0 {
		return nil, nil
	}
	return &t, nil
}

// InsertResumeTask creates an fw:resume task for the Python RunnerService.
// If facetName is non-empty, the task name includes it for visibility (e.g. "fw:resume:ns.Facet").
func (m *MongoOps) InsertResumeTask(ctx context.Context, stepID, workflowID, taskList, facetName string) error {
	collection := m.db.Collection(CollectionTasks)

	resumeName := ResumeTaskName
	if facetName != "" {
		resumeName = ResumeTaskName + ":" + facetName
	}
	now := NowMillis()
	task := TaskDocument{
		UUID:         uuid.New().String(),
		Name:         resumeName,
		RunnerID:     "",
		WorkflowID:   workflowID,
		FlowID:       "",
		StepID:       stepID,
		State:        TaskStatePending,
		Created:      now,
		Updated:      now,
		TaskListName: taskList,
		DataType:     "resume",
		Data: map[string]interface{}{
			"step_id":     stepID,
			"workflow_id": workflowID,
		},
	}

	_, err := collection.InsertOne(ctx, task)
	return err
}

// InsertStepLog inserts a step log entry for dashboard observability.
// Best-effort: errors are logged but not returned.
func (m *MongoOps) InsertStepLog(ctx context.Context, stepID, workflowID, runnerID, facetName, source, level, message string) {
	collection := m.db.Collection(CollectionStepLogs)

	now := NowMillis()
	doc := bson.M{
		"uuid":        uuid.New().String(),
		"step_id":     stepID,
		"workflow_id": workflowID,
		"runner_id":   runnerID,
		"facet_name":  facetName,
		"source":      source,
		"level":       level,
		"message":     message,
		"details":     bson.M{},
		"time":        now,
	}

	if _, err := collection.InsertOne(ctx, doc); err != nil {
		log.Printf("Could not save step log for step %s: %v", stepID, err)
	}
}

func inferTypeHint(value interface{}) string {
	switch value.(type) {
	case bool:
		return "Boolean"
	case int, int32, int64:
		return "Long"
	case float32, float64:
		return "Double"
	case string:
		return "String"
	case []interface{}:
		return "List"
	case map[string]interface{}:
		return "Map"
	default:
		return "Any"
	}
}
