# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""MCP Server with FFL compiler and runtime tools + resources."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import (
    Resource,
    TextContent,
    Tool,
)
from pydantic import AnyUrl

from .serializers import (
    serialize_execution_result,
    serialize_flow,
    serialize_flow_source,
    serialize_handler_registration,
    serialize_log,
    serialize_runner,
    serialize_server,
    serialize_step,
    serialize_task,
)


def create_server(
    store: Any = None,
    config_path: str | None = None,
) -> Server:
    """Create and configure the MCP server.

    Args:
        store: Optional data store (MongoStore or MemoryStore). If None and
               a resource/tool needs it, a MongoStore is created lazily.
        config_path: Optional FFL config file path for MongoStore creation.

    Returns:
        Configured MCP Server instance.
    """
    server = Server("afl-mcp")

    _store_holder: dict[str, Any] = {"store": store}

    def _get_store() -> Any:
        if _store_holder["store"] is None:
            from .store import get_store

            _store_holder["store"] = get_store(config_path)
        return _store_holder["store"]

    # =========================================================================
    # Tools
    # =========================================================================

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="fw_compile",
                description=(
                    "Parse FFL source and return the compiled JSON AST "
                    "({success, json}) or {success: false, errors: [...]} on "
                    "parse failure. Use this when you need the structured AST "
                    "(e.g. to inspect what a piece of FFL means). For "
                    "correctness checks before presenting FFL to the user, "
                    "prefer fw_validate — it returns rule_ids and docs_uris "
                    "and catches semantic errors that compile alone misses."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "AFL source code to compile",
                        },
                    },
                    "required": ["source"],
                },
            ),
            Tool(
                name="fw_validate",
                description=(
                    "Validate FFL source against grammar + semantic rules. "
                    "Call this before showing FFL to the user. Returns "
                    "{valid, errors, warnings} where each diagnostic has "
                    "{message, rule_id, severity, line, column, docs_uri, "
                    "suggested_fix}. On error, fetch fw://docs/rules/{rule_id} "
                    "for paired wrong/right examples and a suggested fix."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "AFL source code to validate",
                        },
                    },
                    "required": ["source"],
                },
            ),
            Tool(
                name="fw_execute_workflow",
                description=(
                    "Execute a workflow defined in FFL source against an "
                    "in-memory store. Call fw_validate first — this tool will "
                    "fail at runtime on semantic errors but won't tell you "
                    "what's wrong as cleanly. Returns {success, workflow_id, "
                    "status, iterations, outputs, error?}. NOTE: this runs "
                    "synchronously with MemoryStore — for distributed "
                    "execution against the real runner fleet, post a "
                    "fw:execute task via the dashboard or scripts/ tooling."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "AFL source code containing the workflow",
                        },
                        "workflow_name": {
                            "type": "string",
                            "description": "Name of the workflow to execute",
                        },
                        "inputs": {
                            "type": "object",
                            "description": "Optional input parameter values",
                        },
                    },
                    "required": ["source", "workflow_name"],
                },
            ),
            Tool(
                name="fw_continue_step",
                description=(
                    "Unblock a step waiting in EventBlocked state by supplying "
                    "its result. Use when a step's handler runs out-of-band "
                    "(e.g. external agent, manual approval) and the workflow "
                    "needs to be told the result. The result dict's keys must "
                    "match the facet's declared return attributes. To retry a "
                    "step that errored, use fw_retry_step instead."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "ID of the step to continue",
                        },
                        "result": {
                            "type": "object",
                            "description": "Optional result dict to apply as return attributes",
                        },
                    },
                    "required": ["step_id"],
                },
            ),
            Tool(
                name="fw_retry_step",
                description=(
                    "Reset a single errored step to EventTransmit so the "
                    "runner picks it up again. Use for transient failures "
                    "(connection blip, timeout). For workflows stuck due to "
                    "ancestor-block errors, dead-server tasks, or premature "
                    "completion, use fw_repair_workflow — it diagnoses and "
                    "fixes multiple issues in one pass. By default refuses "
                    "if the step's task is RUNNING (would race with the "
                    "in-flight handler); pass force=true to override."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "ID of the failed step to retry",
                        },
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Reset the step even if its task is currently RUNNING. "
                                "Use only when the runner is known to be unresponsive."
                            ),
                        },
                    },
                    "required": ["step_id"],
                },
            ),
            Tool(
                name="fw_resume_workflow",
                description=(
                    "Resume a paused/halted workflow execution from its last "
                    "checkpoint, given the original FFL source and inputs. "
                    "Use when a workflow was paused via fw_manage_runner "
                    "(action='pause') or after the runner was stopped. To "
                    "diagnose-then-fix a stuck workflow, run "
                    "fw_repair_workflow first."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "ID of the workflow to resume",
                        },
                        "source": {
                            "type": "string",
                            "description": "AFL source code for the workflow",
                        },
                        "workflow_name": {
                            "type": "string",
                            "description": "Name of the workflow to resume",
                        },
                        "inputs": {
                            "type": "object",
                            "description": "Optional input parameter values",
                        },
                    },
                    "required": ["workflow_id", "source", "workflow_name"],
                },
            ),
            Tool(
                name="fw_manage_runner",
                description=(
                    "Change a runner's lifecycle state: cancel (terminal — "
                    "stops scheduling new steps), pause (halts step dispatch "
                    "while preserving state), or resume (returns a paused "
                    "runner to running). Use cancel for unwanted/runaway "
                    "runs. Use pause+resume for graceful intervention. To "
                    "stop and reset running tasks back to pending across the "
                    "fleet, prefer fw runner drain."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "runner_id": {
                            "type": "string",
                            "description": "ID of the runner to manage",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["cancel", "pause", "resume"],
                            "description": "Action to perform",
                        },
                    },
                    "required": ["runner_id", "action"],
                },
            ),
            Tool(
                name="fw_list_handlers",
                description=(
                    "List registered handler facets the runtime knows how to "
                    "execute. Call this BEFORE writing FFL that references "
                    "handler facets — do not invent handler names. Returns "
                    "an array of {facet_name, module_uri, entrypoint, version, "
                    "timeout_ms, requirements, metadata, ...}. Use the optional "
                    "namespace filter (e.g. 'osm') to scope results."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "description": (
                                "Optional namespace prefix filter "
                                "(matches facet_name starting with '{namespace}.')"
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="fw_describe_handler",
                description=(
                    "Get the full registration for a single handler facet: "
                    "module_uri, entrypoint, version, timeout_ms, requirements, "
                    "metadata, and timestamps. Call this when planning a step "
                    "that uses a handler, to verify the handler exists and to "
                    "see its declared metadata before writing the FFL step."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "facet_name": {
                            "type": "string",
                            "description": "Qualified facet name (e.g. 'osm.DownloadRegion')",
                        },
                    },
                    "required": ["facet_name"],
                },
            ),
            Tool(
                name="fw_manage_handlers",
                description=(
                    "Mutate the handler registry: register a new handler, "
                    "update an existing one, or delete one. For READ access "
                    "prefer fw_list_handlers / fw_describe_handler — those "
                    "are dedicated tools with clearer contracts. The list/get "
                    "actions here remain for backward compatibility."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "get", "register", "delete"],
                            "description": "Action to perform",
                        },
                        "facet_name": {
                            "type": "string",
                            "description": "Qualified facet name (required for get/register/delete)",
                        },
                        "module_uri": {
                            "type": "string",
                            "description": "Python module path (required for register)",
                        },
                        "entrypoint": {
                            "type": "string",
                            "description": "Function name within module (default: handle)",
                        },
                        "version": {
                            "type": "string",
                            "description": "Handler version (default: 1.0.0)",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "description": "Timeout in milliseconds (default: 30000)",
                        },
                        "requirements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Python package requirements",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Arbitrary metadata dict",
                        },
                    },
                    "required": ["action"],
                },
            ),
            Tool(
                name="fw_repair_workflow",
                description=(
                    "Diagnose and repair a stuck workflow in one pass. Runs "
                    "five checks: (1) resets runner state if prematurely "
                    "completed/failed but with non-terminal work, (2) drains "
                    "orphaned tasks on dead/shutdown servers to pending, (3) "
                    "retries steps with transient errors (connection/timeout), "
                    "(4) resets errored ancestor blocks so execution can "
                    "resume, (5) resets steps marked Complete but with failed "
                    "tasks. Pass dry_run=true to preview without changes. "
                    "Prefer this over fw_retry_step for any stuck workflow — "
                    "it covers all the common failure modes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "runner_id": {
                            "type": "string",
                            "description": "ID of the runner whose workflow to repair",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview repairs without applying (default: false)",
                        },
                    },
                    "required": ["runner_id"],
                },
            ),
            Tool(
                name="fw_postgis_query",
                description=(
                    "Run a read-only SQL query against the PostGIS/OSM "
                    "database. Writes are blocked at two levels (SQL keyword "
                    "filter + read-only transaction). Tables: osm_nodes "
                    "(osm_id, region, tags JSONB, geom Point), osm_ways "
                    "(osm_id, region, tags JSONB, geom LineString), "
                    "osm_import_log (region, node_count, way_count, "
                    "imported_at). osm2pgsql-compatible views with flattened "
                    "tag columns: planet_osm_point, planet_osm_line, "
                    "planet_osm_roads. Use ST_* for spatial queries. Tags are "
                    "JSONB — query with tags->>'key' or tags?'key'. Common "
                    "tags: amenity, shop, highway, building, name, cuisine. "
                    "Results capped at 500 rows by default (max 5000)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "SQL query (SELECT only — writes are blocked)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max rows to return (default 500, max 5000)",
                        },
                    },
                    "required": ["sql"],
                },
            ),
            Tool(
                name="fw_catalog_search",
                description=(
                    "Search the Claude workflow catalog for a reusable workflow "
                    "BEFORE authoring a new one. Returns ranked summaries "
                    "{slug, title, description, tags, version, status, "
                    "param_schema, facets_used}. Filter by tags / facet / kind "
                    "(workflow|library). Re-use an existing slug when one fits."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Free-text query"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "facet": {
                            "type": "string",
                            "description": "Require this facet in facets_used",
                        },
                        "kind": {"type": "string", "description": "workflow | library"},
                    },
                },
            ),
            Tool(
                name="fw_catalog_match",
                description=(
                    "Reuse-first matching — call this with a natural-language REQUEST "
                    "BEFORE authoring a workflow. Unlike fw_catalog_search (keyword "
                    "lookup), it matches by *intent* — primarily the recorded authoring "
                    "summary of each workflow — and returns a verdict: 'reuse' (a strong "
                    "match; fill best.param_schema and run via fw_catalog_run instead of "
                    "authoring), 'review' (candidates exist — inspect first), or "
                    "'author_new' (no good match — author one, which then joins the "
                    "catalog for next time). Each candidate carries its summary (intent), "
                    "param_schema, entry_workflow, and a 0-1 confidence. The workflow-level "
                    "analogue of fw_capabilities."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "request": {
                            "type": "string",
                            "description": "The natural-language request to satisfy.",
                        },
                        "kind": {"type": "string", "description": "workflow | library"},
                        "limit": {"type": "integer", "description": "Max candidates (default 5)."},
                    },
                    "required": ["request"],
                },
            ),
            Tool(
                name="fw_capabilities",
                description=(
                    "Search the FACET capability index — the facet-level analogue of "
                    "fw_catalog_search (which finds workflows). Returns ranked facets "
                    "{qualified_name, purpose, signature, params, returns, namespace, "
                    "is_event} so you can discover composable primitives by intent — "
                    "e.g. 'what operates on a GeoJSON path?', 'what extracts amenities?', "
                    "'reverse geocode' — instead of guessing facet names. Indexes the "
                    "deployed/seeded library by default; pass `source` to index FFL you "
                    "are authoring. Filter by namespace (prefix) and kind."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text; matched (AND across terms) against "
                            "facet name, purpose, doc, and param/return names + types.",
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Namespace prefix filter, e.g. 'osm.Spatial' or 'osm'.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["facet", "event_facet", "all"],
                            "description": "Filter by facet kind (default: all).",
                        },
                        "source": {
                            "type": "string",
                            "description": "Optional FFL source to index instead of the "
                            "deployed library (use when authoring).",
                        },
                        "effect": {
                            "type": "string",
                            "enum": ["pure", "external", "io"],
                            "description": "Keep only facets with this declared effect "
                            "(pure = in-process compute; external = hits a network/engine).",
                        },
                        "max_cost": {
                            "type": "string",
                            "enum": ["free", "cheap", "moderate", "expensive"],
                            "description": "Drop facets whose known cost tier exceeds this "
                            "ceiling (prefer cheap primitives; un-annotated cost passes).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return (default 25; 0 = all).",
                        },
                    },
                },
            ),
            Tool(
                name="fw_catalog_get",
                description=(
                    "Fetch one catalog entry + a specific (or latest) revision: "
                    "FFL source, param schema, pinned dependencies, and the full "
                    "version list. Use after fw_catalog_search to inspect a "
                    "candidate before reusing or revising it."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "version": {"type": "integer", "description": "Defaults to latest"},
                    },
                    "required": ["slug"],
                },
            ),
            Tool(
                name="fw_catalog_save",
                description=(
                    "Store an FFL workflow (or library) in the catalog WITHOUT a "
                    "file. Validates, merges pinned library deps, compiles, and "
                    "creates an immutable, content-hashed revision (identical "
                    "content de-dupes; any change bumps the version — the old "
                    "version stays runnable). Saved as a DRAFT: call fw_validate "
                    "first, then fw_catalog_publish before unattended runs. "
                    "Returns {ok, slug, version, revision_id, deduped, is_valid, "
                    "status, warnings}."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "slug": {
                            "type": "string",
                            "description": "Stable id, e.g. 'geo.region-summary'",
                        },
                        "ffl_source": {
                            "type": "string",
                            "description": "The FFL (this entry's own source)",
                        },
                        "kind": {"type": "string", "description": "workflow (default) | library"},
                        "title": {"type": "string"},
                        "description": {
                            "type": "string",
                            "description": "What it does (for discovery)",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "entry_workflow": {
                            "type": "string",
                            "description": "Qualified/short name of the workflow to run (required if the FFL defines more than one)",
                        },
                        "depends_on": {
                            "type": "array",
                            "description": "Pinned library deps: [{slug, version?}]",
                            "items": {"type": "object"},
                        },
                        "note": {
                            "type": "string",
                            "description": "Changelog note for this revision",
                        },
                        "summary": {
                            "type": "string",
                            "description": (
                                "Markdown narrative of WHY this workflow exists — the request/intent "
                                "it was built from and how it addresses it. Recorded with the revision "
                                "and shown in the UI so the workflow can be understood, not just read "
                                "as FFL. Provide this whenever you author a workflow from a description."
                            ),
                        },
                    },
                    "required": ["slug", "ffl_source"],
                },
            ),
            Tool(
                name="fw_catalog_publish",
                description=(
                    "Publish (review-approve) a catalog revision so it can run "
                    "unattended. Refuses an invalid revision. Drafts can only be "
                    "run with allow_unpublished=True (an attended test); "
                    "publishing is the gate for fw_catalog_run's default path."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "version": {"type": "integer", "description": "Defaults to latest"},
                    },
                    "required": ["slug"],
                },
            ),
            Tool(
                name="fw_catalog_run",
                description=(
                    "Run a cataloged workflow by pinning a revision and posting a "
                    "fw:execute bootstrap task with the given inputs (distributed "
                    "execution on the runner fleet; view it in the dashboard). "
                    "Re-running with different inputs re-uses the IDENTICAL pinned "
                    "revision. Default path requires a PUBLISHED revision; pass "
                    "allow_unpublished=true for an attended test of a draft. "
                    "Returns {runner_id, task_id, version, workflow} or "
                    "{ok:false, blocked, error}."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "version": {
                            "type": "integer",
                            "description": "Defaults to latest published",
                        },
                        "inputs": {"type": "object", "description": "Workflow parameter values"},
                        "allow_unpublished": {
                            "type": "boolean",
                            "description": "Attended run of a draft",
                        },
                    },
                    "required": ["slug"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "fw_compile":
            return _tool_compile(arguments)
        elif name == "fw_validate":
            return _tool_validate(arguments)
        elif name == "fw_execute_workflow":
            return _tool_execute_workflow(arguments)
        elif name == "fw_continue_step":
            return _tool_continue_step(arguments, _get_store)
        elif name == "fw_retry_step":
            return _tool_retry_step(arguments, _get_store)
        elif name == "fw_resume_workflow":
            return _tool_resume_workflow(arguments, _get_store)
        elif name == "fw_manage_runner":
            return _tool_manage_runner(arguments, _get_store)
        elif name == "fw_list_handlers":
            return _tool_list_handlers(arguments, _get_store)
        elif name == "fw_describe_handler":
            return _tool_describe_handler(arguments, _get_store)
        elif name == "fw_manage_handlers":
            return _tool_manage_handlers(arguments, _get_store)
        elif name == "fw_repair_workflow":
            return _tool_repair_workflow(arguments, _get_store)
        elif name == "fw_postgis_query":
            return _tool_postgis_query(arguments)
        elif name == "fw_catalog_search":
            return _tool_catalog_search(arguments, _get_store)
        elif name == "fw_catalog_match":
            return _tool_catalog_match(arguments, _get_store)
        elif name == "fw_catalog_get":
            return _tool_catalog_get(arguments, _get_store)
        elif name == "fw_catalog_save":
            return _tool_catalog_save(arguments, _get_store)
        elif name == "fw_catalog_publish":
            return _tool_catalog_publish(arguments, _get_store)
        elif name == "fw_catalog_run":
            return _tool_catalog_run(arguments, _get_store)
        elif name == "fw_capabilities":
            return _tool_capabilities(arguments, _get_store)
        else:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"Unknown tool: {name}"}),
                )
            ]

    # =========================================================================
    # Resources
    # =========================================================================

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl("fw://runners"),
                name="List all runners",
                description="List all runners (most recent first)",
            ),
            Resource(
                uri=AnyUrl("fw://runners/{runner_id}"),
                name="Runner detail",
                description="Runner detail with workflow info",
            ),
            Resource(
                uri=AnyUrl("fw://runners/{runner_id}/steps"),
                name="Runner steps",
                description="Steps for a runner's workflow",
            ),
            Resource(
                uri=AnyUrl("fw://runners/{runner_id}/logs"),
                name="Runner logs",
                description="Log entries for a runner",
            ),
            Resource(
                uri=AnyUrl("fw://steps/{step_id}"),
                name="Step detail",
                description="Step detail with state and attributes",
            ),
            Resource(
                uri=AnyUrl("fw://flows"),
                name="List all flows",
                description="List all compiled flows",
            ),
            Resource(
                uri=AnyUrl("fw://flows/{flow_id}"),
                name="Flow detail",
                description="Flow detail with workflows",
            ),
            Resource(
                uri=AnyUrl("fw://flows/{flow_id}/source"),
                name="Flow source",
                description="AFL source code for a flow",
            ),
            Resource(
                uri=AnyUrl("fw://servers"),
                name="List servers",
                description="List all registered servers",
            ),
            Resource(
                uri=AnyUrl("fw://tasks"),
                name="List tasks",
                description="List pending/active tasks",
            ),
            Resource(
                uri=AnyUrl("fw://handlers"),
                name="List handler registrations",
                description="List all handler registrations",
            ),
            Resource(
                uri=AnyUrl("fw://handlers/{facet_name}"),
                name="Handler registration detail",
                description="Handler registration detail by facet name",
            ),
            # ---- Static documentation and canonical examples ----
            Resource(
                uri=AnyUrl("fw://docs/rules"),
                name="Validation rules index",
                description=(
                    "List of all validator rule IDs with one-line summaries. "
                    "Read this when you want to see what categories of "
                    "validation errors exist."
                ),
            ),
            Resource(
                uri=AnyUrl("fw://docs/rules/{rule_id}"),
                name="Validation rule details",
                description=(
                    "Paired wrong/right examples and a 'why' for a single "
                    "rule_id. Fetch this URI from a fw_validate diagnostic's "
                    "docs_uri field to see how to fix the error."
                ),
            ),
            Resource(
                uri=AnyUrl("fw://docs/grammar"),
                name="FFL grammar reference",
                description="Full FFL language grammar reference.",
            ),
            Resource(
                uri=AnyUrl("fw://docs/execution-model"),
                name="Runtime execution model",
                description="How the runtime executes workflows, steps, and events.",
            ),
            Resource(
                uri=AnyUrl("fw://examples/canonical"),
                name="Canonical FFL examples index",
                description=(
                    "List of small, idiomatic FFL example files. Use these "
                    "as templates when writing new workflows — they cover "
                    "the canonical patterns the validator considers valid."
                ),
            ),
            Resource(
                uri=AnyUrl("fw://examples/canonical/{name}"),
                name="Canonical FFL example",
                description="A single canonical example file by name.",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        return _handle_resource(str(uri), _get_store)

    return server


# =============================================================================
# Tool implementations
# =============================================================================


def _tool_compile(arguments: dict[str, Any]) -> list[TextContent]:
    """Compile FFL source to JSON."""
    source = arguments.get("source", "")
    try:
        from facetwork import emit_dict, parse

        ast = parse(source)
        compiled = emit_dict(ast)
        result = {"success": True, "json": compiled}
    except Exception as e:
        result = {"success": False, "errors": [str(e)]}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_validate(arguments: dict[str, Any]) -> list[TextContent]:
    """Validate FFL source semantically.

    Returns structured diagnostics with rule_id, severity, location, and
    docs_uri so callers can look up paired wrong/right examples and
    repair guidance for each rule.
    """
    source = arguments.get("source", "")

    def _serialize(e: Any) -> dict[str, Any]:
        return {
            "message": e.message,
            "rule_id": getattr(e, "rule_id", "UNKNOWN"),
            "severity": getattr(e, "severity", "error"),
            "line": e.line,
            "column": e.column,
            "docs_uri": getattr(e, "docs_uri", None),
            "suggested_fix": getattr(e, "suggested_fix", None),
        }

    try:
        from facetwork import parse, validate

        ast = parse(source)
        validation = validate(ast)
        result: dict[str, Any] = {
            "valid": validation.is_valid,
            "errors": [_serialize(e) for e in validation.errors],
            "warnings": [_serialize(w) for w in validation.warnings],
        }
    except Exception as e:
        result = {
            "valid": False,
            "errors": [
                {
                    "message": str(e),
                    "rule_id": "PARSE_ERROR",
                    "severity": "error",
                    "line": None,
                    "column": None,
                    "docs_uri": "fw://docs/rules/PARSE_ERROR",
                    "suggested_fix": None,
                }
            ],
            "warnings": [],
        }
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_execute_workflow(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a workflow from FFL source."""
    source = arguments.get("source", "")
    workflow_name = arguments.get("workflow_name", "")
    inputs = arguments.get("inputs", None)

    try:
        from facetwork import emit_dict, parse
        from facetwork.runtime import Evaluator, MemoryStore

        ast = parse(source)
        compiled = emit_dict(ast)

        # Find the workflow by name
        workflow_ast = _find_workflow(compiled, workflow_name)
        if workflow_ast is None:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "success": False,
                            "error": f"Workflow '{workflow_name}' not found in source",
                        }
                    ),
                )
            ]

        store = MemoryStore()
        evaluator = Evaluator(store)
        exec_result = evaluator.execute(workflow_ast, inputs=inputs, program_ast=compiled)
        result = serialize_execution_result(exec_result)
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_continue_step(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Continue an event-blocked step."""
    step_id = arguments.get("step_id", "")
    result_data = arguments.get("result", None)

    try:
        from facetwork.runtime import Evaluator

        store = get_store()
        evaluator = Evaluator(store)
        evaluator.continue_step(step_id, result=result_data)
        result: dict[str, Any] = {"success": True}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_retry_step(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Retry a failed step.  Refuses if the step's task is RUNNING
    unless ``force=true`` — mirrors the dashboard ``/steps/{id}/retry``
    endpoint."""
    step_id = arguments.get("step_id", "")
    force = bool(arguments.get("force", False))

    try:
        from facetwork.runtime import Evaluator
        from facetwork.runtime.entities import TaskState

        store = get_store()
        task = store.get_task_for_step(step_id)
        task_was_running = task is not None and task.state == TaskState.RUNNING
        if task_was_running and not force:
            step = store.get_step(step_id)
            result: dict[str, Any] = {
                "success": False,
                "error": "step_task_running",
                "message": (
                    "Step's task is currently RUNNING. Pass force=true to reset it anyway."
                ),
                "blockers": [
                    {
                        "step_id": step_id,
                        "facet_name": (step.facet_name if step else "") or "",
                        "task_uuid": task.uuid,
                    }
                ],
            }
        else:
            evaluator = Evaluator(store)
            evaluator.retry_step(step_id)
            result = {"success": True, "forced": bool(force and task_was_running)}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_resume_workflow(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Resume a paused workflow."""
    workflow_id = arguments.get("workflow_id", "")
    source = arguments.get("source", "")
    workflow_name = arguments.get("workflow_name", "")
    inputs = arguments.get("inputs", None)

    try:
        from facetwork import emit_dict, parse
        from facetwork.runtime import Evaluator

        ast = parse(source)
        compiled = emit_dict(ast)
        workflow_ast = _find_workflow(compiled, workflow_name)
        if workflow_ast is None:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "success": False,
                            "error": f"Workflow '{workflow_name}' not found in source",
                        }
                    ),
                )
            ]

        store = get_store()
        evaluator = Evaluator(store)
        exec_result = evaluator.resume(
            workflow_id,
            workflow_ast,
            program_ast=compiled,
            inputs=inputs,
        )
        result = serialize_execution_result(exec_result)
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_manage_runner(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Manage a runner (cancel/pause/resume)."""
    runner_id = arguments.get("runner_id", "")
    action = arguments.get("action", "")

    from facetwork.runtime.entities import RunnerState

    action_map = {
        "cancel": RunnerState.CANCELLED,
        "pause": RunnerState.PAUSED,
        "resume": RunnerState.RUNNING,
    }

    if action not in action_map:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "success": False,
                        "error": f"Invalid action: {action}. Must be cancel, pause, or resume.",
                    }
                ),
            )
        ]

    try:
        store = get_store()
        runner = store.get_runner(runner_id)
        if not runner:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "success": False,
                            "error": f"Runner '{runner_id}' not found",
                        }
                    ),
                )
            ]
        store.update_runner_state(runner_id, action_map[action])
        result: dict[str, Any] = {"success": True}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_list_handlers(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """List registered handlers, optionally filtered by namespace prefix."""
    namespace = arguments.get("namespace")
    try:
        store = get_store()
        handlers = store.list_handler_registrations()
        if namespace:
            prefix = f"{namespace}."
            handlers = [h for h in handlers if h.facet_name.startswith(prefix)]
        result: dict[str, Any] = {
            "success": True,
            "count": len(handlers),
            "handlers": [serialize_handler_registration(h) for h in handlers],
        }
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_describe_handler(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Return the full registration for a single handler facet."""
    facet_name = arguments.get("facet_name", "")
    if not facet_name:
        return [
            TextContent(
                type="text",
                text=json.dumps({"success": False, "error": "facet_name is required"}),
            )
        ]
    try:
        store = get_store()
        handler = store.get_handler_registration(facet_name)
        if not handler:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "success": False,
                            "error": f"Handler '{facet_name}' not found",
                            "hint": (
                                "Call fw_list_handlers to see what is registered. "
                                "Handler facet names are qualified (e.g. 'osm.DownloadRegion')."
                            ),
                        }
                    ),
                )
            ]
        result: dict[str, Any] = {
            "success": True,
            "handler": serialize_handler_registration(handler),
        }
    except Exception as e:
        result = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_manage_handlers(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Manage handler registrations (list/get/register/delete)."""
    import time

    from facetwork.runtime.entities import HandlerRegistration

    action = arguments.get("action", "")

    if action == "list":
        try:
            store = get_store()
            handlers = store.list_handler_registrations()
            result = {
                "success": True,
                "handlers": [serialize_handler_registration(h) for h in handlers],
            }
        except Exception as e:
            result = {"success": False, "error": str(e)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    elif action == "get":
        facet_name = arguments.get("facet_name", "")
        if not facet_name:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"success": False, "error": "facet_name is required for get"}),
                )
            ]
        try:
            store = get_store()
            handler = store.get_handler_registration(facet_name)
            if not handler:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "success": False,
                                "error": f"Handler '{facet_name}' not found",
                            }
                        ),
                    )
                ]
            result = {
                "success": True,
                "handler": serialize_handler_registration(handler),
            }
        except Exception as e:
            result = {"success": False, "error": str(e)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    elif action == "register":
        facet_name = arguments.get("facet_name", "")
        module_uri = arguments.get("module_uri", "")
        if not facet_name:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"success": False, "error": "facet_name is required for register"}
                    ),
                )
            ]
        if not module_uri:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"success": False, "error": "module_uri is required for register"}
                    ),
                )
            ]
        try:
            store = get_store()
            now = int(time.time() * 1000)
            # Preserve original created timestamp on upsert
            existing = store.get_handler_registration(facet_name)
            created = existing.created if existing else now
            reg = HandlerRegistration(
                facet_name=facet_name,
                module_uri=module_uri,
                entrypoint=arguments.get("entrypoint", "handle"),
                version=arguments.get("version", "1.0.0"),
                timeout_ms=arguments.get("timeout_ms", 30000),
                requirements=arguments.get("requirements", []),
                metadata=arguments.get("metadata", {}),
                created=created,
                updated=now,
            )
            store.save_handler_registration(reg)
            result = {
                "success": True,
                "handler": serialize_handler_registration(reg),
            }
        except Exception as e:
            result = {"success": False, "error": str(e)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    elif action == "delete":
        facet_name = arguments.get("facet_name", "")
        if not facet_name:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"success": False, "error": "facet_name is required for delete"}
                    ),
                )
            ]
        try:
            store = get_store()
            deleted = store.delete_handler_registration(facet_name)
            if not deleted:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "success": False,
                                "error": f"Handler '{facet_name}' not found",
                            }
                        ),
                    )
                ]
            result = {"success": True}
        except Exception as e:
            result = {"success": False, "error": str(e)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    else:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "success": False,
                        "error": f"Invalid action: {action}. Must be list, get, register, or delete.",
                    }
                ),
            )
        ]


# =============================================================================
# Resource handler
# =============================================================================


def _handle_resource(uri: str, get_store: Any) -> str:
    """Route a resource URI to its handler.

    Accepts both the canonical ``fw://`` scheme and the legacy ``afl://`` scheme
    (pre-Facetwork name) so cached/old client references keep resolving during
    the migration. ``fw://`` is what the server now advertises.
    """
    parts = uri.replace("fw://", "").replace("afl://", "").strip("/").split("/")

    # Static resources — served from disk, no store needed.
    if parts[0] == "docs":
        return _handle_docs_resource(parts[1:])
    if parts[0] == "examples":
        return _handle_examples_resource(parts[1:])

    store = get_store()

    if parts[0] == "runners":
        if len(parts) == 1:
            runners = store.get_all_runners()
            return json.dumps([serialize_runner(r) for r in runners], default=str)
        runner_id = parts[1]
        if len(parts) == 2:
            runner = store.get_runner(runner_id)
            if not runner:
                return json.dumps({"error": "Runner not found"})
            return json.dumps(serialize_runner(runner), default=str)
        if len(parts) == 3 and parts[2] == "steps":
            runner = store.get_runner(runner_id)
            if not runner:
                return json.dumps({"error": "Runner not found"})
            steps = store.get_steps_by_workflow(runner.workflow_id)
            return json.dumps([serialize_step(s) for s in steps], default=str)
        if len(parts) == 3 and parts[2] == "logs":
            logs = store.get_logs_by_runner(runner_id)
            return json.dumps([serialize_log(lg) for lg in logs], default=str)

    elif parts[0] == "steps":
        if len(parts) == 2:
            step = store.get_step(parts[1])
            if not step:
                return json.dumps({"error": "Step not found"})
            return json.dumps(serialize_step(step), default=str)

    elif parts[0] == "flows":
        if len(parts) == 1:
            flows = store.get_all_flows()
            return json.dumps([serialize_flow(f) for f in flows], default=str)
        flow_id = parts[1]
        if len(parts) == 2:
            flow = store.get_flow(flow_id)
            if not flow:
                return json.dumps({"error": "Flow not found"})
            return json.dumps(serialize_flow(flow), default=str)
        if len(parts) == 3 and parts[2] == "source":
            flow = store.get_flow(flow_id)
            if not flow:
                return json.dumps({"error": "Flow not found"})
            return json.dumps(serialize_flow_source(flow), default=str)

    elif parts[0] == "servers":
        servers = store.get_all_servers()
        return json.dumps([serialize_server(s) for s in servers], default=str)

    elif parts[0] == "tasks":
        tasks = store.get_all_tasks()
        return json.dumps([serialize_task(t) for t in tasks], default=str)

    elif parts[0] == "handlers":
        if len(parts) == 1:
            handlers = store.list_handler_registrations()
            return json.dumps([serialize_handler_registration(h) for h in handlers], default=str)
        facet_name = parts[1]
        handler = store.get_handler_registration(facet_name)
        if not handler:
            return json.dumps({"error": f"Handler '{facet_name}' not found"})
        return json.dumps(serialize_handler_registration(handler), default=str)

    return json.dumps({"error": f"Unknown resource: {uri}"})


# Repo root resolved from this file's path: facetwork/mcp/server.py -> repo/
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULES_DIR = _REPO_ROOT / "docs" / "reference" / "rules"
_CANONICAL_DIR = _REPO_ROOT / "examples" / "canonical"
_DOC_ALIASES = {
    "grammar": _REPO_ROOT / "docs" / "reference" / "language" / "grammar.md",
    "execution-model": _REPO_ROOT / "docs" / "reference" / "runtime.md",
}


def _read_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"<error reading {path.name}: {e}>"


def _handle_docs_resource(parts: list[str]) -> str:
    """Serve fw://docs/* resources from on-disk files."""
    if not parts:
        return json.dumps({"error": "Empty docs path"})

    if parts[0] == "rules":
        # fw://docs/rules            -> index
        # fw://docs/rules/{rule_id}  -> specific rule
        if len(parts) == 1:
            if not _RULES_DIR.exists():
                return json.dumps(
                    {
                        "error": "Rules directory not found",
                        "expected_path": str(_RULES_DIR),
                        "hint": "Rule docs live at docs/reference/rules/{rule_id}.md",
                    }
                )
            entries = []
            for rule_path in sorted(_RULES_DIR.glob("*.md")):
                rule_id = rule_path.stem
                # Skip the README — it documents the directory itself.
                if rule_id.lower() == "readme":
                    continue
                summary = _first_nonempty_line(rule_path) or ""
                entries.append({"rule_id": rule_id, "summary": summary})
            return json.dumps({"rules": entries, "count": len(entries)})
        if len(parts) == 2:
            rule_id = parts[1]
            content = _read_text_file(_RULES_DIR / f"{rule_id}.md")
            if content is None:
                return json.dumps(
                    {
                        "error": f"Rule '{rule_id}' not documented",
                        "hint": (
                            "Read fw://docs/rules to see which rules have "
                            "documentation. Some rule_ids are emitted by the "
                            "validator before their docs file is written."
                        ),
                    }
                )
            return content

    if len(parts) == 1 and parts[0] in _DOC_ALIASES:
        content = _read_text_file(_DOC_ALIASES[parts[0]])
        if content is None:
            return json.dumps(
                {
                    "error": f"Doc '{parts[0]}' not found on disk",
                    "expected_path": str(_DOC_ALIASES[parts[0]]),
                }
            )
        return content

    return json.dumps({"error": f"Unknown docs path: {'/'.join(parts)}"})


def _handle_examples_resource(parts: list[str]) -> str:
    """Serve fw://examples/* resources from on-disk files."""
    if not parts or parts[0] != "canonical":
        return json.dumps({"error": f"Unknown examples path: {'/'.join(parts)}"})

    # fw://examples/canonical            -> index
    # fw://examples/canonical/{name}     -> specific example file
    if len(parts) == 1:
        if not _CANONICAL_DIR.exists():
            return json.dumps(
                {
                    "error": "Canonical examples directory not found",
                    "expected_path": str(_CANONICAL_DIR),
                    "hint": "Canonical examples live at examples/canonical/",
                }
            )
        entries = []
        for path in sorted(_CANONICAL_DIR.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                summary = _first_nonempty_line(path) if path.suffix == ".md" else ""
                entries.append({"name": path.name, "summary": summary})
        return json.dumps({"examples": entries, "count": len(entries)})

    if len(parts) == 2:
        # Reject path traversal — names must be a single filename.
        name = parts[1]
        if "/" in name or ".." in name or name.startswith("."):
            return json.dumps({"error": f"Invalid example name: {name}"})
        # Allow lookup by exact name OR by stem (e.g. "workflow-simple" -> "workflow-simple.ffl")
        path = _CANONICAL_DIR / name
        if not path.exists():
            stem_matches = list(_CANONICAL_DIR.glob(f"{name}.*"))
            if len(stem_matches) == 1:
                path = stem_matches[0]
            else:
                return json.dumps(
                    {
                        "error": f"Example '{name}' not found",
                        "hint": "Read fw://examples/canonical for the index.",
                    }
                )
        content = _read_text_file(path)
        if content is None:
            return json.dumps({"error": f"Example '{name}' could not be read"})
        return content

    return json.dumps({"error": f"Unknown examples path: {'/'.join(parts)}"})


def _first_nonempty_line(path: Path) -> str | None:
    """Return the first non-empty, non-heading-marker line from a file."""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Drop leading '#' for markdown headings, return the title text.
                if line.startswith("#"):
                    return line.lstrip("#").strip()
                return line
    except OSError:
        return None
    return None


# =============================================================================
# Helpers
# =============================================================================


def _find_workflow(compiled: dict, workflow_name: str) -> dict | None:
    """Find a workflow declaration by name in compiled output."""
    from facetwork.ast_utils import find_workflow

    return find_workflow(compiled, workflow_name)


# =============================================================================
# Claude workflow catalog tools
# =============================================================================


def _catalog_service(store: Any) -> Any:
    """Build a CatalogService over the MongoStore's `_db` + the store itself
    (for FlowDefinition materialization and bootstrap-task submission)."""
    from facetwork.catalog import CatalogService, MongoCatalogStore

    db = getattr(store, "_db", None)
    if db is None:
        raise RuntimeError(
            "the workflow catalog requires a MongoStore-backed store (no _db on "
            f"{type(store).__name__})"
        )
    return CatalogService(MongoCatalogStore(db), store)


def _catalog_text(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str))]


