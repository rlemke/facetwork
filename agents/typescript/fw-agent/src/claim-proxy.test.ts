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

// The wire contract with facetwork.runtime.claimer (POST /next, POST /kick).

import http from "http";
import { AddressInfo } from "net";
import { ClaimProxy } from "./claim-proxy";

let server: http.Server;
let lastBody: Record<string, any> = {};
let status = 200;
let reply = '{"task": null}';
let kicks = 0;

beforeEach(async () => {
  status = 200;
  reply = '{"task": null}';
  kicks = 0;
  server = http.createServer((req, res) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      if (req.url === "/kick") {
        kicks++;
        res.end("{}");
        return;
      }
      lastBody = JSON.parse(data || "{}");
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(reply);
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
});

afterEach(async () => {
  await new Promise<void>((r) => server.close(() => r()));
});

const url = () => `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;

test("sends the same spec a direct claim uses", async () => {
  reply = '{"task": "t-1"}';
  const got = await new ClaimProxy(url()).next(
    "srv-1", ["ns.Facet"], ["ns"], { memory_gb: 6.5 }, 5, true
  );
  expect(got).toBe("t-1");
  expect(lastBody.server_id).toBe("srv-1");
  expect(lastBody.claims).toEqual([[["ns.Facet"], ["ns"]]]);
  // an agent runs no evaluator: no FFL features, no script environments
  expect(lastBody.known_features).toEqual([]);
  expect(lastBody.script_environments).toEqual([]);
  expect(lastBody.resources).toEqual({ memory_gb: 6.5 });
  expect(lastBody.wait_s).toBe(5);
  expect(lastBody.kick).toBe(true);
});

test("no task is null", async () => {
  expect(await new ClaimProxy(url()).next("s", ["a.B"], ["a"], {}, 0, false)).toBeNull();
});

test("an error status rejects so the caller claims directly", async () => {
  status = 400;
  reply = '{"error": "server_id and claims are required"}';
  await expect(new ClaimProxy(url()).next("s", ["a.B"], ["a"], {}, 0, false)).rejects.toThrow("HTTP 400");
});

test("an unreachable claimer rejects", async () => {
  // discard: nothing listens
  await expect(new ClaimProxy("http://127.0.0.1:9").next("s", ["a.B"], ["a"], {}, 0, false)).rejects.toThrow();
});

test("kick reaches the claimer", async () => {
  new ClaimProxy(url()).kick();
  for (let i = 0; i < 50 && kicks === 0; i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
  expect(kicks).toBe(1);
});

test("unset environment means claim directly", () => {
  expect(ClaimProxy.fromEnv({})).toBeNull();
  expect(ClaimProxy.fromEnv({ FW_CLAIM_PROXY_URL: " http://c:8790/ " })!.baseUrl).toBe("http://c:8790");
});
