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

"""Workflow-browser view-data helpers.

The new-run / authoring UI lives in the v3 routes; this module now provides the
shared helpers they import for the workflow browser — `_collect_workflows_with_ns`
and `_build_afl_snippet`.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Helpers for the workflow browser (v3 new-run)
# ---------------------------------------------------------------------------


def _collect_workflows_with_ns(node: dict, ns_prefix: str, acc: list[dict]) -> None:
    """Recursively collect WorkflowDecl nodes with their namespace path."""
    for decl in node.get("declarations", []):
        if decl.get("type") == "WorkflowDecl":
            acc.append({"ns": ns_prefix, "wf": decl})
        elif decl.get("type") == "Namespace":
            child_ns = decl.get("name", "")
            full_ns = f"{ns_prefix}.{child_ns}" if ns_prefix else child_ns
            _collect_workflows_with_ns(decl, full_ns, acc)


def _build_afl_snippet(ns: str, wf: dict) -> str:
    """Generate a minimal FFL source snippet for a workflow."""
    name = wf.get("name", "Unnamed")

    # Build params string
    param_parts: list[str] = []
    for p in wf.get("params", []):
        pname = p.get("name", "")
        ptype = p.get("type", "String")
        default = p.get("default")
        if isinstance(default, dict) and "value" in default:
            default = default["value"]
        if default is not None:
            if isinstance(default, str):
                param_parts.append(f'{pname}: {ptype} = "{default}"')
            else:
                param_parts.append(f"{pname}: {ptype} = {default}")
        else:
            param_parts.append(f"{pname}: {ptype}")
    params_str = ", ".join(param_parts)

    # Build returns string
    ret_parts: list[str] = []
    for r in wf.get("returns", []):
        rname = r.get("name", "")
        rtype = r.get("type", "String")
        ret_parts.append(f"{rname}: {rtype}")
    returns_str = f" => ({', '.join(ret_parts)})" if ret_parts else ""

    sig = f"workflow {name}({params_str}){returns_str} andThen {{"
    body = "    // Edit workflow body here"
    close = "}"

    if ns:
        indent = "    "
        lines = [
            f"namespace {ns} {{",
            f"{indent}{sig}",
            f"{indent}{body}",
            f"{indent}{close}",
            "}",
        ]
    else:
        lines = [sig, body, close]

    return "\n".join(lines)


