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
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// The wire contract with facetwork.runtime.claimer (POST /next, POST /kick).

func fakeClaimer(t *testing.T, status int, reply string, body *map[string]interface{}, kicked chan struct{}) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/next", func(w http.ResponseWriter, r *http.Request) {
		if body != nil {
			json.NewDecoder(r.Body).Decode(body)
		}
		w.WriteHeader(status)
		w.Write([]byte(reply))
	})
	mux.HandleFunc("/kick", func(w http.ResponseWriter, r *http.Request) {
		if kicked != nil {
			kicked <- struct{}{}
		}
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func TestClaimProxySendsTheSameSpecADirectClaimUses(t *testing.T) {
	var body map[string]interface{}
	srv := fakeClaimer(t, 200, `{"task": "t-1"}`, &body, nil)
	got, err := NewClaimProxy(srv.URL+"/").Next(context.Background(), "srv-1",
		[]string{"ns.Facet"}, []string{"ns"}, map[string]interface{}{"memory_gb": 6.5}, 5, true)
	if err != nil || got != "t-1" {
		t.Fatalf("got %q, %v", got, err)
	}
	claims := body["claims"].([]interface{})[0].([]interface{})
	if body["server_id"] != "srv-1" || claims[0].([]interface{})[0] != "ns.Facet" || claims[1].([]interface{})[0] != "ns" {
		t.Errorf("spec %v", body)
	}
	// an agent runs no evaluator: no FFL features, no script environments
	if len(body["known_features"].([]interface{})) != 0 || len(body["script_environments"].([]interface{})) != 0 {
		t.Errorf("an agent must not advertise features or environments: %v", body)
	}
	if body["resources"].(map[string]interface{})["memory_gb"] != 6.5 || body["wait_s"] != 5.0 || body["kick"] != true {
		t.Errorf("spec %v", body)
	}
}

func TestClaimProxyNoTaskIsEmpty(t *testing.T) {
	srv := fakeClaimer(t, 200, `{"task": null}`, nil, nil)
	got, err := NewClaimProxy(srv.URL).Next(context.Background(), "s", []string{"a.B"}, []string{"a"}, nil, 0, false)
	if err != nil || got != "" {
		t.Fatalf("got %q, %v", got, err)
	}
}

func TestClaimProxyErrorStatusIsAnError(t *testing.T) {
	srv := fakeClaimer(t, 400, `{"error": "server_id and claims are required"}`, nil, nil)
	if _, err := NewClaimProxy(srv.URL).Next(context.Background(), "s", []string{"a.B"}, []string{"a"}, nil, 0, false); err == nil {
		t.Fatal("want an error so the caller claims directly")
	}
}

func TestClaimProxyUnreachableIsAnError(t *testing.T) {
	// discard: nothing listens
	if _, err := NewClaimProxy("http://127.0.0.1:9").Next(context.Background(), "s", []string{"a.B"}, []string{"a"}, nil, 0, false); err == nil {
		t.Fatal("want an error so the caller claims directly")
	}
}

func TestClaimProxyKickReachesTheClaimer(t *testing.T) {
	kicked := make(chan struct{}, 1)
	srv := fakeClaimer(t, 200, `{}`, nil, kicked)
	NewClaimProxy(srv.URL).Kick()
	select {
	case <-kicked:
	case <-time.After(5 * time.Second):
		t.Fatal("kick never arrived")
	}
}

func TestPollerFallsBackWhenTheClaimerIsDown(t *testing.T) {
	p := NewAgentPoller(DefaultConfig())
	p.SetClaimProxy(NewClaimProxy("http://127.0.0.1:9"))
	p.ops = nil // a direct claim would need MongoDB: only check the skip window
	before := NowMillis()
	func() {
		defer func() { recover() }() // the direct claim then hits the nil ops
		p.claimNext(context.Background(), []string{"a.B"})
	}()
	if p.proxyDownUntil < before+ClaimProxyRetryMS-1000 {
		t.Errorf("an unreachable claimer must be skipped for ClaimProxyRetryMS, got %d", p.proxyDownUntil-before)
	}
}
