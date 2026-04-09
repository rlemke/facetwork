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

import "time"

// TaskDocument represents a task in the tasks collection.
type TaskDocument struct {
	UUID         string                 `bson:"uuid"`
	Name         string                 `bson:"name"`
	RunnerID     string                 `bson:"runner_id"`
	WorkflowID   string                 `bson:"workflow_id"`
	FlowID       string                 `bson:"flow_id"`
	StepID       string                 `bson:"step_id"`
	State        string                 `bson:"state"`
	Created      int64                  `bson:"created"`
	Updated      int64                  `bson:"updated"`
	Error        map[string]interface{} `bson:"error,omitempty"`
	TaskListName string                 `bson:"task_list_name"`
	DataType     string                 `bson:"data_type,omitempty"`
	Data         map[string]interface{} `bson:"data,omitempty"`
}

// StepAttribute represents a parameter or return value attribute.
type StepAttribute struct {
	Name     string      `bson:"name"`
	Value    interface{} `bson:"value"`
	TypeHint string      `bson:"type_hint,omitempty"`
}

// StepAttributes holds params and returns for a step.
type StepAttributes struct {
	Params  map[string]StepAttribute `bson:"params,omitempty"`
	Returns map[string]StepAttribute `bson:"returns,omitempty"`
}

// StepDocument represents a step in the steps collection.
type StepDocument struct {
	UUID        string         `bson:"uuid"`
	WorkflowID  string         `bson:"workflow_id"`
	ObjectType  string         `bson:"object_type"`
	State       string         `bson:"state"`
	StatementID string         `bson:"statement_id"`
	ContainerID string         `bson:"container_id"`
	BlockID     string         `bson:"block_id"`
	FacetName   string         `bson:"facet_name,omitempty"`
	Attributes  StepAttributes `bson:"attributes,omitempty"`
}

// ServerDocument represents a server in the servers collection.
type ServerDocument struct {
	UUID        string   `bson:"uuid"`
	ServerGroup string   `bson:"server_group"`
	ServiceName string   `bson:"service_name"`
	ServerName  string   `bson:"server_name"`
	ServerIPs   []string `bson:"server_ips"`
	StartTime   int64    `bson:"start_time"`
	PingTime    int64    `bson:"ping_time"`
	Topics      []string `bson:"topics"`
	Handlers    []string `bson:"handlers"`
	Handled     []struct {
		Handler    string `bson:"handler"`
		Handled    int    `bson:"handled"`
		NotHandled int    `bson:"not_handled"`
	} `bson:"handled"`
	State string `bson:"state"`
}

// NowMillis returns the current time in milliseconds since Unix epoch.
func NowMillis() int64 {
	return time.Now().UnixNano() / int64(time.Millisecond)
}
