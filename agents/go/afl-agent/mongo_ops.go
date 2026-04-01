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

// ClaimTask atomically claims a pending task for processing.
// Returns nil if no task is available.
func (m *MongoOps) ClaimTask(ctx context.Context, taskNames []string, taskList string) (*TaskDocument, error) {
	collection := m.db.Collection(CollectionTasks)

	filter := bson.M{
		"state":          TaskStatePending,
		"name":           bson.M{"$in": taskNames},
		"task_list_name": taskList,
	}

	update := bson.M{
		"$set": bson.M{
			"state":   TaskStateRunning,
			"updated": NowMillis(),
		},
	}

	opts := options.FindOneAndUpdate().SetReturnDocument(options.After)

	var task TaskDocument
	err := collection.FindOneAndUpdate(ctx, filter, update, opts).Decode(&task)
	if err == mongo.ErrNoDocuments {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	return &task, nil
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

// MarkTaskCompleted marks a task as completed.
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

// InsertResumeTask creates an afl:resume task for the Python RunnerService.
// If facetName is non-empty, the task name includes it for visibility (e.g. "afl:resume:ns.Facet").
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
