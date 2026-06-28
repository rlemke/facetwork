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

"""Flow view-data helpers.

The flow UI (list / detail / source / JSON / run) lives in the v3 routes; this
module now provides the shared helpers they import — `build_run_params` and
`create_flow_run` (the run-submission logic) plus the team/param utilities.
"""

from __future__ import annotations

from facetwork.runtime.expression import evaluate_default
from facetwork.runtime.task_list_routing import namespace_of


def _flow_teams(flow) -> set[str]:
    """Teams a flow belongs to — union of its workflows' Teams mixins.

    Read from the compiled AST (where the mixins live); falls back to any
    embedded workflow metadata.
    """
    from facetwork.capabilities import author_and_teams

    teams: set[str] = set()
    ast = getattr(flow, "compiled_ast", None)
    if ast:

        def _walk(decls):
            for d in decls or []:
                if not isinstance(d, dict):
                    continue
                t = d.get("type")
                if t == "Namespace":
                    _walk(d.get("declarations") or d.get("body") or [])
                elif t in ("WorkflowDecl", "Workflow"):
                    _author, tm = author_and_teams(d.get("mixins"))
                    teams.update(tm)

        _walk(ast.get("declarations"))
    for wf in getattr(flow, "workflows", []) or []:
        md = getattr(wf, "metadata", None)
        if md and getattr(md, "teams", None):
            teams.update(md.teams)
    return teams


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


def _collect_declarations(declarations: list, namespace: str, groups: dict[str, list]) -> None:
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
                        elem_name = elem.get("name", "?") if isinstance(elem, dict) else str(elem)
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
            entry["steps"].append(
                {
                    "name": step.get("name", ""),
                    "target": target,
                    "args": args,
                }
            )
            # Recurse into nested body (statement-level andThen)
            if step.get("body"):
                nested = step["body"] if isinstance(step["body"], list) else [step["body"]]
                for nb in nested:
                    for ns_step in nb.get("steps", []):
                        if ns_step.get("type") != "StepStmt":
                            continue
                        nc = ns_step.get("call", {})
                        entry["steps"].append(
                            {
                                "name": ns_step.get("name", ""),
                                "target": nc.get("target", ""),
                                "args": [a.get("name", "") for a in nc.get("args", [])],
                            }
                        )


def build_run_params(flow, workflow_name: str):
    """Extract a workflow's params from a flow's compiled AST.

    Returns ``(params, parse_error, workflow_doc)`` where each param is
    ``{name, type, default, default_json, description}``. Shared by the v2 and
    v3 run forms so they can't diverge on parameter extraction.
    """
    import json

    params: list[dict] = []
    parse_error = None
    workflow_doc = None
    if not (flow.compiled_ast or flow.compiled_sources):
        return params, parse_error, workflow_doc
    try:
        from facetwork.ast_utils import find_workflow

        if flow.compiled_ast:
            program_dict = flow.compiled_ast
        else:
            from facetwork.emitter import JSONEmitter
            from facetwork.parser import FFLParser

            parser = FFLParser()
            ast = parser.parse(flow.compiled_sources[0].content)
            program_dict = json.loads(JSONEmitter(include_locations=False).emit(ast))

        wf_ast = find_workflow(program_dict, workflow_name)
        if wf_ast:
            workflow_doc = wf_ast.get("doc")
            param_descs: dict[str, str] = {}
            if workflow_doc and isinstance(workflow_doc, dict):
                for pd in workflow_doc.get("params", []):
                    param_descs[pd.get("name", "")] = pd.get("description", "")
            for p in wf_ast.get("params", []):
                default_val = evaluate_default(p.get("default"))
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
    return params, parse_error, workflow_doc


def create_flow_run(flow, workflow_def, inputs_json, purpose, teams, store, current_user) -> str:
    """Create a Runner + bootstrap Task for a workflow from a flow; return runner_id.

    Shared by the v2 and v3 run forms so the run-submission logic (default
    extraction, user-input override, author/team tagging, runner+task creation)
    can't diverge.
    """
    import json
    import time

    from facetwork.ast_utils import find_workflow
    from facetwork.capabilities import author_and_teams
    from facetwork.emitter import JSONEmitter
    from facetwork.parser import FFLParser
    from facetwork.runtime.entities import (
        RunnerDefinition,
        RunnerState,
        TaskDefinition,
        TaskState,
        UserDefinition,
    )
    from facetwork.runtime.types import generate_id

    inputs: dict = {}
    program_dict: dict | None = None
    wf_ast: dict | None = None
    if flow.compiled_ast or flow.compiled_sources:
        try:
            if flow.compiled_ast:
                program_dict = flow.compiled_ast
            else:
                parser = FFLParser()
                ast = parser.parse(flow.compiled_sources[0].content)
                program_dict = json.loads(JSONEmitter(include_locations=False).emit(ast))
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

    try:
        user_inputs = json.loads(inputs_json) if inputs_json else {}
        inputs.update(user_inputs)
    except (json.JSONDecodeError, ValueError):
        pass

    now_ms = int(time.time() * 1000)
    runner_id = generate_id()
    execution_workflow_id = generate_id()

    mixin_author, mixin_teams = author_and_teams(wf_ast.get("mixins")) if wf_ast else ("", [])
    author_def = None
    if mixin_author:
        au = store.get_user(mixin_author)
        author_def = au.to_user_definition() if au else UserDefinition(email=mixin_author)
    run_teams = [t for t in teams if t]
    if not run_teams:
        run_teams = [current_user.default_team] if current_user.default_team else list(mixin_teams)

    store.save_runner(
        RunnerDefinition(
            uuid=runner_id,
            workflow_id=execution_workflow_id,
            workflow=workflow_def,
            state=RunnerState.CREATED,
            user=current_user.to_user_definition(),
            author=author_def,
            purpose=purpose,
            teams=run_teams,
            compiled_ast=program_dict,
            workflow_ast=wf_ast,
        )
    )
    store.save_task(
        TaskDefinition(
            uuid=generate_id(),
            name=f"fw:execute:{workflow_def.name}",
            runner_id=runner_id,
            workflow_id=execution_workflow_id,
            flow_id=flow.uuid,
            step_id="",
            state=TaskState.PENDING,
            created=now_ms,
            updated=now_ms,
            task_list_name=namespace_of(workflow_def.name),
            data={
                "flow_id": flow.uuid,
                "workflow_id": execution_workflow_id,
                "workflow_name": workflow_def.name,
                "inputs": inputs,
                "runner_id": runner_id,
            },
        )
    )
    return runner_id
