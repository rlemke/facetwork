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

import { AgentPoller, Handler } from "./poller";
import { defaultConfig } from "./config";
import {
  CollectionTasks,
  CollectionSteps,
  CollectionServers,
  CollectionStepLogs,
  TaskStatePending,
  TaskStateRunning,
  TaskStateCompleted,
  ResumeTaskName,
  StepLogLevelInfo,
  StepLogLevelWarning,
  StepLogLevelError,
  StepLogLevelSuccess,
  StepLogSourceFramework,
  StepLogSourceHandler,
} from "./protocol";
import { inferTypeHint } from "./models";

describe("AgentPoller", () => {
  it("should create with default config", () => {
    const config = defaultConfig();
    const poller = new AgentPoller(config);

    expect(poller).toBeDefined();
    expect(poller.registeredHandlers()).toHaveLength(0);
  });

  it("should register handlers", () => {
    const config = defaultConfig();
    const poller = new AgentPoller(config);

    const handler: Handler = async (params) => {
      return { result: "ok" };
    };

    poller.register("ns.TestFacet", handler);

    const handlers = poller.registeredHandlers();
    expect(handlers).toHaveLength(1);
    expect(handlers[0]).toBe("ns.TestFacet");
  });

  it("should register multiple handlers", () => {
    const config = defaultConfig();
    const poller = new AgentPoller(config);

    poller.register("ns.FacetA", async () => ({}));
    poller.register("ns.FacetB", async () => ({}));
    poller.register("FacetC", async () => ({}));

    const handlers = poller.registeredHandlers();
    expect(handlers).toHaveLength(3);
    expect(handlers).toContain("ns.FacetA");
    expect(handlers).toContain("ns.FacetB");
    expect(handlers).toContain("FacetC");
  });
});

describe("defaultConfig", () => {
  it("should return expected defaults", () => {
    const config = defaultConfig();

    expect(config.serviceName).toBe("afl-agent");
    expect(config.serverGroup).toBe("default");
    expect(config.taskList).toBe("default");
    expect(config.maxConcurrent).toBe(5);
    expect(config.pollIntervalMs).toBe(2000);
    expect(config.heartbeatIntervalMs).toBe(10000);
    expect(config.mongoUrl).toBe("mongodb://localhost:27017");
    expect(config.database).toBe("afl");
  });
});

describe("Protocol constants", () => {
  it("should have correct collection names", () => {
    expect(CollectionTasks).toBe("tasks");
    expect(CollectionSteps).toBe("steps");
    expect(CollectionServers).toBe("servers");
  });

  it("should have correct task states", () => {
    expect(TaskStatePending).toBe("pending");
    expect(TaskStateRunning).toBe("running");
    expect(TaskStateCompleted).toBe("completed");
  });

  it("should have correct protocol task name", () => {
    expect(ResumeTaskName).toBe("afl:resume");
  });
});

describe("Step log constants", () => {
  it("should have correct step log levels", () => {
    expect(StepLogLevelInfo).toBe("info");
    expect(StepLogLevelWarning).toBe("warning");
    expect(StepLogLevelError).toBe("error");
    expect(StepLogLevelSuccess).toBe("success");
  });

  it("should have correct step log sources", () => {
    expect(StepLogSourceFramework).toBe("framework");
    expect(StepLogSourceHandler).toBe("handler");
  });

  it("should have correct step_logs collection", () => {
    expect(CollectionStepLogs).toBe("step_logs");
  });
});

describe("inferTypeHint", () => {
  it("should infer Boolean", () => {
    expect(inferTypeHint(true)).toBe("Boolean");
    expect(inferTypeHint(false)).toBe("Boolean");
  });

  it("should infer Long for integers", () => {
    expect(inferTypeHint(42)).toBe("Long");
    expect(inferTypeHint(0)).toBe("Long");
    expect(inferTypeHint(-100)).toBe("Long");
  });

  it("should infer Double for floats", () => {
    expect(inferTypeHint(3.14)).toBe("Double");
    expect(inferTypeHint(0.5)).toBe("Double");
  });

  it("should infer String", () => {
    expect(inferTypeHint("hello")).toBe("String");
    expect(inferTypeHint("")).toBe("String");
  });

  it("should infer List for arrays", () => {
    expect(inferTypeHint([1, 2, 3])).toBe("List");
    expect(inferTypeHint([])).toBe("List");
  });

  it("should infer Map for objects", () => {
    expect(inferTypeHint({ a: 1 })).toBe("Map");
    expect(inferTypeHint({})).toBe("Map");
  });

  it("should infer Any for null/undefined", () => {
    expect(inferTypeHint(null)).toBe("Any");
    expect(inferTypeHint(undefined)).toBe("Any");
  });
});
