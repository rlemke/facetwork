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

"""Flow routes — list, detail, source, JSON, and run views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from facetwork.runtime.expression import evaluate_default

from ...dependencies import get_store

router = APIRouter(prefix="/flows")


@router.get("")
def flow_list(request: Request, q: str | None = None, store=Depends(get_store)):
    """List all flows, optionally filtered by name search."""
    flows = store.get_all_flows()
    # Filter out auto-generated CLI submissions (Run, app.Execute, test.Run, etc.)
    # These have path=cli:submit; seeded examples have path=cli:seed.
    flows = [f for f in flows if getattr(f.name, "path", "") != "cli:submit"]
    if q:
        flows = [f for f in flows if q.lower() in f.name.name.lower()]
    return request.app.state.templates.TemplateResponse(
        request,
        "flows/list.html",
        {"flows": flows, "search_query": q, "active_tab": "flows"},
    )


@router.get("/{flow_id}")
def flow_detail(flow_id: str, request: Request, store=Depends(get_store)):
    """Show flow detail with namespace-grouped workflows."""
    flow = store.get_flow(flow_id)
    workflows = store.get_workflows_by_flow(flow_id) if flow else []
    runners = []
    if flow:
        for wf in flow.workflows:
            runners.extend(store.get_runners_by_workflow(wf.uuid))
        runners.sort(key=lambda r: r.start_time, reverse=True)
        runners = runners[:20]

    # Group workflows by namespace prefix derived from qualified names
    ns_groups: dict[str, list] = {}
    for wf in workflows:
        if "." in wf.name:
            ns_prefix, _short = wf.name.rsplit(".", 1)
        else:
            ns_prefix = ""
        ns_groups.setdefault(ns_prefix, []).append(wf)

    namespace_list = sorted(
        [
            {"name": ns or "system.unnamespaced", "prefix": ns or "_top", "count": len(wfs)}
            for ns, wfs in ns_groups.items()
        ],
        key=lambda x: str(x["name"]),
    )

    # Compute declaration counts from compiled AST (entity lists may be empty)
    ast_counts = {"namespaces": 0, "workflows": 0, "event_facets": 0, "facets": 0, "schemas": 0}
    if flow and flow.compiled_ast:
        _count_declarations(flow.compiled_ast.get("declarations", []), ast_counts)

    return request.app.state.templates.TemplateResponse(
        request,
        "flows/detail.html",
        {
            "flow": flow,
            "workflows": workflows,
            "namespace_list": namespace_list,
            "runners": runners,
            "ast_counts": ast_counts,
            "active_tab": "flows",
        },
    )


@router.get("/{flow_id}/source")
def flow_source(flow_id: str, request: Request, store=Depends(get_store)):
    """Show FFL source code for a flow."""
    flow = store.get_flow(flow_id)
    sources = flow.compiled_sources if flow else []
    return request.app.state.templates.TemplateResponse(
        request,
        "flows/source.html",
        {"flow": flow, "sources": sources},
    )


@router.get("/{flow_id}/json")
def flow_json(flow_id: str, request: Request, store=Depends(get_store)):
    """Parse FFL source and show the compiled JSON output."""
    flow = store.get_flow(flow_id)
    json_output = None
    parse_error = None

    if flow:
        try:
            if flow.compiled_ast:
                import json

                json_output = json.dumps(flow.compiled_ast, indent=2)
            elif flow.compiled_sources:
                from facetwork.emitter import JSONEmitter
                from facetwork.parser import FFLParser

                parser = FFLParser()
                source_text = flow.compiled_sources[0].content
                ast = parser.parse(source_text)
                emitter = JSONEmitter(indent=2)
                json_output = emitter.emit(ast)
        except Exception as exc:
            parse_error = str(exc)

    return request.app.state.templates.TemplateResponse(
        request,
        "flows/json.html",
        {
            "flow": flow,
            "json_output": json_output,
            "parse_error": parse_error,
        },
    )


@router.get("/{flow_id}/browse")
def flow_browse(flow_id: str, request: Request, store=Depends(get_store)):
    """Browse compiled JSON grouped by declaration type with clickable cross-refs."""
    import json

    flow = store.get_flow(flow_id)
    groups: dict[str, list] = {
        "workflows": [],
        "event_facets": [],
        "facets": [],
        "schemas": [],
    }
    parse_error = None

    if flow:
        try:
            if flow.compiled_ast:
                program_dict = flow.compiled_ast
            elif flow.compiled_sources:
                from facetwork.emitter import JSONEmitter
                from facetwork.parser import FFLParser

                parser = FFLParser()
                source_text = flow.compiled_sources[0].content
                ast = parser.parse(source_text)
                emitter = JSONEmitter(include_locations=False)
                program_json = emitter.emit(ast)
                program_dict = json.loads(program_json)
            else:
                program_dict = None

            if program_dict:
                _collect_declarations(program_dict.get("declarations", []), "", groups)
        except Exception as exc:
            parse_error = str(exc)

    category_order = ["workflows", "event_facets", "facets", "schemas"]
    category_labels = {
        "workflows": "Workflows",
        "event_facets": "Event Facets",
        "facets": "Facets",
        "schemas": "Schemas",
    }

    return request.app.state.templates.TemplateResponse(
        request,
        "flows/browse.html",
        {
            "flow": flow,
            "groups": groups,
            "category_order": category_order,
            "category_labels": category_labels,
            "parse_error": parse_error,
        },
    )


def _count_declarations(declarations: list, counts: dict[str, int]) -> None:
    """Recursively count declaration types from the compiled AST."""
    for decl in declarations:
        dtype = decl.get("type", "")
        if dtype == "Namespace":
            counts["namespaces"] += 1
            _count_declarations(decl.get("declarations", []), counts)
        elif dtype == "WorkflowDecl":
            counts["workflows"] += 1
        elif dtype == "EventFacetDecl":
            counts["event_facets"] += 1
        elif dtype == "FacetDecl":
            counts["facets"] += 1
        elif dtype == "SchemaDecl":
            counts["schemas"] += 1


def _collect_declarations(
    declarations: list, namespace: str, groups: dict[str, list]
) -> None:
    """Recursively collect declarations from JSON AST into typed groups."""
    for decl in declarations:
        dtype = decl.get("type", "")
        name = decl.get("name", "")
        qualified = f"{namespace}.{name}" if namespace else name

        if dtype == "Namespace":
            _collect_declarations(decl.get("declarations", []), qualified, groups)
            continue

        entry: dict = {
            "qualified_name": qualified,
            "short_name": name,
            "namespace": namespace or None,
            "doc": None,
            "params": [],
            "returns": [],
            "steps": [],
            "fields": [],
        }

        # Extract doc comment
        doc = decl.get("doc")
        if doc and isinstance(doc, dict):
            entry["doc"] = doc.get("description", "")

        # Extract params
        for p in decl.get("params", []):
            ptype = p.get("type", "")
            if isinstance(ptype, dict):
                ptype = ptype.get("name", ptype.get("type", "?"))
            entry["params"].append({"name": p.get("name", ""), "type": ptype})

        # Extract returns
        for r in decl.get("returns", []):
            rtype = r.get("type", "")
            if isinstance(rtype, dict):
                rtype = rtype.get("name", rtype.get("type", "?"))
            entry["returns"].append({"name": r.get("name", ""), "type": rtype})

        if dtype == "WorkflowDecl":
            _collect_steps(decl, entry, namespace)
            groups["workflows"].append(entry)
        elif dtype == "EventFacetDecl":
            _collect_steps(decl, entry, namespace)
            groups["event_facets"].append(entry)
        elif dtype == "FacetDecl":
            _collect_steps(decl, entry, namespace)
            groups["facets"].append(entry)
        elif dtype == "SchemaDecl":
            for f in decl.get("fields", []):
                ftype = f.get("type", "")
                if isinstance(ftype, dict):
                    inner = ftype
                    if inner.get("type") == "ArrayType":
                        elem = inner.get("elementType", {})
                        elem_name = (
                            elem.get("name", "?") if isinstance(elem, dict) else str(elem)
                        )
                        ftype = f"[{elem_name}]"
                    else:
                        ftype = inner.get("name", inner.get("type", "?"))
                entry["fields"].append({"name": f.get("name", ""), "type": ftype})
            groups["schemas"].append(entry)


def _collect_steps(decl: dict, entry: dict, namespace: str) -> None:
    """Extract step statements from a declaration body."""
    body = decl.get("body")
    if body is None:
        return
    bodies = body if isinstance(body, list) else [body]
    for b in bodies:
        for step in b.get("steps", []):
            if step.get("type") != "StepStmt":
                continue
            call = step.get("call", {})
            target = call.get("target", "")
            args = [a.get("name", "") for a in call.get("args", [])]
            # Also recurse into statement-level andThen body
            entry["steps"].append({
                "name": step.get("name", ""),
                "target": target,
                "args": args,
            })
            # Recurse into nested body (statement-level andThen)
            if step.get("body"):
                nested = step["body"] if isinstance(step["body"], list) else [step["body"]]
                for nb in nested:
                    for ns_step in nb.get("steps", []):
                        if ns_step.get("type") != "StepStmt":
                            continue
                        nc = ns_step.get("call", {})
                        entry["steps"].append({
                            "name": ns_step.get("name", ""),
                            "target": nc.get("target", ""),
                            "args": [a.get("name", "") for a in nc.get("args", [])],
                        })


@router.get("/{flow_id}/ns/{namespace_name:path}")
def flow_namespace(
    flow_id: str,
    namespace_name: str,
    request: Request,
    store=Depends(get_store),
):
    """Show workflows within a specific namespace of a flow."""
    flow = store.get_flow(flow_id)
    all_workflows = store.get_workflows_by_flow(flow_id) if flow else []

    # Filter workflows by namespace prefix
    if namespace_name == "_top":
        filtered = [wf for wf in all_workflows if "." not in wf.name]
        display_name = "system.unnamespaced"
    else:
        filtered = [
            wf
            for wf in all_workflows
            if "." in wf.name and wf.name.rsplit(".", 1)[0] == namespace_name
        ]
        display_name = namespace_name

    # Build display list with short names
    ns_workflows = []
    for wf in filtered:
        short_name = wf.name.rsplit(".", 1)[1] if "." in wf.name else wf.name
        ns_workflows.append({"wf": wf, "short_name": short_name})

    # Filter facets by namespace prefix
    ns_facets = []
    if flow:
        for facet in flow.facets:
            if namespace_name == "_top":
                if "." not in facet.name:
                    ns_facets.append({"facet": facet, "short_name": facet.name})
            elif "." in facet.name and facet.name.rsplit(".", 1)[0] == namespace_name:
                short_name = facet.name.rsplit(".", 1)[1]
                ns_facets.append({"facet": facet, "short_name": short_name})

    return request.app.state.templates.TemplateResponse(
        request,
        "flows/namespace.html",
        {
            "flow": flow,
            "namespace_name": display_name,
            "ns_workflows": ns_workflows,
            "ns_facets": ns_facets,
        },
    )


@router.get("/{flow_id}/run/{workflow_id}")
def flow_run_form(
    flow_id: str,
    workflow_id: str,
    request: Request,
    store=Depends(get_store),
):
    """Show parameter input form for running a workflow from a flow."""
    import json

    flow = store.get_flow(flow_id)
    if not flow:
        return request.app.state.templates.TemplateResponse(
            request,
            "flows/detail.html",
            {"flow": None, "workflows": [], "runners": []},
        )
    workflow_def = store.get_workflow(workflow_id)
    workflow_name = workflow_def.name if workflow_def else "Unknown"
    params: list[dict] = []
    parse_error = None
    workflow_doc = None

    if flow.compiled_ast or flow.compiled_sources:
        try:
            from facetwork.ast_utils import find_workflow

            if flow.compiled_ast:
                program_dict = flow.compiled_ast
            else:
                from facetwork.emitter import JSONEmitter
                from facetwork.parser import FFLParser

                parser = FFLParser()
                source_text = flow.compiled_sources[0].content
                ast = parser.parse(source_text)
                emitter = JSONEmitter(include_locations=False)
                program_json = emitter.emit(ast)
                program_dict = json.loads(program_json)

            wf_ast = find_workflow(program_dict, workflow_name)
            if wf_ast:
                workflow_doc = wf_ast.get("doc")

                # Build param description lookup from @param tags
                param_descs: dict[str, str] = {}
                if workflow_doc and isinstance(workflow_doc, dict):
                    for pd in workflow_doc.get("params", []):
                        param_descs[pd.get("name", "")] = pd.get("description", "")

                for p in wf_ast.get("params", []):
                    default_val = evaluate_default(p.get("default"))
                    # Render as JSON for complex types so the form round-trips correctly
                    default_json: str | None
                    if isinstance(default_val, (list, dict)):
                        default_json = json.dumps(default_val)
                    else:
                        default_json = str(default_val) if default_val is not None else None
                    params.append(
                        {
                            "name": p.get("name", ""),
                            "type": p.get("type", ""),
                            "default": default_val,
                            "default_json": default_json,
                            "description": param_descs.get(p.get("name", ""), ""),
                        }
                    )
        except Exception as exc:
            parse_error = str(exc)

    return request.app.state.templates.TemplateResponse(
        request,
        "flows/run.html",
        {
            "flow": flow,
            "workflow_def": workflow_def,
            "workflow_name": workflow_name,
            "params": params,
            "parse_error": parse_error,
            "workflow_doc": workflow_doc,
        },
    )


@router.post("/{flow_id}/run/{workflow_id}")
def flow_run_execute(
    flow_id: str,
    workflow_id: str,
    request: Request,
    inputs_json: str = Form("{}"),
    store=Depends(get_store),
):
    """Execute a workflow from an existing flow — creates only Runner + Task."""
    import json
    import time

    from facetwork.ast_utils import find_workflow
    from facetwork.emitter import JSONEmitter
    from facetwork.parser import FFLParser
    from facetwork.runtime.entities import (
        RunnerDefinition,
        RunnerState,
        TaskDefinition,
        TaskState,
    )
    from facetwork.runtime.types import generate_id

    flow = store.get_flow(flow_id)
    workflow_def = store.get_workflow(workflow_id) if flow else None

    if not flow or not workflow_def:
        return request.app.state.templates.TemplateResponse(
            request,
            "flows/detail.html",
            {"flow": flow, "workflows": [], "runners": []},
        )

    # Extract defaults from compiled AST and capture for runner snapshot
    inputs: dict = {}
    program_dict: dict | None = None
    wf_ast: dict | None = None
    if flow.compiled_ast or flow.compiled_sources:
        try:
            if flow.compiled_ast:
                program_dict = flow.compiled_ast
            else:
                parser = FFLParser()
                source_text = flow.compiled_sources[0].content
                ast = parser.parse(source_text)
                emitter = JSONEmitter(include_locations=False)
                program_json = emitter.emit(ast)
                program_dict = json.loads(program_json)

            if program_dict is None:
                raise ValueError("Flow has no compiled AST or sources")
            wf_ast = find_workflow(program_dict, workflow_def.name)
            if wf_ast:
                for param in wf_ast.get("params", []):
                    default_val = param.get("default")
                    if default_val is not None:
                        inputs[param["name"]] = evaluate_default(default_val)
        except Exception:
            pass

    # Override with user-provided inputs from form
    try:
        user_inputs = json.loads(inputs_json) if inputs_json else {}
        inputs.update(user_inputs)
    except (json.JSONDecodeError, ValueError):
        pass

    # Create Runner + Task with a unique execution workflow_id.
    # Each run gets its own workflow_id so steps don't collide between runs
    # of the same workflow definition.
    now_ms = int(time.time() * 1000)
    runner_id = generate_id()
    task_id = generate_id()
    execution_workflow_id = generate_id()

    runner = RunnerDefinition(
        uuid=runner_id,
        workflow_id=execution_workflow_id,
        workflow=workflow_def,
        state=RunnerState.CREATED,
        compiled_ast=program_dict,
        workflow_ast=wf_ast,
    )
    store.save_runner(runner)

    task = TaskDefinition(
        uuid=task_id,
        name=f"fw:execute:{workflow_def.name}",
        runner_id=runner_id,
        workflow_id=execution_workflow_id,
        flow_id=flow_id,
        step_id="",
        state=TaskState.PENDING,
        created=now_ms,
        updated=now_ms,
        task_list_name="default",
        data={
            "flow_id": flow_id,
            "workflow_id": execution_workflow_id,
            "workflow_name": workflow_def.name,
            "inputs": inputs,
            "runner_id": runner_id,
        },
    )
    store.save_task(task)

    return RedirectResponse(
        url=f"/runners/{runner_id}",
        status_code=303,
    )
