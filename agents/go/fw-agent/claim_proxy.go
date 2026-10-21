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
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"net/http"
	"os"
	"strings"
	"time"
)

// ClaimProxyEnv names the host's claimer, e.g. http://runner-claimer:8790.
const ClaimProxyEnv = "FW_CLAIM_PROXY_URL"

// ClaimProxy is a client for the host's claimer (facetwork.runtime.claimer).
//
// The claimer polls MongoDB once for every runner on its machine and hands
// each task over with an ownership-gated write, so a task it returns is
// already running under this agent's server id. The agent sends the same spec
// its direct claim uses: handler names and task list, the memory free now, and
// no FFL features or script environments (an agent declines both).
type ClaimProxy struct {
	baseURL string
	client  *http.Client
}

// NewClaimProxy returns a client for the claimer at baseURL.
func NewClaimProxy(baseURL string) *ClaimProxy {
	return &ClaimProxy{baseURL: strings.TrimRight(baseURL, "/"), client: &http.Client{}}
}

// ClaimProxyFromEnv returns the claimer named by FW_CLAIM_PROXY_URL, or nil
// when it is unset.
func ClaimProxyFromEnv() *ClaimProxy {
	url := strings.TrimSpace(os.Getenv(ClaimProxyEnv))
	if url == "" {
		return nil
	}
	return NewClaimProxy(url)
}

// BaseURL is the claimer's address.
func (c *ClaimProxy) BaseURL() string { return c.baseURL }

// Next asks for one task, waiting up to waitS seconds for one to appear. kick
// says work just finished here, so follow-on tasks may be waiting. It returns
// the uuid of a task now held by serverID, or "" for none. An error means the
// claimer could not be reached or answered badly: claim directly instead.
func (c *ClaimProxy) Next(ctx context.Context, serverID string, taskNames, taskLists []string,
	resources map[string]interface{}, waitS float64, kick bool) (string, error) {
	if resources == nil {
		resources = map[string]interface{}{}
	}
	body, err := json.Marshal(map[string]interface{}{
		"server_id":           serverID,
		"claims":              []interface{}{[]interface{}{taskNames, taskLists}},
		"known_features":      []string{},
		"resources":           resources,
		"script_environments": []string{},
		"wait_s":              waitS,
		"kick":                kick,
	})
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration((waitS+30)*float64(time.Second)))
	defer cancel()
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/next", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req = req.WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	data, err := ioutil.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("claimer answered HTTP %d: %s", resp.StatusCode, data)
	}
	var out struct {
		Task *string `json:"task"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		return "", fmt.Errorf("claimer reply is not JSON: %v", err)
	}
	if out.Task == nil {
		return "", nil
	}
	return *out.Task, nil
}

// Kick tells the claimer work may be waiting. Fire-and-forget: a lost kick
// costs latency only.
func (c *ClaimProxy) Kick() {
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		req, err := http.NewRequest(http.MethodPost, c.baseURL+"/kick", strings.NewReader("{}"))
		if err != nil {
			return
		}
		if resp, err := c.client.Do(req.WithContext(ctx)); err == nil {
			resp.Body.Close()
		}
	}()
}
