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

import { Db } from "mongodb";
import * as os from "os";
import { CollectionServers, ServerStateRunning, ServerStateShutdown } from "./protocol";
import { AgentPollerConfig } from "./config";
import { ServerDocument, nowMillis } from "./models";

/**
 * Handles server lifecycle in MongoDB.
 */
export class ServerRegistration {
  constructor(private readonly db: Db) {}

  /**
   * Registers a server in the servers collection.
   */
  async register(
    serverId: string,
    config: AgentPollerConfig,
    handlers: string[]
  ): Promise<void> {
    const collection = this.db.collection<ServerDocument>(CollectionServers);

    const now = nowMillis();
    const server: ServerDocument = {
      uuid: serverId,
      server_group: config.serverGroup,
      service_name: config.serviceName,
      server_name: config.serverName,
      server_ips: getLocalIPs(),
      start_time: now,
      ping_time: now,
      topics: handlers,
      handlers: handlers,
      handled: null,
      state: ServerStateRunning,
    };

    await collection.updateOne(
      { uuid: serverId },
      { $set: server },
      { upsert: true }
    );
  }

  /**
   * Marks a server as shutdown.
   */
  async deregister(serverId: string): Promise<void> {
    const collection = this.db.collection<ServerDocument>(CollectionServers);

    await collection.updateOne(
      { uuid: serverId },
      {
        $set: {
          state: ServerStateShutdown,
          ping_time: nowMillis(),
        },
      }
    );
  }

  /**
   * Updates the server's ping time.
   */
  async heartbeat(serverId: string): Promise<void> {
    const collection = this.db.collection<ServerDocument>(CollectionServers);

    await collection.updateOne(
      { uuid: serverId },
      {
        $set: {
          ping_time: nowMillis(),
        },
      }
    );
  }
}

/**
 * Returns local non-loopback IPv4 addresses.
 */
function getLocalIPs(): string[] {
  const ips: string[] = [];
  const interfaces = os.networkInterfaces();

  for (const name of Object.keys(interfaces)) {
    const addrs = interfaces[name];
    if (!addrs) continue;

    for (const addr of addrs) {
      if (addr.family === "IPv4" && !addr.internal) {
        ips.push(addr.address);
      }
    }
  }

  return ips;
}
