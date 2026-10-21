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

// This library against the protocol's shared conformance cases
// (agents/protocol/conformance). The Python runtime runs the same files in
// tests/runtime/test_agent_protocol_conformance.py, so a rule exists once.
//
// The retry cases are pure. The claim and lifecycle cases need a real MongoDB:
// set FW_AGENT_TEST_MONGO_URL (e.g. a throwaway mongo:7 container); they are
// skipped otherwise. A scratch database is created and dropped.

import (
	"context"
	"encoding/json"
	"io/ioutil"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

var confDir = filepath.Join("..", "..", "protocol", "conformance")

func loadJSON(t *testing.T, path string, v interface{}) {
	t.Helper()
	raw, err := ioutil.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, v); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}

// --- retry transition (pure) -------------------------------------------------

func TestConformanceRetryCases(t *testing.T) {
	var spec struct {
		Cases []struct {
			ID              string `json:"id"`
			RetryCount      int    `json:"retry_count"`
			MaxRetries      int    `json:"max_retries"`
			Permanent       bool   `json:"permanent"`
			State           string `json:"state"`
			RetryCountAfter int    `json:"retry_count_after"`
			BackoffMS       *int64 `json:"backoff_ms"`
		} `json:"cases"`
	}
	loadJSON(t, filepath.Join(confDir, "retry_cases.json"), &spec)
	if len(spec.Cases) == 0 {
		t.Fatal("no retry cases")
	}
	for _, c := range spec.Cases {
		c := c
		t.Run(c.ID, func(t *testing.T) {
			now := int64(1000000)
			got := FailTransition(c.RetryCount, c.MaxRetries, c.Permanent, now)
			if got.State != c.State {
				t.Errorf("state = %s, want %s", got.State, c.State)
			}
			if got.RetryCount != c.RetryCountAfter {
				t.Errorf("retry_count = %d, want %d", got.RetryCount, c.RetryCountAfter)
			}
			if c.BackoffMS != nil && got.NextRetryAfter-now != *c.BackoffMS {
				t.Errorf("backoff = %d, want %d", got.NextRetryAfter-now, *c.BackoffMS)
			}
		})
	}
}

func TestConformanceSpecVersionAndTiming(t *testing.T) {
	var spec struct {
		Version string `json:"version"`
		Timing  struct {
			LeaseMS            int64   `json:"lease_ms"`
			LeaseReclaimScanMS int64   `json:"lease_reclaim_scan_ms"`
			DefaultMaxRetries  int     `json:"default_max_retries"`
			ClaimProxyWaitS    float64 `json:"claim_proxy_wait_s"`
			ClaimProxyRetryMS  int64   `json:"claim_proxy_retry_ms"`
		} `json:"timing"`
	}
	loadJSON(t, filepath.Join(confDir, "..", "constants.json"), &spec)
	if spec.Version != ProtocolVersion {
		t.Errorf("spec version %s, library implements %s", spec.Version, ProtocolVersion)
	}
	if spec.Timing.LeaseMS != LeaseMS || spec.Timing.LeaseReclaimScanMS != LeaseReclaimScanMS ||
		spec.Timing.DefaultMaxRetries != DefaultMaxRetries ||
		spec.Timing.ClaimProxyWaitS != ClaimProxyWaitS || spec.Timing.ClaimProxyRetryMS != ClaimProxyRetryMS {
		t.Errorf("timing mismatch: spec %+v", spec.Timing)
	}
}

// --- live MongoDB --------------------------------------------------------------

