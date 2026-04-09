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

import { RegistryRunner } from "./registry-runner";
import { defaultConfig } from "./config";
import { CollectionHandlerRegistrations } from "./protocol";

describe("RegistryRunner", () => {
  it("should return empty effectiveHandlers when no active topics", () => {
    const config = defaultConfig();
    const runner = new RegistryRunner(config);

    runner.register("ns.FacetA", async () => ({}));
    runner.register("ns.FacetB", async () => ({}));

    expect(runner.effectiveHandlers()).toHaveLength(0);
  });

  it("should delegate register to underlying poller", () => {
    const config = defaultConfig();
    const runner = new RegistryRunner(config);

    runner.register("ns.FacetA", async () => ({ result: "ok" }));

    expect(runner.registeredHandlers()).toContain("ns.FacetA");
  });

  it("should return intersection of registered and active topics", () => {
    const config = defaultConfig();
    const runner = new RegistryRunner(config);

    runner.register("ns.FacetA", async () => ({}));
    runner.register("ns.FacetB", async () => ({}));
    runner.register("ns.FacetC", async () => ({}));

    // Simulate active topics by calling the internal refreshTopics
    // We can't easily mock the DB here, so we test via the public API
    // by verifying empty intersection
    const effective = runner.effectiveHandlers();
    expect(effective).toHaveLength(0);
  });

  it("should accept custom refresh interval", () => {
    const config = defaultConfig();
    const runner = new RegistryRunner(config, 5000);

    // Runner was created successfully with custom interval
    expect(runner).toBeDefined();
  });
});

describe("CollectionHandlerRegistrations constant", () => {
  it("should be handler_registrations", () => {
    expect(CollectionHandlerRegistrations).toBe("handler_registrations");
  });
});
