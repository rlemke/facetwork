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

"""CLI module for submitting FFL workflows to the runtime.

Usage::

    python -m afl.runtime.submit \\
        --primary workflow.afl \\
        --library types.afl \\
        --workflow "ns.WorkflowName"

Compiles FFL sources, validates them, creates runtime entities in MongoDB,
and queues the workflow for execution by the RunnerService.
"""

import argparse
import json
import logging
import sys
import time

from .expression import evaluate_default
from .task_list_routing import namespace_of

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the submit CLI."""
    parser = argparse.ArgumentParser(
        prog="facetwork.runtime.submit",
        description="Submit an FFL workflow for execution",
    )

    # Legacy single-file input (backward compatible)
    parser.add_argument(
        "input",
        nargs="?",
        help="Input FFL file (legacy single-file mode). Use --primary for multi-file input.",
    )

    # Multi-source input options
    parser.add_argument(
        "--primary",
        action="append",
        dest="primary_files",
        metavar="FILE",
        help="Primary FFL source file (repeatable)",
    )

    parser.add_argument(
        "--library",
        action="append",
        dest="library_files",
        metavar="FILE",
        help="Library/dependency FFL source file (repeatable)",
    )

    # Workflow selection
    parser.add_argument(
        "--workflow",
        required=True,
        metavar="NAME",
        help="Qualified workflow name to execute (e.g. ns.WorkflowName)",
    )

    parser.add_argument(
        "--inputs",
        default="{}",
        metavar="JSON",
        help='JSON string of input parameters (default: "{}")',
    )

    parser.add_argument(
        "--task-list",
        default=None,
        metavar="NAME",
        help=(
            "Task list name (override). If omitted, the bootstrap routes to the "
            "workflow's top-level namespace (e.g. 'osm'), which the runners "
            "serving that namespace poll."
        ),
    )

    # Config and logging
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to FFL config file (JSON)",
    )

    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: WARNING)",
    )

    parser.add_argument(
        "--log-format",
        default="json",
        choices=["json", "text"],
        help="Log format (default: json)",
    )

    return parser


def _find_workflow_in_program(program_dict: dict, workflow_name: str) -> dict | None:
    """Find a workflow in the compiled program AST by name."""
    from facetwork.ast_utils import find_workflow

    return find_workflow(program_dict, workflow_name)


def _connect_store(config):
    """Create a MongoStore from config. Separated for testability."""
    from .mongo_store import MongoStore

    return MongoStore.from_config(config.mongodb)


def _resolve_bootstrap_list(store, workflow_name: str) -> str:
    """Task list for the ``fw:execute`` bootstrap of *workflow_name*.

    Namespace routing is the default — but a namespace no live runner polls
    (a runner polls its configured list plus the namespaces of its loaded
    handlers) leaves the bootstrap unclaimed forever. Detect that at submit
    time and fall back to ``"default"``, which every runner polls. The
    workflow's OWN event/script tasks still route by their facets' namespaces
    (and script tasks claim by environment regardless of list), so this only
    moves the bootstrap. Best-effort: any store/query problem keeps the
    namespace-routed default.
    """
    target = namespace_of(workflow_name)
    if target == "default":
        return target
    try:
        from .task_list_routing import namespaces_for

        cutoff = int(time.time() * 1000) - 120_000
        for server in store.get_all_servers():
            if (server.ping_time or 0) < cutoff:
                continue
            polled = set(namespaces_for(server.handlers or [])) | {server.task_list}
            if target in polled:
                return target
        print(
            f"No live runner polls task list '{target}' — routing the "
            f"bootstrap to 'default' (any runner can start the workflow)."
        )
        return "default"
    except Exception:
        return target


def main(args: list[str] | None = None) -> int:
    """Main entry point for the submit CLI.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    parser = _build_parser()
    parsed = parser.parse_args(args)

    # Configure logging
    from facetwork.logging import configure_logging

    configure_logging(
        level=parsed.log_level,
        log_format=parsed.log_format,
    )

    # -------------------------------------------------------------------------
    # 1. Collect source files
    # -------------------------------------------------------------------------
    from ..config import load_config
    from ..loader import SourceLoader
    from ..source import CompilerInput

    config = load_config(parsed.config)

    compiler_input = CompilerInput()
    has_multi_source = parsed.primary_files or parsed.library_files

    if parsed.input and has_multi_source:
        print(
            "Error: Cannot use positional input with --primary/--library. "
            "Use --primary for the main source file.",
            file=sys.stderr,
        )
        return 1

    if parsed.input:
        try:
            entry = SourceLoader.load_file(parsed.input, is_library=False)
            compiler_input.primary_sources.append(entry)
        except FileNotFoundError:
            print(f"Error: File not found: {parsed.input}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading input: {e}", file=sys.stderr)
            return 1
    elif not has_multi_source:
        print("Error: No source files specified. Use positional arg or --primary.", file=sys.stderr)
        return 1

    for file_path in parsed.primary_files or []:
        try:
            entry = SourceLoader.load_file(file_path, is_library=False)
            compiler_input.primary_sources.append(entry)
        except FileNotFoundError:
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

    for file_path in parsed.library_files or []:
        try:
            entry = SourceLoader.load_file(file_path, is_library=True)
            compiler_input.library_sources.append(entry)
        except FileNotFoundError:
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

    # -------------------------------------------------------------------------
    # 2. Parse and validate
    # -------------------------------------------------------------------------
    from ..emitter import JSONEmitter
    from ..parser import FFLParser, ParseError
    from ..validator import validate

    afl_parser = FFLParser()
    try:
        ast, _source_registry = afl_parser.parse_sources(compiler_input)
    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1

    result = validate(ast)
    if not result.is_valid:
        for error in result.errors:
            print(f"Validation error: {error}", file=sys.stderr)
        return 1

    # -------------------------------------------------------------------------
    # 3. Compile to JSON
    # -------------------------------------------------------------------------
    emitter = JSONEmitter(include_locations=False)
    program_json = emitter.emit(ast)
    program_dict = json.loads(program_json)

    # Refuse to submit work this runtime cannot execute faithfully. This is the
    # loud, human-facing half of the forward-compat guard (facetwork/
    # ast_features.py): submitting a program that needs a construct we don't
    # implement would run it with those semantics silently missing.
    from ..ast_features import UnsupportedASTFeatureError, check_supported

    try:
        check_supported(program_dict)
    except UnsupportedASTFeatureError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Freeze environment manifests into the compiled program (resolve-and-
    # freeze at publish — docs/architecture/script-environments.md §5). The
    # annotated dict is what persists as compiled_ast, so the pinned versions
    # ride the runner snapshot hermetically.
    from ..environments import annotate_program

    env_hashes = annotate_program(program_dict)
    if env_hashes:
        print(
            f"Froze {len(env_hashes)} environment manifest(s): "
            + ", ".join(f"{n}@{h[:8]}" for n, h in sorted(env_hashes.items()))
        )

    # -------------------------------------------------------------------------
    # 4. Find workflow and extract default inputs
    # -------------------------------------------------------------------------
    workflow_ast = _find_workflow_in_program(program_dict, parsed.workflow)
    if workflow_ast is None:
        print(
            f"Error: Workflow '{parsed.workflow}' not found in compiled program.", file=sys.stderr
        )
        return 1

    inputs: dict = {}
    for param in workflow_ast.get("params", []):
        default_val = param.get("default")
        if default_val is not None:
            inputs[param["name"]] = evaluate_default(default_val)

    try:
        user_inputs = json.loads(parsed.inputs) if parsed.inputs else {}
        inputs.update(user_inputs)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error: Invalid --inputs JSON: {e}", file=sys.stderr)
        return 1

    logger.info("Workflow '%s' found with %d input(s)", parsed.workflow, len(inputs))

    # -------------------------------------------------------------------------
    # 5. Concatenate FFL source text for compiled_sources
    # -------------------------------------------------------------------------
    source_parts: list[str] = []
    for entry in compiler_input.all_sources:
        source_parts.append(entry.text)
    combined_source = "\n".join(source_parts)

    # -------------------------------------------------------------------------
    # 6. Connect to MongoDB and create entities
    # -------------------------------------------------------------------------
    from facetwork.capabilities import author_and_teams

    from .entities import (
        FlowDefinition,
        FlowIdentity,
        Parameter,
        RunnerDefinition,
        RunnerState,
        SourceText,
        TaskDefinition,
        TaskState,
        UserDefinition,
        WorkflowDefinition,
        WorkflowMetaData,
    )
    from .types import generate_id

    # Ownership tags from the workflow's `with Author(email=…)` / `with Teams(…)`
    # mixins — author the run with them so the dashboard can attribute and filter.
    author_email, flow_teams = author_and_teams(workflow_ast.get("mixins"))

    try:
        store = _connect_store(config)
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}", file=sys.stderr)
        return 1

    now_ms = int(time.time() * 1000)
    flow_id = generate_id()
    wf_id = generate_id()
    runner_id = generate_id()
    task_id = generate_id()

    # Qualify unnamespaced workflows with system.cli prefix
    wf_name = parsed.workflow
    if "." not in wf_name:
        wf_name = f"system.cli.{wf_name}"

    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name=wf_name, path="cli:submit", uuid=flow_id),
        compiled_sources=[SourceText(name="source.ffl", content=combined_source)],
        compiled_ast=program_dict,
    )
    store.save_flow(flow)

    workflow = WorkflowDefinition(
        uuid=wf_id,
        name=wf_name,
        namespace_id="cli",
        facet_id=wf_id,
        flow_id=flow_id,
        starting_step="",
        version="1.0",
        metadata=WorkflowMetaData(author=author_email, teams=flow_teams),
        date=now_ms,
    )
    store.save_workflow(workflow)

    # Resolve the author email to a rich User snapshot if one exists.
    author_def: UserDefinition | None = None
    if author_email:
        author_user = store.get_user(author_email)
        author_def = (
            author_user.to_user_definition() if author_user else UserDefinition(email=author_email)
        )

    runner = RunnerDefinition(
        uuid=runner_id,
        workflow_id=wf_id,
        workflow=workflow,
        state=RunnerState.CREATED,
        author=author_def,
        teams=list(flow_teams),
        # Same reason as the dashboard path: without the inputs on the run,
        # repeated runs of one workflow are indistinguishable in the runs list.
        parameters=[Parameter(name=k, value=v) for k, v in inputs.items()],
    )
    store.save_runner(runner)

    task = TaskDefinition(
        uuid=task_id,
        name=f"fw:execute:{wf_name}",
        runner_id=runner_id,
        workflow_id=wf_id,
        flow_id=flow_id,
        step_id="",
        state=TaskState.PENDING,
        created=now_ms,
        updated=now_ms,
        # Bootstrap routes by the workflow's namespace so a runner serving that
        # namespace claims it (namespace routing); an explicit
        # --task-list still overrides for back-compat. A namespace NO live
        # runner polls (brand-new, handler-less — e.g. a pure-script flow)
        # falls back to "default", which every runner polls; otherwise the
        # bootstrap would sit unclaimed forever.
        task_list_name=(
            parsed.task_list
            if parsed.task_list is not None
            else _resolve_bootstrap_list(store, parsed.workflow)
        ),
        data={
            "flow_id": flow_id,
            "workflow_id": wf_id,
            "workflow_name": parsed.workflow,
            "inputs": inputs,
            "runner_id": runner_id,
        },
    )
    store.save_task(task)

    store.close()

    print(f"Submitted workflow '{parsed.workflow}'")
    print(f"  Runner ID: {runner_id}")
    print(f"  Flow ID:   {flow_id}")
    print(f"  State:     {RunnerState.CREATED}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