func liveDB(t *testing.T) *mongo.Database {
	t.Helper()
	url := os.Getenv("FW_AGENT_TEST_MONGO_URL")
	if url == "" {
		t.Skip("FW_AGENT_TEST_MONGO_URL not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := mongo.Connect(ctx, options.Client().ApplyURI(url))
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	db := client.Database("fw_agent_conformance_go_" + uuid.New().String()[:8])
	t.Cleanup(func() {
		_ = db.Drop(context.Background())
		_ = client.Disconnect(context.Background())
	})
	return db
}

// resolve turns {"rel_ms": N} into now+N, recursively; JSON numbers that are
// whole become int64 so Mongo stores them as integers.
func resolve(v interface{}, now int64) interface{} {
	switch x := v.(type) {
	case map[string]interface{}:
		if rel, ok := x["rel_ms"]; ok && len(x) == 1 {
			return now + int64(rel.(float64))
		}
		out := bson.M{}
		for k, val := range x {
			out[k] = resolve(val, now)
		}
		return out
	case []interface{}:
		out := bson.A{}
		for _, val := range x {
			out = append(out, resolve(val, now))
		}
		return out
	case float64:
		if x == float64(int64(x)) {
			return int64(x)
		}
		return x
	}
	return v
}

func TestConformanceClaimCases(t *testing.T) {
	var spec struct {
		Server struct {
			TaskNames []string               `json:"task_names"`
			TaskLists []string               `json:"task_lists"`
			Resources map[string]interface{} `json:"resources"`
		} `json:"server"`
		Cases []struct {
			ID              string                 `json:"id"`
			Task            map[string]interface{} `json:"task"`
			Claimed         bool                   `json:"claimed"`
			RetryCountAfter *int                   `json:"retry_count_after"`
		} `json:"cases"`
	}
	loadJSON(t, filepath.Join(confDir, "claim_cases.json"), &spec)
	db := liveDB(t)
	ctx := context.Background()
	tasks := db.Collection(CollectionTasks)
	ops := NewMongoOps(db)
	for _, c := range spec.Cases {
		c := c
		t.Run(c.ID, func(t *testing.T) {
			_ = tasks.Drop(ctx)
			now := NowMillis()
			id := "t-" + c.ID
			doc := bson.M{"uuid": id, "runner_id": "r", "workflow_id": "w", "flow_id": "f",
				"step_id": "s-" + id, "created": now, "updated": now}
			for k, v := range c.Task {
				doc[k] = resolve(v, now)
			}
			if _, err := tasks.InsertOne(ctx, doc); err != nil {
				t.Fatal(err)
			}
			got, err := ops.ClaimTaskFor(ctx, spec.Server.TaskNames, spec.Server.TaskLists,
				"conformance-server", spec.Server.Resources, true)
			if err != nil {
				t.Fatal(err)
			}
			if (got != nil) != c.Claimed {
				t.Fatalf("claimed = %v, want %v", got != nil, c.Claimed)
			}
			if got == nil {
				return
			}
			var stored bson.M
			if err := tasks.FindOne(ctx, bson.M{"uuid": id}).Decode(&stored); err != nil {
				t.Fatal(err)
			}
			if stored["state"] != "running" || stored["server_id"] != "conformance-server" {
				t.Errorf("stored state/server = %v/%v", stored["state"], stored["server_id"])
			}
			if toInt64(stored["lease_expires"]) <= now {
				t.Errorf("lease_expires %v not in the future", stored["lease_expires"])
			}
			if c.RetryCountAfter != nil && int(toInt64(stored["retry_count"])) != *c.RetryCountAfter {
				t.Errorf("retry_count = %v, want %d", stored["retry_count"], *c.RetryCountAfter)
			}
		})
	}
}

func toInt64(v interface{}) int64 {
	switch x := v.(type) {
	case int32:
		return int64(x)
	case int64:
		return x
	case float64:
		return int64(x)
	}
	return 0
}

// --- the rest of the lifecycle (live) ------------------------------------------

func claimOne(t *testing.T, db *mongo.Database, server string, extra bson.M) *TaskDocument {
	t.Helper()
	ctx := context.Background()
	tasks := db.Collection(CollectionTasks)
	_ = tasks.Drop(ctx)
	now := NowMillis()
	doc := bson.M{"uuid": "life", "name": "ns.Facet", "task_list_name": "ns", "state": "pending",
		"step_id": "s", "workflow_id": "w", "created": now, "updated": now}
	for k, v := range extra {
		doc[k] = v
	}
	if _, err := tasks.InsertOne(ctx, doc); err != nil {
		t.Fatal(err)
	}
	task, err := NewMongoOps(db).ClaimTaskFor(ctx, []string{"ns.Facet"}, []string{"ns"}, server, nil, false)
	if err != nil || task == nil {
		t.Fatalf("claim: %v %v", task, err)
	}
	return task
}

func storedLife(t *testing.T, db *mongo.Database) bson.M {
	t.Helper()
	var d bson.M
	if err := db.Collection(CollectionTasks).FindOne(context.Background(), bson.M{"uuid": "life"}).Decode(&d); err != nil {
		t.Fatal(err)
	}
	return d
}

func TestConformanceCompletionIsOwnershipGated(t *testing.T) {
	db := liveDB(t)
	ctx := context.Background()
	ops := NewMongoOps(db)
	task := claimOne(t, db, "me", nil)
	if ok, _ := ops.CompleteTask(ctx, task, "someone-else"); ok {
		t.Error("completed by a non-holder")
	}
	if storedLife(t, db)["state"] != "running" {
		t.Error("state changed by a non-holder")
	}
	if ok, err := ops.CompleteTask(ctx, task, "me"); !ok || err != nil {
		t.Fatalf("holder could not complete: %v", err)
	}
	if storedLife(t, db)["state"] != "completed" {
		t.Error("not completed")
	}
}

func TestConformanceFailureRetriesThenDeadLetters(t *testing.T) {
	db := liveDB(t)
	ctx := context.Background()
	ops := NewMongoOps(db)
	task := claimOne(t, db, "me", bson.M{"retry_count": 0, "max_retries": 2})
	first, err := ops.FailTask(ctx, task, "me", "boom", false)
	if err != nil || first == nil {
		t.Fatalf("fail: %v %v", first, err)
	}
	d := storedLife(t, db)
	if d["state"] != "pending" || d["server_id"] != "" || toInt64(d["retry_count"]) != 1 {
		t.Errorf("after first failure: %v", d)
	}
	if toInt64(d["next_retry_after"]) != first.NextRetryAfter {
		t.Errorf("next_retry_after %v, want %d", d["next_retry_after"], first.NextRetryAfter)
	}
	if d["error"].(bson.M)["message"] != "boom" {
		t.Errorf("error = %v", d["error"])
	}
	// Cooling down: not claimable yet.
	if got, _ := ops.ClaimTaskFor(ctx, []string{"ns.Facet"}, []string{"ns"}, "me", nil, false); got != nil {
		t.Error("claimed during cooldown")
	}
	_, _ = db.Collection(CollectionTasks).UpdateOne(ctx, bson.M{"uuid": "life"},
		bson.M{"$set": bson.M{"next_retry_after": 0}})
	again, err := ops.ClaimTaskFor(ctx, []string{"ns.Facet"}, []string{"ns"}, "me", nil, false)
	if err != nil || again == nil {
		t.Fatalf("re-claim: %v %v", again, err)
	}
	second, _ := ops.FailTask(ctx, again, "me", "boom", false)
	if second == nil || !second.DeadLettered() || storedLife(t, db)["state"] != "dead_letter" {
		t.Errorf("not dead-lettered at budget: %+v", second)
	}
}

func TestConformancePermanentErrorDeadLettersAtOnce(t *testing.T) {
	db := liveDB(t)
	ops := NewMongoOps(db)
	task := claimOne(t, db, "me", nil)
	err := NewPermanentError("bad input")
	got, _ := ops.FailTask(context.Background(), task, "me", err.Error(), IsPermanent(err))
	if got == nil || !got.DeadLettered() {
		t.Fatalf("not dead-lettered: %+v", got)
	}
	d := storedLife(t, db)
	if d["state"] != "dead_letter" || toInt64(d["retry_count"]) != 0 || d["error"].(bson.M)["permanent"] != true {
		t.Errorf("stored: %v", d)
	}
}

func TestConformanceFailureOnReclaimedTaskWritesNothing(t *testing.T) {
	db := liveDB(t)
	task := claimOne(t, db, "me", nil)
	got, err := NewMongoOps(db).FailTask(context.Background(), task, "not-me", "boom", false)
	if err != nil || got != nil {
		t.Errorf("non-holder failure recorded: %+v %v", got, err)
	}
	if storedLife(t, db)["state"] != "running" {
		t.Error("state changed by a non-holder")
	}
}

func TestConformanceLeaseRenewalAndCancellationFollowOwnership(t *testing.T) {
	db := liveDB(t)
	ctx := context.Background()
	ops := NewMongoOps(db)
	task := claimOne(t, db, "me", nil)
	before := toInt64(storedLife(t, db)["lease_expires"])
	if ok, err := ops.RenewLease(ctx, task.UUID, "me", 40, "halfway"); !ok || err != nil {
		t.Fatalf("renew: %v", err)
	}
	d := storedLife(t, db)
	if toInt64(d["lease_expires"]) < before || toInt64(d["progress_pct"]) != 40 {
		t.Errorf("after renew: %v", d)
	}
	if r, _ := ops.CancellationReason(ctx, task.UUID, "me"); r != "" {
		t.Errorf("unexpected cancellation: %s", r)
	}
	tasks := db.Collection(CollectionTasks)
	_, _ = tasks.UpdateOne(ctx, bson.M{"uuid": "life"}, bson.M{"$set": bson.M{"server_id": "other"}})
	if ok, _ := ops.RenewLease(ctx, task.UUID, "me", -1, ""); ok {
		t.Error("renewed a reclaimed task")
	}
	if r, _ := ops.CancellationReason(ctx, task.UUID, "me"); !strings.Contains(r, "reclaimed") {
		t.Errorf("reason = %q", r)
	}
	_, _ = tasks.UpdateOne(ctx, bson.M{"uuid": "life"}, bson.M{"$set": bson.M{"state": "canceled"}})
	if r, _ := ops.CancellationReason(ctx, task.UUID, "me"); !strings.Contains(r, "canceled") {
		t.Errorf("reason = %q", r)
	}
}

func TestConformanceHeartbeatReportsAPrunedRecord(t *testing.T) {
	db := liveDB(t)
	ctx := context.Background()
	servers := db.Collection(CollectionServers)
	reg := NewServerRegistration(db)
	cfg := DefaultConfig()
	if live, _ := reg.Ping(ctx, "srv"); live {
		t.Error("ping matched a missing record")
	}
	if err := reg.Register(ctx, "srv", cfg, []string{"ns.Facet"}); err != nil {
		t.Fatal(err)
	}
	var s bson.M
	_ = servers.FindOne(ctx, bson.M{"uuid": "srv"}).Decode(&s)
	if s["task_list"] != cfg.TaskList {
		t.Errorf("task_list = %v", s["task_list"])
	}
	if _, ok := s["resources"].(bson.M)["cpus"]; !ok {
		t.Errorf("resources = %v", s["resources"])
	}
	if live, _ := reg.Ping(ctx, "srv"); !live {
		t.Error("ping missed a live record")
	}
	_, _ = servers.UpdateOne(ctx, bson.M{"uuid": "srv"}, bson.M{"$set": bson.M{"state": "shutdown"}})
	if live, _ := reg.Ping(ctx, "srv"); live {
		t.Error("ping matched a shutdown record")
	}
	_, _ = servers.UpdateOne(ctx, bson.M{"uuid": "srv"}, bson.M{"$set": bson.M{"state": "quarantine"}})
	if live, _ := reg.Ping(ctx, "srv"); !live {
		t.Error("ping missed a quarantined record")
	}
	_ = servers.FindOne(ctx, bson.M{"uuid": "srv"}).Decode(&s)
	if s["state"] != "quarantine" {
		t.Errorf("quarantined record became %v", s["state"])
	}
}
