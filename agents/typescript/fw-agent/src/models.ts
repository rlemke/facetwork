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
 * Document types matching MongoDB collections.
 */

export interface TaskDocument {
  uuid: string;
  name: string;
  runner_id: string;
  workflow_id: string;
  flow_id: string;
  step_id: string;
  state: string;
  created: number;
  updated: number;
  error?: { message: string };
  task_list_name: string;
  data_type?: string;
  data?: Record<string, unknown>;
}

export interface StepAttribute {
  name: string;
  value: unknown;
  type_hint?: string;
}

export interface StepAttributes {
  params?: Record<string, StepAttribute>;
  returns?: Record<string, StepAttribute>;
}

export interface StepDocument {
  uuid: string;
  workflow_id: string;
  object_type: string;
  state: string;
  statement_id: string;
  container_id: string;
  block_id: string;
  facet_name?: string;
  attributes?: StepAttributes;
}

export interface HandledStat {
  handler: string;
  handled: number;
  not_handled: number;
}

export interface ServerDocument {
  uuid: string;
  server_group: string;
  service_name: string;
  server_name: string;
  server_ips: string[];
  start_time: number;
  ping_time: number;
  topics: string[];
  handlers: string[];
  handled: HandledStat[] | null;
  state: string;
}

/**
 * Returns current time in milliseconds since Unix epoch.
 */
export function nowMillis(): number {
  return Date.now();
}

/**
 * Infers a type hint string from a JavaScript value.
 */
export function inferTypeHint(value: unknown): string {
  if (typeof value === "boolean") {
    return "Boolean";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? "Long" : "Double";
  }
  if (typeof value === "string") {
    return "String";
  }
  if (Array.isArray(value)) {
    return "List";
  }
  if (typeof value === "object" && value !== null) {
    return "Map";
  }
  return "Any";
}
