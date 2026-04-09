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
import * as path from "path";
import * as os from "os";

/**
 * Configuration for AgentPoller.
 */
export interface AgentPollerConfig {
  /** Service identifier for server registration */
  serviceName: string;
  /** Logical group name */
  serverGroup: string;
  /** Hostname (defaults to os.hostname()) */
  serverName: string;
  /** Task list name for routing */
  taskList: string;
  /** Polling interval in milliseconds */
  pollIntervalMs: number;
  /** Maximum number of concurrent event handlers */
  maxConcurrent: number;
  /** Heartbeat interval in milliseconds */
  heartbeatIntervalMs: number;
  /** MongoDB connection string */
  mongoUrl: string;
  /** MongoDB database name */
  database: string;
}

/**
 * Returns a configuration with default values.
 */
export function defaultConfig(): AgentPollerConfig {
  return {
    serviceName: "fw-agent",
    serverGroup: "default",
    serverName: os.hostname() || "unknown",
    taskList: "default",
    pollIntervalMs: 2000,
    maxConcurrent: 5,
    heartbeatIntervalMs: 10000,
    mongoUrl: "mongodb://localhost:27017",
    database: "afl",
  };
}

interface RunnerSection {
  pollIntervalMs?: number;
  maxConcurrent?: number;
  heartbeatIntervalMs?: number;
  lockDurationMs?: number;
  sweepIntervalMs?: number;
  useRegistry?: boolean;
  topics?: string[];
}

interface AflConfigFile {
  mongodb?: {
    url?: string;
    database?: string;
  };
  runner?: RunnerSection;
}

/**
 * Loads configuration from a JSON file.
 */
export function loadConfig(filePath: string): AgentPollerConfig {
  const cfg = defaultConfig();

  try {
    const data = fs.readFileSync(filePath, "utf-8");
    const fileCfg: AflConfigFile = JSON.parse(data);

    if (fileCfg.mongodb?.url) {
      cfg.mongoUrl = fileCfg.mongodb.url;
    }
    if (fileCfg.mongodb?.database) {
      cfg.database = fileCfg.mongodb.database;
    }

    // Runner section
    if (fileCfg.runner) {
      if (fileCfg.runner.pollIntervalMs !== undefined) {
        cfg.pollIntervalMs = fileCfg.runner.pollIntervalMs;
      }
      if (fileCfg.runner.maxConcurrent !== undefined) {
        cfg.maxConcurrent = fileCfg.runner.maxConcurrent;
      }
      if (fileCfg.runner.heartbeatIntervalMs !== undefined) {
        cfg.heartbeatIntervalMs = fileCfg.runner.heartbeatIntervalMs;
      }
    }
  } catch {
    // Ignore file read/parse errors, use defaults
  }

  // AFL_ENV overlay
  const envName = process.env.AFL_ENV;
  if (envName) {
    const dir = path.dirname(filePath);
    const overlayPath = path.join(dir, `afl.config.${envName}.json`);
    try {
      const overlayData = fs.readFileSync(overlayPath, "utf-8");
      const overlay: AflConfigFile = JSON.parse(overlayData);
      if (overlay.mongodb?.url) cfg.mongoUrl = overlay.mongodb.url;
      if (overlay.mongodb?.database) cfg.database = overlay.mongodb.database;
      if (overlay.runner?.pollIntervalMs !== undefined)
        cfg.pollIntervalMs = overlay.runner.pollIntervalMs;
      if (overlay.runner?.maxConcurrent !== undefined)
        cfg.maxConcurrent = overlay.runner.maxConcurrent;
      if (overlay.runner?.heartbeatIntervalMs !== undefined)
        cfg.heartbeatIntervalMs = overlay.runner.heartbeatIntervalMs;
    } catch {
      // Overlay file not found or invalid — ignore
    }
  }

  applyEnvOverrides(cfg);
  return cfg;
}

/**
 * Resolves configuration using the standard search order:
 * 1. Explicit path argument
 * 2. AFL_CONFIG environment variable
 * 3. afl.config.json in current directory
 * 4. ~/.afl/afl.config.json
 * 5. /etc/afl/afl.config.json
 * 6. Environment variables
 * 7. Built-in defaults
 */
export function resolveConfig(explicitPath?: string): AgentPollerConfig {
  if (explicitPath) {
    try {
      return loadConfig(explicitPath);
    } catch {
      // Fall through to other options
    }
  }

  const envPath = process.env.AFL_CONFIG;
  if (envPath) {
    try {
      return loadConfig(envPath);
    } catch {
      // Fall through
    }
  }

  const searchPaths = [
    "afl.config.json",
    path.join(os.homedir(), ".afl", "afl.config.json"),
    "/etc/afl/afl.config.json",
  ];

  for (const searchPath of searchPaths) {
    if (fs.existsSync(searchPath)) {
      try {
        return loadConfig(searchPath);
      } catch {
        // Continue to next path
      }
    }
  }

  return fromEnvironment();
}

/**
 * Creates a configuration from environment variables.
 */
export function fromEnvironment(): AgentPollerConfig {
  const cfg = defaultConfig();
  applyEnvOverrides(cfg);
  return cfg;
}

function applyEnvOverrides(cfg: AgentPollerConfig): void {
  const mongoUrl = process.env.AFL_MONGODB_URL;
  if (mongoUrl) {
    cfg.mongoUrl = mongoUrl;
  }

  const database = process.env.AFL_MONGODB_DATABASE;
  if (database) {
    cfg.database = database;
  }

  // Runner env overrides
  const pollInterval = process.env.AFL_POLL_INTERVAL_MS;
  if (pollInterval) {
    cfg.pollIntervalMs = parseInt(pollInterval, 10);
  }
  const maxConcurrent = process.env.AFL_MAX_CONCURRENT;
  if (maxConcurrent) {
    cfg.maxConcurrent = parseInt(maxConcurrent, 10);
  }
  const heartbeatInterval = process.env.AFL_HEARTBEAT_INTERVAL_MS;
  if (heartbeatInterval) {
    cfg.heartbeatIntervalMs = parseInt(heartbeatInterval, 10);
  }
}