def _tool_capabilities(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    """Search the facet capability index (deployed library, or a given source)."""
    try:
        from facetwork.capabilities import index_program, index_programs, search

        source = arguments.get("source", "")
        if source:
            from facetwork import emit_dict, parse

            caps = index_program(emit_dict(parse(source)))
            corpus = "source"
        else:
            flows = get_store().get_all_flows()
            caps = index_programs(
                [f.compiled_ast for f in flows if getattr(f, "compiled_ast", None)]
            )
            corpus = "library"

        limit = arguments.get("limit", 25)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 25
        hits = search(
            caps,
            query=arguments.get("query", ""),
            namespace=arguments.get("namespace", ""),
            kind=arguments.get("kind", "all"),
            limit=limit,
            effect=arguments.get("effect", ""),
            max_cost=arguments.get("max_cost", ""),
        )
        return _catalog_text(
            {
                "corpus": corpus,
                "indexed": len(caps),
                "count": len(hits),
                "capabilities": [c.to_dict() for c in hits],
            }
        )
    except Exception as e:
        return _catalog_text({"error": str(e)})


def _tool_catalog_search(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    try:
        svc = _catalog_service(get_store())
        results = svc.search(
            arguments.get("query", ""),
            tags=arguments.get("tags"),
            facet=arguments.get("facet"),
            kind=arguments.get("kind"),
        )
        return _catalog_text({"results": results, "count": len(results)})
    except Exception as e:
        return _catalog_text({"error": str(e)})


def _tool_catalog_match(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    try:
        svc = _catalog_service(get_store())
        limit = arguments.get("limit", 5)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        result = svc.match(arguments.get("request", ""), kind=arguments.get("kind"), limit=limit)
        return _catalog_text(result)
    except Exception as e:
        return _catalog_text({"error": str(e)})


def _tool_catalog_get(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    try:
        svc = _catalog_service(get_store())
        detail = svc.get(arguments["slug"], arguments.get("version"))
        if detail is None:
            return _catalog_text({"error": f"no such workflow: {arguments['slug']}"})
        return _catalog_text(detail)
    except Exception as e:
        return _catalog_text({"error": str(e)})


def _tool_catalog_save(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    from dataclasses import asdict

    try:
        svc = _catalog_service(get_store())
        res = svc.save(
            arguments["slug"],
            ffl_source=arguments["ffl_source"],
            kind=arguments.get("kind", "workflow"),
            title=arguments.get("title", ""),
            description=arguments.get("description", ""),
            tags=arguments.get("tags"),
            depends_on=arguments.get("depends_on"),
            entry_workflow=arguments.get("entry_workflow"),
            author=arguments.get("author", "claude"),
            note=arguments.get("note", ""),
            summary=arguments.get("summary", ""),
        )
        return _catalog_text(asdict(res))
    except Exception as e:
        return _catalog_text({"ok": False, "error": str(e)})


def _tool_catalog_publish(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    try:
        svc = _catalog_service(get_store())
        rev = svc.publish(arguments["slug"], arguments.get("version"))
        return _catalog_text(
            {"ok": True, "slug": rev.slug, "version": rev.version, "status": rev.status}
        )
    except Exception as e:
        return _catalog_text({"ok": False, "error": str(e)})


def _tool_catalog_run(arguments: dict[str, Any], get_store: Any) -> list[TextContent]:
    from facetwork.catalog import CatalogRunBlocked

    try:
        svc = _catalog_service(get_store())
        res = svc.run(
            arguments["slug"],
            version=arguments.get("version"),
            inputs=arguments.get("inputs"),
            allow_unpublished=bool(arguments.get("allow_unpublished", False)),
        )
        res["ok"] = True
        return _catalog_text(res)
    except CatalogRunBlocked as e:
        return _catalog_text({"ok": False, "blocked": True, "error": str(e)})
    except Exception as e:
        return _catalog_text({"ok": False, "error": str(e)})


# =============================================================================
# PostGIS query tool
# =============================================================================

# SQL statements that are NOT allowed (anything that modifies data)
_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"COPY|SET|RESET|VACUUM|ANALYZE|CLUSTER|REINDEX|LOCK|"
    r"BEGIN|COMMIT|ROLLBACK|SAVEPOINT|EXECUTE|PREPARE|DEALLOCATE|"
    r"DO\s+\$)\b",
    re.IGNORECASE,
)


def _tool_repair_workflow(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Diagnose and repair a stuck workflow."""
    runner_id = arguments.get("runner_id", "")
    dry_run = arguments.get("dry_run", False)
    try:
        store = get_store()
        result = store.repair_workflow(runner_id, dry_run=dry_run)
        output = {
            "success": True,
            "dry_run": dry_run,
            "runner_id": result["runner_id"],
            "workflow_id": result["workflow_id"],
            "runner_reset": result["runner_reset"],
            "runner_previous_state": result["runner_previous_state"],
            "orphaned_tasks_reset": len(result["orphaned_tasks_reset"]),
            "transient_steps_retried": len(result["transient_steps_retried"]),
            "ancestors_reset": len(result["ancestors_reset"]),
            "inconsistent_steps_reset": len(result.get("inconsistent_steps_reset", [])),
            "details": {
                "orphaned_tasks": result["orphaned_tasks_reset"],
                "retried_steps": result["transient_steps_retried"],
                "inconsistent_steps": result.get("inconsistent_steps_reset", []),
            },
        }
    except Exception as e:
        output = {"success": False, "error": str(e)}
    return [TextContent(type="text", text=json.dumps(output, default=str))]


def _tool_postgis_query(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a read-only SQL query against PostGIS."""
    from mcp.types import TextContent

    sql = arguments.get("sql", "").strip()
    limit = min(arguments.get("limit", 500), 5000)

    if not sql:
        return [TextContent(type="text", text=json.dumps({"error": "No SQL provided"}))]

    # Block write operations
    if _FORBIDDEN_SQL.search(sql):
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": "Only SELECT queries are allowed"}),
            )
        ]

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": "psycopg2 not installed"}),
            )
        ]

    postgis_url = os.environ.get("FW_POSTGIS_URL", "postgresql://afl:afl@afl-postgres:5432/afl_gis")

    try:
        conn = psycopg2.connect(
            postgis_url, options="-c default_transaction_read_only=on", gssencmode="disable"
        )
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchmany(limit)
                # Convert to serializable dicts
                results = []
                for row in rows:
                    r = {}
                    for k, v in row.items():
                        if hasattr(v, "isoformat"):
                            r[k] = v.isoformat()
                        else:
                            r[k] = v
                    results.append(r)

                total = cur.rowcount if cur.rowcount >= 0 else len(results)
                truncated = total > limit

                result = {
                    "success": True,
                    "rows": results,
                    "row_count": len(results),
                    "total_count": total,
                    "truncated": truncated,
                }
        finally:
            conn.close()
    except psycopg2.errors.ReadOnlySqlTransaction:
        result = {"error": "Only SELECT queries are allowed (read-only connection)"}
    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, default=str))]
