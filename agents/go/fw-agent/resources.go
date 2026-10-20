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
	"io/ioutil"
	"math"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Measured capacity of this host, as the protocol's resource routing needs it.
//
// Same rules as the Python runtime (facetwork/runtime/base_runner.py): memory
// capacity is the container's cgroup limit where one is set (the number the
// OOM killer enforces), else the machine total; memory offered when CLAIMING
// is what is available now, capped by the cgroup's remaining headroom. A
// dimension that cannot be measured is OMITTED, never guessed: an absent
// dimension makes this server decline tasks that demand it, the safe direction.

const gib = 1024.0 * 1024.0 * 1024.0
const liveTTL = 5 * time.Second

var (
	resMu    sync.Mutex
	capacity map[string]interface{}
	liveAt   time.Time
	liveGB   *float64
)

// Capacity returns the static capacity, measured once per process:
// {memory_gb, cpus}.
func Capacity() map[string]interface{} {
	resMu.Lock()
	defer resMu.Unlock()
	if capacity == nil {
		res := map[string]interface{}{}
		if n := runtime.NumCPU(); n > 0 {
			res["cpus"] = float64(n)
		}
		mem, ok := cgroupLimit()
		if !ok {
			mem, ok = meminfoBytes("MemTotal")
		}
		if ok && mem > 0 {
			res["memory_gb"] = round2(float64(mem) / gib)
		}
		capacity = res
	}
	out := make(map[string]interface{}, len(capacity))
	for k, v := range capacity {
		out[k] = v
	}
	return out
}

// AvailableMemoryGB is the memory this process could get right now, in GB.
// ok is false when it cannot be read (e.g. no /proc on a macOS dev box).
func AvailableMemoryGB() (float64, bool) {
	resMu.Lock()
	defer resMu.Unlock()
	if time.Since(liveAt) < liveTTL {
		if liveGB == nil {
			return 0, false
		}
		return *liveGB, true
	}
	avail, ok := meminfoBytes("MemAvailable")
	lim, lerr := readTrim("/sys/fs/cgroup/memory.max")
	cur, cerr := readTrim("/sys/fs/cgroup/memory.current")
	if lerr == nil && cerr == nil {
		l, e1 := strconv.ParseInt(lim, 10, 64)
		c, e2 := strconv.ParseInt(cur, 10, 64)
		if e1 == nil && e2 == nil && l < (int64(1)<<62) {
			head := l - c
			if head < 0 {
				head = 0
			}
			if !ok || head < avail {
				avail, ok = head, true
			}
		}
	}
	liveAt = time.Now()
	if !ok {
		liveGB = nil
		return 0, false
	}
	gb := round2(float64(avail) / gib)
	liveGB = &gb
	return gb, true
}

// ClaimResources is what to offer a claim: capacity with memory_gb cut to
// what is free now. FW_CLAIM_LIVE_MEMORY=0 offers capacity only.
func ClaimResources() map[string]interface{} {
	res := Capacity()
	capGB, has := res["memory_gb"].(float64)
	if os.Getenv("FW_CLAIM_LIVE_MEMORY") == "0" || !has {
		return res
	}
	if live, ok := AvailableMemoryGB(); ok {
		res["memory_gb"] = math.Min(capGB, live)
	}
	return res
}

// LiveFree is what to publish on the heartbeat as free_resources (without
// "at"), or nil when memory cannot be read.
func LiveFree() map[string]interface{} {
	gb, ok := AvailableMemoryGB()
	if !ok {
		return nil
	}
	return map[string]interface{}{"memory_gb": gb}
}

func cgroupLimit() (int64, bool) {
	for _, p := range []string{"/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"} {
		raw, err := readTrim(p)
		if err != nil {
			continue
		}
		if raw == "max" {
			return 0, false
		}
		v, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || v <= 0 || v >= (int64(1)<<62) {
			return 0, false
		}
		return v, true
	}
	return 0, false
}

func meminfoBytes(key string) (int64, bool) {
	raw, err := readTrim("/proc/meminfo")
	if err != nil {
		return 0, false
	}
	for _, line := range strings.Split(raw, "\n") {
		if strings.HasPrefix(line, key+":") {
			f := strings.Fields(line)
			if len(f) < 2 {
				return 0, false
			}
			kb, err := strconv.ParseInt(f[1], 10, 64)
			if err != nil {
				return 0, false
			}
			return kb * 1024, true
		}
	}
	return 0, false
}

func readTrim(path string) (string, error) {
	b, err := ioutil.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(b)), nil
}

func round2(v float64) float64 { return math.Round(v*100) / 100 }
