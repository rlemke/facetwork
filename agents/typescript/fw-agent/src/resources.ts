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

import * as fs from "fs";
import * as os from "os";

/**
 * Measured capacity of this host, as the protocol's resource routing needs it.
 *
 * Same rules as the Python runtime (facetwork/runtime/base_runner.py): memory
 * capacity is the container's cgroup limit where one is set (the number the
 * OOM killer enforces), else the machine total; memory offered when CLAIMING
 * is what is available now, capped by the cgroup's remaining headroom. A
 * dimension that cannot be measured is OMITTED, never guessed: an absent
 * dimension makes this server decline tasks that demand it, the safe direction.
 */

const GIB = 1024 * 1024 * 1024;
const LIVE_TTL_MS = 5_000;

let capacityCache: Record<string, number> | null = null;
let liveAt = 0;
let liveGb: number | null = null;

function read(path: string): string | null {
  try {
    return fs.readFileSync(path, "utf8").trim();
  } catch {
    return null;
  }
}

function round(gb: number): number {
  return Math.round(gb * 100) / 100;
}

function cgroupLimit(): number | null {
  for (const p of ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"]) {
    const raw = read(p);
    if (raw === null) continue;
    if (raw === "max") return null;
    const v = Number(raw);
    return Number.isFinite(v) && v > 0 && v < 2 ** 62 ? v : null;
  }
  return null;
}

function memAvailable(): number | null {
  const meminfo = read("/proc/meminfo");
  if (meminfo !== null) {
    for (const line of meminfo.split("\n")) {
      if (line.startsWith("MemAvailable")) {
        const kb = Number(line.trim().split(/\s+/)[1]);
        return Number.isFinite(kb) ? kb * 1024 : null;
      }
    }
  }
  // No /proc (macOS dev box): the OS view of free memory.
  const free = os.freemem();
  return free > 0 ? free : null;
}

/** Static capacity, measured once per process: { cpus, memory_gb }. */
export function capacity(): Record<string, number> {
  if (capacityCache === null) {
    const res: Record<string, number> = {};
    const cpus = os.cpus().length;
    if (cpus > 0) res.cpus = cpus;
    const mem = cgroupLimit() ?? os.totalmem();
    if (mem && mem > 0) res.memory_gb = round(mem / GIB);
    capacityCache = res;
  }
  return capacityCache;
}

/** Memory this process could get right now, in GB, or null if unreadable. */
export function availableMemoryGb(): number | null {
  const now = Date.now();
  if (now - liveAt < LIVE_TTL_MS) {
    return liveGb;
  }
  let avail = memAvailable();
  const lim = read("/sys/fs/cgroup/memory.max");
  const cur = read("/sys/fs/cgroup/memory.current");
  if (lim && cur && /^\d+$/.test(lim) && /^\d+$/.test(cur)) {
    const l = Number(lim);
    if (l < 2 ** 62) {
      const head = Math.max(0, l - Number(cur));
      avail = avail === null ? head : Math.min(avail, head);
    }
  }
  liveGb = avail === null ? null : round(avail / GIB);
  liveAt = now;
  return liveGb;
}

/** What to offer a claim: capacity with memory cut to what is free now. */
export function claimResources(): Record<string, number> {
  const res = { ...capacity() };
  if (process.env.FW_CLAIM_LIVE_MEMORY === "0" || res.memory_gb === undefined) {
    return res;
  }
  const live = availableMemoryGb();
  if (live !== null) {
    res.memory_gb = Math.min(res.memory_gb, live);
  }
  return res;
}

/** What to publish on the heartbeat as free_resources (without "at"), or null. */
export function liveFree(): Record<string, number> | null {
  const gb = availableMemoryGb();
  return gb === null ? null : { memory_gb: gb };
}
