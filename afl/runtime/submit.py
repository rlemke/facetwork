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

"""CLI module for submitting AFL workflows to the runtime.

Usage::

    python -m afl.runtime.submit \\
        --primary workflow.afl \\
        --library types.afl \\
        --workflow "ns.WorkflowName"

Compiles AFL sources, validates them, creates runtime entities in MongoDB,
and queues the workflow for execution by the RunnerService.
"""

import argparse
import json
import logging
import sys
import time

from .expression import evaluate_default

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the submit CLI."""
    parser = argparse.ArgumentParser(
        prog="afl.runtime.submit",
        description="Submit an AFL workflow for execution",
    )

    # Legacy single-file input (backward compatible)
    parser.add_argument(
        "input",
        nargs="?",
        help="Input AFL file (legacy single-file mode). Use --primary for multi-file input.",
    )

    # Multi-source input options
    parser.add_argument(
        "--primary",
        action="append",
        dest="primary_files",
        metavar="FILE",
        help="Primary AFL source file (repeatable)",
    )

    parser.add_argument(
        "--library",
        action="append",
        dest="library_files",
        metavar="FILE",
        help="Library/dependency AFL source file (repeatable)",
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
        default="default",
        metavar="NAME",
        help="Task list name (default: default)",
    )

    # Config and logging
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to AFL config file (JSON)",
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
    from afl.ast_utils import find_workflow

    return find_workflow(program_dict, workflow_name)


def _connect_store(config):
    """Create a MongoStore from config. Separated for testability."""
    from .mongo_store import MongoStore

    return MongoStore.from_config(config.mongodb)


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
    from afl.logging import configure_logging

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
    from ..parser import AFLParser, ParseError
    from ..validator import validate

    afl_parser = AFLParser()
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
    # 5. Concatenate AFL source text for compiled_sources
    # -------------------------------------------------------------------------
    source_parts: list[str] = []
    for entry in compiler_input.all_sources:
        source_parts.append(entry.text)
    combined_source = "\n".join(source_parts)

    # -------------------------------------------------------------------------
    # 6. Connect to MongoDB and create entities
    # -------------------------------------------------------------------------
    from .entities import (
        FlowDefinition,
        FlowIdentity,
        RunnerDefinition,
        RunnerState,
        SourceText,
        TaskDefinition,
        TaskState,
        WorkflowDefinition,
    )
    from .types import generate_id

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
        compiled_sources=[SourceText(name="source.afl", content=combined_source)],
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
        date=now_ms,
    )
    store.save_workflow(workflow)

    runner = RunnerDefinition(
        uuid=runner_id,
        workflow_id=wf_id,
        workflow=workflow,
        state=RunnerState.CREATED,
    )
    store.save_runner(runner)

    task = TaskDefinition(
        uuid=task_id,
        name="afl:execute",
        runner_id=runner_id,
        workflow_id=wf_id,
        flow_id=flow_id,
        step_id="",
        state=TaskState.PENDING,
        created=now_ms,
        updated=now_ms,
        task_list_name=parsed.task_list,
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
