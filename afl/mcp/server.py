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

"""MCP Server with AFL compiler and runtime tools + resources."""

from __future__ import annotations

import json
import os
import re
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
        config_path: Optional AFL config file path for MongoStore creation.

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
                name="afl_compile",
                description="Parse AFL source code and return compiled JSON.",
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
                name="afl_validate",
                description="Validate AFL source code semantically.",
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
                name="afl_execute_workflow",
                description="Execute a workflow from AFL source code.",
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
                name="afl_continue_step",
                description="Unblock an event-blocked step by providing a result.",
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
                name="afl_retry_step",
                description="Retry a failed step by resetting it to EVENT_TRANSMIT.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "ID of the failed step to retry",
                        },
                    },
                    "required": ["step_id"],
                },
            ),
            Tool(
                name="afl_resume_workflow",
                description="Resume a paused workflow execution.",
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
                name="afl_manage_runner",
                description="Cancel, pause, or resume a runner.",
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
                name="afl_manage_handlers",
                description="List, get, register, or delete handler registrations.",
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
                name="afl_postgis_query",
                description=(
                    "Run a read-only SQL query against the PostGIS/OSM database. "
                    "Tables: osm_nodes (osm_id, region, tags JSONB, geom Point), "
                    "osm_ways (osm_id, region, tags JSONB, geom LineString), "
                    "osm_import_log (region, node_count, way_count, imported_at). "
                    "Use ST_* functions for spatial queries. "
                    "Tags are JSONB — query with tags->>'key' or tags?'key'. "
                    "Common tags: amenity, shop, highway, building, name, cuisine, etc. "
                    "Results limited to 500 rows by default."
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
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "afl_compile":
            return _tool_compile(arguments)
        elif name == "afl_validate":
            return _tool_validate(arguments)
        elif name == "afl_execute_workflow":
            return _tool_execute_workflow(arguments)
        elif name == "afl_continue_step":
            return _tool_continue_step(arguments, _get_store)
        elif name == "afl_retry_step":
            return _tool_retry_step(arguments, _get_store)
        elif name == "afl_resume_workflow":
            return _tool_resume_workflow(arguments, _get_store)
        elif name == "afl_manage_runner":
            return _tool_manage_runner(arguments, _get_store)
        elif name == "afl_manage_handlers":
            return _tool_manage_handlers(arguments, _get_store)
        elif name == "afl_postgis_query":
            return _tool_postgis_query(arguments)
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
                uri=AnyUrl("afl://runners"),
                name="List all runners",
                description="List all runners (most recent first)",
            ),
            Resource(
                uri=AnyUrl("afl://runners/{runner_id}"),
                name="Runner detail",
                description="Runner detail with workflow info",
            ),
            Resource(
                uri=AnyUrl("afl://runners/{runner_id}/steps"),
                name="Runner steps",
                description="Steps for a runner's workflow",
            ),
            Resource(
                uri=AnyUrl("afl://runners/{runner_id}/logs"),
                name="Runner logs",
                description="Log entries for a runner",
            ),
            Resource(
                uri=AnyUrl("afl://steps/{step_id}"),
                name="Step detail",
                description="Step detail with state and attributes",
            ),
            Resource(
                uri=AnyUrl("afl://flows"),
                name="List all flows",
                description="List all compiled flows",
            ),
            Resource(
                uri=AnyUrl("afl://flows/{flow_id}"),
                name="Flow detail",
                description="Flow detail with workflows",
            ),
            Resource(
                uri=AnyUrl("afl://flows/{flow_id}/source"),
                name="Flow source",
                description="AFL source code for a flow",
            ),
            Resource(
                uri=AnyUrl("afl://servers"),
                name="List servers",
                description="List all registered servers",
            ),
            Resource(
                uri=AnyUrl("afl://tasks"),
                name="List tasks",
                description="List pending/active tasks",
            ),
            Resource(
                uri=AnyUrl("afl://handlers"),
                name="List handler registrations",
                description="List all handler registrations",
            ),
            Resource(
                uri=AnyUrl("afl://handlers/{facet_name}"),
                name="Handler registration detail",
                description="Handler registration detail by facet name",
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
    """Compile AFL source to JSON."""
    source = arguments.get("source", "")
    try:
        from afl import emit_dict, parse

        ast = parse(source)
        compiled = emit_dict(ast)
        result = {"success": True, "json": compiled}
    except Exception as e:
        result = {"success": False, "errors": [str(e)]}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_validate(arguments: dict[str, Any]) -> list[TextContent]:
    """Validate AFL source semantically."""
    source = arguments.get("source", "")
    try:
        from afl import parse, validate

        ast = parse(source)
        validation = validate(ast)
        if validation.is_valid:
            result = {"valid": True, "errors": []}
        else:
            result = {
                "valid": False,
                "errors": [
                    {
                        "message": e.message,
                        "line": e.line,
                        "column": e.column,
                    }
                    for e in validation.errors
                ],
            }
    except Exception as e:
        result = {"valid": False, "errors": [{"message": str(e)}]}
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _tool_execute_workflow(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a workflow from AFL source."""
    source = arguments.get("source", "")
    workflow_name = arguments.get("workflow_name", "")
    inputs = arguments.get("inputs", None)

    try:
        from afl import emit_dict, parse
        from afl.runtime import Evaluator, MemoryStore

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
        from afl.runtime import Evaluator

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
    """Retry a failed step."""
    step_id = arguments.get("step_id", "")

    try:
        from afl.runtime import Evaluator

        store = get_store()
        evaluator = Evaluator(store)
        evaluator.retry_step(step_id)
        result: dict[str, Any] = {"success": True}
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
        from afl import emit_dict, parse
        from afl.runtime import Evaluator

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

    from afl.runtime.entities import RunnerState

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


def _tool_manage_handlers(
    arguments: dict[str, Any],
    get_store: Any,
) -> list[TextContent]:
    """Manage handler registrations (list/get/register/delete)."""
    import time

    from afl.runtime.entities import HandlerRegistration

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
    """Route a resource URI to its handler."""
    store = get_store()
    parts = uri.replace("afl://", "").strip("/").split("/")

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


# =============================================================================
# Helpers
# =============================================================================


def _find_workflow(compiled: dict, workflow_name: str) -> dict | None:
    """Find a workflow declaration by name in compiled output."""
    from afl.ast_utils import find_workflow

    return find_workflow(compiled, workflow_name)


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


def _tool_postgis_query(arguments: dict[str, Any]) -> list["TextContent"]:
    """Execute a read-only SQL query against PostGIS."""
    from mcp.types import TextContent

    sql = arguments.get("sql", "").strip()
    limit = min(arguments.get("limit", 500), 5000)

    if not sql:
        return [TextContent(type="text", text=json.dumps({"error": "No SQL provided"}))]

    # Block write operations
    if _FORBIDDEN_SQL.search(sql):
        return [TextContent(
            type="text",
            text=json.dumps({"error": "Only SELECT queries are allowed"}),
        )]

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return [TextContent(
            type="text",
            text=json.dumps({"error": "psycopg2 not installed"}),
        )]

    postgis_url = os.environ.get(
        "AFL_POSTGIS_URL", "postgresql://afl:afl@localhost:5432/afl_gis"
    )

    try:
        conn = psycopg2.connect(postgis_url, options="-c default_transaction_read_only=on")
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
