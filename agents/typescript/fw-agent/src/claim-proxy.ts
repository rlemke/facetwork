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

/** Env var naming the host's claimer, e.g. `http://runner-claimer:8790`. */
export const ClaimProxyEnv = "FW_CLAIM_PROXY_URL";

/**
 * Client for the host's claimer (`facetwork.runtime.claimer`).
 *
 * The claimer polls MongoDB once for every runner on its machine and hands
 * each task over with an ownership-gated write, so a task it returns is
 * already `running` under this agent's server id. The agent sends the same
 * spec its direct claim uses: handler names and task list, the memory free
 * now, and no FFL features or script environments (an agent declines both).
 */
export class ClaimProxy {
  readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  /** The claimer named by `FW_CLAIM_PROXY_URL`, or null when it is unset. */
  static fromEnv(env: Record<string, string | undefined> = process.env): ClaimProxy | null {
    const url = (env[ClaimProxyEnv] ?? "").trim();
    return url ? new ClaimProxy(url) : null;
  }

  /**
   * Asks for one task, waiting up to `waitS` seconds for one to appear.
   * `kick` says work just finished here, so follow-on tasks may be waiting.
   * Resolves to the uuid of a task now held by `serverId`, or null. Rejects
   * when the claimer cannot be reached or answers badly: claim directly then.
   */
  async next(
    serverId: string,
    taskNames: string[],
    taskLists: string[],
    resources: Record<string, unknown>,
    waitS: number,
    kick: boolean
  ): Promise<string | null> {
    const resp = await fetch(`${this.baseUrl}/next`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        server_id: serverId,
        claims: [[taskNames, taskLists]],
        known_features: [],
        resources: resources ?? {},
        script_environments: [],
        wait_s: waitS,
        kick,
      }),
      signal: AbortSignal.timeout((waitS + 30) * 1000),
    });
    const text = await resp.text();
    if (!resp.ok) {
      throw new Error(`claimer answered HTTP ${resp.status}: ${text}`);
    }
    const task = (JSON.parse(text) as { task?: string | null }).task;
    return task ? task : null;
  }

  /** Tells the claimer work may be waiting. Fire-and-forget: a lost kick costs latency only. */
  kick(): void {
    fetch(`${this.baseUrl}/kick`, {
      method: "POST",
      body: "{}",
      signal: AbortSignal.timeout(2000),
    }).catch(() => undefined);
  }
}
