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

/**
 * This library against the protocol's shared conformance cases
 * (agents/protocol/conformance). The Python runtime runs the same files in
 * tests/runtime/test_agent_protocol_conformance.py, so a rule exists once.
 *
 * The retry cases are pure. The claim and lifecycle cases need a real MongoDB:
 * set FW_AGENT_TEST_MONGO_URL (e.g. a throwaway mongo:7 container); they are
 * skipped otherwise. A scratch database is created and dropped.
 */

import * as fs from "fs";
import * as path from "path";
import { Db, MongoClient } from "mongodb";
import { MongoOps, failTransition } from "./mongo-ops";
import { ServerRegistration } from "./server-registration";
import { defaultConfig } from "./config";
import { DefaultMaxRetries, LeaseMs, LeaseReclaimScanMs, ProtocolVersion } from "./protocol";
import { TaskDocument } from "./models";

const CONF = path.resolve(__dirname, "../../../protocol/conformance");
const claimSpec = JSON.parse(fs.readFileSync(path.join(CONF, "claim_cases.json"), "utf8"));
const retrySpec = JSON.parse(fs.readFileSync(path.join(CONF, "retry_cases.json"), "utf8"));
const constants = JSON.parse(fs.readFileSync(path.join(CONF, "..", "constants.json"), "utf8"));

describe("retry cases", () => {
  for (const c of retrySpec.cases) {
    test(c.id, () => {
      const now = 1_000_000;
      const t = failTransition(c.retry_count, c.max_retries, c.permanent, now);
      expect(t.state).toBe(c.state);
      expect(t.retryCount).toBe(c.retry_count_after);
      if (c.backoff_ms !== undefined) {
        expect(t.nextRetryAfter - now).toBe(c.backoff_ms);
      }
    });
  }
});

test("spec version and timing match this library", () => {
  expect(constants.version).toBe(ProtocolVersion);
  expect(constants.timing.lease_ms).toBe(LeaseMs);
  expect(constants.timing.lease_reclaim_scan_ms).toBe(LeaseReclaimScanMs);
  expect(constants.timing.default_max_retries).toBe(DefaultMaxRetries);
});

const MONGO_URL = process.env.FW_AGENT_TEST_MONGO_URL;
const live = MONGO_URL ? describe : describe.skip;

function resolve(v: unknown, now: number): unknown {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    if (Object.keys(o).length === 1 && "rel_ms" in o) {
      return now + (o.rel_ms as number);
    }
    return Object.fromEntries(Object.entries(o).map(([k, x]) => [k, resolve(x, now)]));
  }
  return v;
}

