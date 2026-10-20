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

package afl.agent;

import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Measured capacity of this host, as the protocol's resource routing needs it.
 *
 * <p>Same rules as the Python runtime ({@code facetwork/runtime/base_runner.py}):
 * memory capacity is the container's cgroup limit where one is set (the number
 * the OOM killer enforces), else the machine total; memory offered when
 * CLAIMING is what is available now, capped by the cgroup's remaining headroom.
 * A dimension that cannot be measured is OMITTED, never guessed: an absent
 * dimension makes this server decline tasks that demand it, which is the safe
 * direction.
 */
public final class Resources {

    private static final double GIB = 1024.0 * 1024.0 * 1024.0;
    private static final long LIVE_TTL_MS = 5_000L;

    private static Map<String, Object> capacity;
    private static long liveAt;
    private static Double liveGb;

    private Resources() {
    }

    /** Static capacity, measured once per process: {memory_gb, cpus}. */
    public static synchronized Map<String, Object> capacity() {
        if (capacity == null) {
            Map<String, Object> res = new LinkedHashMap<>();
            int cpus = Runtime.getRuntime().availableProcessors();
            if (cpus > 0) {
                res.put("cpus", (double) cpus);
            }
            Long mem = cgroupLimit();
            if (mem == null) {
                mem = physicalTotal();
            }
            if (mem != null && mem > 0) {
                res.put("memory_gb", round(mem / GIB));
            }
            capacity = res;
        }
        return capacity;
    }

    /** Memory this process could get right now, in GB, or null if unreadable. */
    public static synchronized Double availableMemoryGb() {
        long now = System.currentTimeMillis();
        if (now - liveAt < LIVE_TTL_MS) {
            return liveGb;
        }
        Long avail = memAvailable();
        try {
            String lim = read("/sys/fs/cgroup/memory.max");
            String cur = read("/sys/fs/cgroup/memory.current");
            if (lim != null && cur != null && lim.matches("\\d+") && cur.matches("\\d+")) {
                long l = Long.parseLong(lim);
                if (l < (1L << 62)) {
                    long head = Math.max(0, l - Long.parseLong(cur));
                    avail = (avail == null) ? head : Math.min(avail, head);
                }
            }
        } catch (NumberFormatException e) {
            // keep what /proc/meminfo said
        }
        liveGb = (avail == null) ? null : round(avail / GIB);
        liveAt = now;
        return liveGb;
    }

    /** What to offer a claim: capacity with memory cut to what is free now. */
    public static Map<String, Object> claimResources() {
        Map<String, Object> res = new LinkedHashMap<>(capacity());
        if ("0".equals(System.getenv("FW_CLAIM_LIVE_MEMORY")) || !res.containsKey("memory_gb")) {
            return res;
        }
        Double live = availableMemoryGb();
        if (live != null) {
            res.put("memory_gb", Math.min((Double) res.get("memory_gb"), live));
        }
        return res;
    }

    /** What to publish on the heartbeat as free_resources (without "at"), or null. */
    public static Map<String, Object> liveFree() {
        Double gb = availableMemoryGb();
        if (gb == null) {
            return null;
        }
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("memory_gb", gb);
        return m;
    }

    private static Long cgroupLimit() {
        for (String p : new String[] {"/sys/fs/cgroup/memory.max",
                "/sys/fs/cgroup/memory/memory.limit_in_bytes"}) {
            String raw = read(p);
            if (raw == null) {
                continue;
            }
            if (raw.equals("max")) {
                return null;
            }
            try {
                long v = Long.parseLong(raw);
                return (v > 0 && v < (1L << 62)) ? v : null;
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    private static Long physicalTotal() {
        try {
            var os = ManagementFactory.getOperatingSystemMXBean();
            if (os instanceof com.sun.management.OperatingSystemMXBean sun) {
                return sun.getTotalMemorySize();
            }
        } catch (Throwable t) {
            // not measurable here
        }
        return null;
    }

    private static Long memAvailable() {
        String meminfo = read("/proc/meminfo");
        if (meminfo != null) {
            for (String line : meminfo.split("\\n")) {
                if (line.startsWith("MemAvailable")) {
                    String[] parts = line.trim().split("\\s+");
                    try {
                        return Long.parseLong(parts[1]) * 1024L;
                    } catch (RuntimeException e) {
                        return null;
                    }
                }
            }
        }
        // No /proc (macOS dev box): the JVM's view of free physical memory.
        try {
            var os = ManagementFactory.getOperatingSystemMXBean();
            if (os instanceof com.sun.management.OperatingSystemMXBean sun) {
                return sun.getFreeMemorySize();
            }
        } catch (Throwable t) {
            // not measurable here
        }
        return null;
    }

    private static String read(String path) {
        try {
            return Files.readString(Path.of(path)).trim();
        } catch (Exception e) {
            return null;
        }
    }

    private static double round(double gb) {
        return Math.round(gb * 100.0) / 100.0;
    }
}
