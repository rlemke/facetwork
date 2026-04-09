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

"""Entity-to-dict converters for MCP responses."""

from __future__ import annotations

from typing import Any

from facetwork.runtime.entities import (
    FlowDefinition,
    HandlerRegistration,
    LogDefinition,
    RunnerDefinition,
    ServerDefinition,
    TaskDefinition,
)
from facetwork.runtime.evaluator import ExecutionResult
from facetwork.runtime.step import StepDefinition


def serialize_runner(runner: RunnerDefinition) -> dict[str, Any]:
    """Serialize a RunnerDefinition to a dict."""
    return {
        "uuid": runner.uuid,
        "workflow_id": runner.workflow_id,
        "workflow_name": runner.workflow.name,
        "state": runner.state,
        "start_time": runner.start_time,
        "end_time": runner.end_time,
        "duration": runner.duration,
        "parameters": [
            {"name": p.name, "value": p.value, "type_hint": p.type_hint} for p in runner.parameters
        ],
    }


def serialize_step(step: StepDefinition) -> dict[str, Any]:
    """Serialize a StepDefinition to a dict."""
    result: dict[str, Any] = {
        "id": step.id,
        "workflow_id": step.workflow_id,
        "object_type": step.object_type,
        "state": step.state,
        "statement_id": step.statement_id,
        "container_id": step.container_id,
        "block_id": step.block_id,
    }
    # Include facet name if present
    if step.facet_name:
        result["facet_name"] = step.facet_name
    # Include params and returns
    params = {}
    for name, attr in step.attributes.params.items():
        params[name] = attr.value
    if params:
        result["params"] = params
    returns = {}
    for name, attr in step.attributes.returns.items():
        returns[name] = attr.value
    if returns:
        result["returns"] = returns
    return result


def serialize_flow(flow: FlowDefinition) -> dict[str, Any]:
    """Serialize a FlowDefinition to a dict."""
    return {
        "uuid": flow.uuid,
        "name": flow.name.name,
        "path": flow.name.path,
        "workflows": [
            {
                "uuid": w.uuid,
                "name": w.name,
                "version": w.version,
            }
            for w in flow.workflows
        ],
        "sources": len(flow.compiled_sources),
        "facets": len(flow.facets),
    }


def serialize_flow_source(flow: FlowDefinition) -> dict[str, Any]:
    """Serialize a FlowDefinition's source text."""
    sources = []
    for src in flow.compiled_sources:
        sources.append(
            {
                "name": src.name,
                "content": src.content,
                "language": src.language,
            }
        )
    return {
        "uuid": flow.uuid,
        "name": flow.name.name,
        "sources": sources,
    }


def serialize_task(task: TaskDefinition) -> dict[str, Any]:
    """Serialize a TaskDefinition to a dict."""
    return {
        "uuid": task.uuid,
        "name": task.name,
        "runner_id": task.runner_id,
        "workflow_id": task.workflow_id,
        "flow_id": task.flow_id,
        "step_id": task.step_id,
        "state": task.state,
        "created": task.created,
        "updated": task.updated,
        "task_list_name": task.task_list_name,
        "data_type": task.data_type,
    }


def serialize_log(log: LogDefinition) -> dict[str, Any]:
    """Serialize a LogDefinition to a dict."""
    return {
        "uuid": log.uuid,
        "order": log.order,
        "runner_id": log.runner_id,
        "step_id": log.step_id,
        "note_type": log.note_type,
        "note_originator": log.note_originator,
        "note_importance": log.note_importance,
        "message": log.message,
        "state": log.state,
        "time": log.time,
    }


def serialize_server(server: ServerDefinition) -> dict[str, Any]:
    """Serialize a ServerDefinition to a dict."""
    return {
        "uuid": server.uuid,
        "server_group": server.server_group,
        "service_name": server.service_name,
        "server_name": server.server_name,
        "state": server.state,
        "start_time": server.start_time,
        "ping_time": server.ping_time,
        "topics": server.topics,
        "handlers": server.handlers,
        "handled": [
            {"handler": h.handler, "handled": h.handled, "not_handled": h.not_handled}
            for h in server.handled
        ],
    }


def serialize_handler_registration(registration: HandlerRegistration) -> dict[str, Any]:
    """Serialize a HandlerRegistration to a dict."""
    return {
        "facet_name": registration.facet_name,
        "module_uri": registration.module_uri,
        "entrypoint": registration.entrypoint,
        "version": registration.version,
        "checksum": registration.checksum,
        "timeout_ms": registration.timeout_ms,
        "requirements": registration.requirements,
        "metadata": registration.metadata,
        "created": registration.created,
        "updated": registration.updated,
    }


def serialize_execution_result(result: ExecutionResult) -> dict[str, Any]:
    """Serialize an ExecutionResult to a dict."""
    d: dict[str, Any] = {
        "success": result.success,
        "workflow_id": result.workflow_id,
        "status": result.status,
        "iterations": result.iterations,
        "outputs": result.outputs,
    }
    if result.error:
        d["error"] = str(result.error)
    return d
