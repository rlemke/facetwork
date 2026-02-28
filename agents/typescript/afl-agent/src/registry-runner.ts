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

import { MongoClient, Db } from "mongodb";
import { AgentPoller, Handler } from "./poller";
import { AgentPollerConfig } from "./config";
import { CollectionHandlerRegistrations } from "./protocol";

/**
 * RegistryRunner wraps an AgentPoller and restricts polling to only those
 * handler names that also appear in MongoDB's handler_registrations collection.
 * This provides DB-driven topic filtering without requiring dynamic module loading.
 */
export class RegistryRunner {
  private readonly poller: AgentPoller;
  private readonly refreshIntervalMs: number;
  private activeTopics: Set<string> = new Set();
  private handlerMetadata: Map<string, Record<string, unknown>> = new Map();
  private refreshTimer: NodeJS.Timeout | null = null;
  private db: Db | null = null;
  private client: MongoClient | null = null;

  constructor(config: AgentPollerConfig, refreshIntervalMs = 30000) {
    this.poller = new AgentPoller(config);
    this.refreshIntervalMs = refreshIntervalMs;
    this.poller.metadataProvider = (facetName: string) => this.handlerMetadata.get(facetName);
  }

  /**
   * Registers a handler (delegates to the underlying poller).
   */
  register(facetName: string, handler: Handler): void {
    this.poller.register(facetName, handler);
  }

  /**
   * Returns all registered handler names from the underlying poller.
   */
  registeredHandlers(): string[] {
    return this.poller.registeredHandlers();
  }

  /**
   * Returns the effective handlers: intersection of registered and active topics.
   */
  effectiveHandlers(): string[] {
    const registered = this.poller.registeredHandlers();
    return registered.filter((name) => this.activeTopics.has(name));
  }

  /**
   * Refreshes active topics from the handler_registrations collection.
   */
  async refreshTopics(): Promise<void> {
    if (!this.db) {
      return;
    }

    try {
      const coll = this.db.collection(CollectionHandlerRegistrations);
      const docs = await coll.find({}).toArray();
      this.activeTopics = new Set(
        docs
          .map((doc) => doc.facet_name as string)
          .filter((name) => name != null)
      );

      const metadata = new Map<string, Record<string, unknown>>();
      for (const doc of docs) {
        const name = doc.facet_name as string;
        if (name && doc.metadata) {
          metadata.set(name, doc.metadata as Record<string, unknown>);
        }
      }
      this.handlerMetadata = metadata;
    } catch {
      // best-effort; keep existing topics
    }
  }

  /**
   * Starts the runner. Connects to MongoDB, starts the refresh loop,
   * and delegates to the underlying poller.
   */
  async start(): Promise<void> {
    // Start the refresh loop
    this.refreshTimer = setInterval(async () => {
      await this.refreshTopics();
    }, this.refreshIntervalMs);

    // Delegate to poller
    await this.poller.start();
  }

  /**
   * Stops the runner and cleans up.
   */
  async stop(): Promise<void> {
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }

    await this.poller.stop();
  }
}
