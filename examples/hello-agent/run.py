#!/usr/bin/env python3
"""Hello Agent Example - End-to-End Workflow Execution

This script demonstrates the complete Facetwork execution cycle:

    +-------------+     +-------------+     +-------------+
    |   Compile   |---->|   Execute   |---->|    Agent    |
    |  .afl->JSON |     |  (pauses)   |     |  processes  |
    +-------------+     +-------------+     +-------------+
                                                  |
    +-------------+     +-------------+           |
    |   Output    |<----|   Resume    |<----------+
    |   results   |     |  (completes)|
    +-------------+     +-------------+

Run it:
    python examples/hello-agent/run.py

What you'll see:
    1. FFL source compiled to JSON
    2. Workflow executes until it hits the event facet (Greet)
    3. Agent polls for tasks, processes the Greet event
    4. Workflow resumes and completes
    5. Final output is printed
"""

from pathlib import Path

from facetwork import emit_dict, parse
from facetwork.ast_utils import find_all_workflows
from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main():
    # -------------------------------------------------------------------------
    # Step 1: Compile FFL source
    # -------------------------------------------------------------------------

    afl_path = Path(__file__).parent / "workflow.ffl"
    source = afl_path.read_text()

    print("=" * 60)
    print("STEP 1: Compile FFL source")
    print("=" * 60)
    print(f"\nSource file: {afl_path}\n")
    print(source)

    ast = parse(source)
    compiled = emit_dict(ast)
    all_wfs = find_all_workflows(compiled)
    workflow_ast = all_wfs[0]
    program_ast = compiled

    ns_count = len([d for d in compiled.get("declarations", []) if d.get("type") == "Namespace"])
    print(f"Compiled to JSON: {ns_count} namespace(s), {len(all_wfs)} workflow(s)")
    print(f"Workflow: {workflow_ast['name']}")

    # -------------------------------------------------------------------------
    # Step 2: Execute workflow - pauses at event facet
    # -------------------------------------------------------------------------

    print("\n" + "=" * 60)
    print("STEP 2: Execute workflow (pauses at event facet)")
    print("=" * 60)

    # MemoryStore keeps everything in-process (no MongoDB needed)
    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    inputs = {"name": "World"}
    print(f"\nInputs: {inputs}")

    result = evaluator.execute(workflow_ast, inputs=inputs, program_ast=program_ast)

    print(f"Status: {result.status}")
    print(f"Workflow ID: {result.workflow_id}")

    if result.status != ExecutionStatus.PAUSED:
        print(f"ERROR: Expected PAUSED, got {result.status}")
        return

    print("\nWorkflow paused - waiting for agent to process 'hello.Greet' event")

    # -------------------------------------------------------------------------
    # Step 3: Agent processes the event
    # -------------------------------------------------------------------------

    print("\n" + "=" * 60)
    print("STEP 3: Agent processes the event")
    print("=" * 60)

    def greet_handler(payload: dict) -> dict:
        """Agent logic: receives name, returns greeting message."""
        name = payload.get("name", "stranger")
        message = f"Hello, {name}!"
        print(f"\n  Agent received: name = {name!r}")
        print(f"  Agent returns:  message = {message!r}")
        return {"message": message}

    poller = AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=AgentPollerConfig(service_name="hello-agent"),
    )
    poller.register("hello.Greet", greet_handler)

    # Cache AST for resume (in production, stored in MongoDB)
    poller.cache_workflow_ast(result.workflow_id, workflow_ast)

    print("\nAgent polling for tasks...")
    dispatched = poller.poll_once()
    print(f"Dispatched {dispatched} task(s)")

    # -------------------------------------------------------------------------
    # Step 4: Resume workflow to completion
    # -------------------------------------------------------------------------

    print("\n" + "=" * 60)
    print("STEP 4: Resume workflow to completion")
    print("=" * 60)

    final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)

    print(f"\nStatus: {final.status}")
    print(f"Iterations: {final.iterations}")

    if final.status == ExecutionStatus.COMPLETED:
        print(f"\nOutputs: {final.outputs}")
        print("\n" + "=" * 60)
        print(f"SUCCESS: {final.outputs.get('greeting')}")
        print("=" * 60)
    else:
        print(f"\nERROR: {final.error}")


if __name__ == "__main__":
    main()