live("against MongoDB", () => {
  let client: MongoClient;
  let db: Db;

  beforeAll(async () => {
    client = new MongoClient(MONGO_URL as string);
    await client.connect();
    db = client.db("fw_agent_conformance_ts_" + Math.random().toString(16).slice(2, 10));
  });

  afterAll(async () => {
    await db.dropDatabase();
    await client.close();
  });

  describe("claim cases", () => {
    const srv = claimSpec.server;
    for (const c of claimSpec.cases) {
      test(c.id, async () => {
        const tasks = db.collection(CollectionTasksName);
        await tasks.deleteMany({});
        const now = Date.now();
        const uuid = "t-" + c.id;
        const doc: Record<string, unknown> = {
          uuid, runner_id: "r", workflow_id: "w", flow_id: "f",
          step_id: "s-" + uuid, created: now, updated: now,
        };
        for (const [k, v] of Object.entries(c.task)) {
          doc[k] = resolve(v, now);
        }
        await tasks.insertOne(doc);

        const got = await new MongoOps(db).claimTask(
          srv.task_names, srv.task_lists, "conformance-server", srv.resources, true
        );
        expect(got !== null).toBe(c.claimed);
        if (got) {
          const stored = await tasks.findOne({ uuid });
          expect(stored!.state).toBe("running");
          expect(stored!.server_id).toBe("conformance-server");
          expect(stored!.lease_expires).toBeGreaterThan(now);
          if (c.retry_count_after !== undefined) {
            expect(stored!.retry_count).toBe(c.retry_count_after);
          }
        }
      });
    }
  });

  async function claimOne(ops: MongoOps, server: string, extra: Record<string, unknown> = {}): Promise<TaskDocument> {
    const tasks = db.collection(CollectionTasksName);
    await tasks.deleteMany({});
    const now = Date.now();
    await tasks.insertOne({
      uuid: "life", name: "ns.Facet", task_list_name: "ns", state: "pending",
      step_id: "s", workflow_id: "w", created: now, updated: now, ...extra,
    });
    const t = await ops.claimTask(["ns.Facet"], ["ns"], server, {}, false);
    expect(t).not.toBeNull();
    return t!;
  }

  const stored = () => db.collection(CollectionTasksName).findOne({ uuid: "life" });

  test("completion is ownership-gated", async () => {
    const ops = new MongoOps(db);
    const t = await claimOne(ops, "me");
    expect(await ops.completeTask(t, "someone-else")).toBe(false);
    expect((await stored())!.state).toBe("running");
    expect(await ops.completeTask(t, "me")).toBe(true);
    expect((await stored())!.state).toBe("completed");
  });

  test("failure retries with backoff, then dead-letters", async () => {
    const ops = new MongoOps(db);
    const t = await claimOne(ops, "me", { retry_count: 0, max_retries: 2 });
    const first = await ops.failTask(t, "me", "boom", false);
    expect(first).not.toBeNull();
    let d = (await stored())!;
    expect(d.state).toBe("pending");
    expect(d.server_id).toBe("");
    expect(d.retry_count).toBe(1);
    expect(d.next_retry_after).toBe(first!.nextRetryAfter);
    expect(d.error.message).toBe("boom");
    // Cooling down: not claimable yet.
    expect(await ops.claimTask(["ns.Facet"], ["ns"], "me", {}, false)).toBeNull();

    await db.collection(CollectionTasksName).updateOne({ uuid: "life" }, { $set: { next_retry_after: 0 } });
    const again = await ops.claimTask(["ns.Facet"], ["ns"], "me", {}, false);
    const second = await ops.failTask(again!, "me", "boom", false);
    expect(second!.state).toBe("dead_letter");
    d = (await stored())!;
    expect(d.state).toBe("dead_letter");
  });

  test("a permanent error dead-letters at once", async () => {
    const ops = new MongoOps(db);
    const t = await claimOne(ops, "me");
    expect((await ops.failTask(t, "me", "bad input", true))!.state).toBe("dead_letter");
    const d = (await stored())!;
    expect(d.state).toBe("dead_letter");
    expect(d.retry_count).toBe(0);
    expect(d.error.permanent).toBe(true);
  });

  test("a failure on a reclaimed task writes nothing", async () => {
    const ops = new MongoOps(db);
    const t = await claimOne(ops, "me");
    expect(await ops.failTask(t, "not-me", "boom", false)).toBeNull();
    expect((await stored())!.state).toBe("running");
  });

  test("lease renewal and cancellation follow ownership", async () => {
    const ops = new MongoOps(db);
    const t = await claimOne(ops, "me");
    const before = (await stored())!.lease_expires;
    expect(await ops.renewLease(t.uuid, "me", 40, "halfway")).toBe(true);
    const d = (await stored())!;
    expect(d.lease_expires).toBeGreaterThanOrEqual(before);
    expect(d.progress_pct).toBe(40);
    expect(await ops.cancellationReason(t.uuid, "me")).toBeNull();

    await db.collection(CollectionTasksName).updateOne({ uuid: "life" }, { $set: { server_id: "other" } });
    expect(await ops.renewLease(t.uuid, "me")).toBe(false);
    expect(await ops.cancellationReason(t.uuid, "me")).toContain("reclaimed");

    await db.collection(CollectionTasksName).updateOne({ uuid: "life" }, { $set: { state: "canceled" } });
    expect(await ops.cancellationReason(t.uuid, "me")).toContain("canceled");
  });

  test("heartbeat reports a pruned record and publishes free memory", async () => {
    const servers = db.collection("servers");
    await servers.deleteMany({});
    const reg = new ServerRegistration(db);
    const cfg = defaultConfig();
    expect(await reg.heartbeat("srv")).toBe(false); // no record yet
    await reg.register("srv", cfg, ["ns.Facet"]);
    let s = (await servers.findOne({ uuid: "srv" }))!;
    expect(s.resources.memory_gb).toBeGreaterThan(0);
    expect(s.task_list).toBe(cfg.taskList);
    expect(await reg.heartbeat("srv")).toBe(true);
    s = (await servers.findOne({ uuid: "srv" }))!;
    expect(s.free_resources.memory_gb).toBeDefined();

    await servers.updateOne({ uuid: "srv" }, { $set: { state: "shutdown" } });
    expect(await reg.heartbeat("srv")).toBe(false); // marked shutdown: re-register
    await servers.updateOne({ uuid: "srv" }, { $set: { state: "quarantine" } });
    expect(await reg.heartbeat("srv")).toBe(true); // still live, not resurrected
    expect((await servers.findOne({ uuid: "srv" }))!.state).toBe("quarantine");
  });
});

const CollectionTasksName = "tasks";
