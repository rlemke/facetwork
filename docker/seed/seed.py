#!/usr/bin/env python3
"""
Seed script - Populates MongoDB with example workflows.

This script:
1. Seeds inline example workflows (addone, chain, parallel)
2. Discovers and seeds FFL files from examples/ directories
3. Creates proper FlowDefinition + WorkflowDefinition entities
   so the Dashboard "Run" button works
4. Seeds handler registrations, a sample runner execution trace,
   a server registration, and a published source so every
   dashboard page shows meaningful data out of the box.

Run with: docker compose --profile seed run --rm seed
"""

import glob
import json
import logging
import os
import sys
import time

# Add parent to path for afl imports
sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed")

SEED_PATH = "docker:seed"

# Inline example FFL sources
INLINE_SOURCES = {
    "addone-example": """
/** Core handler namespace with arithmetic and greeting facets. */
namespace handlers {
    /** Increments a value by one. */
    event facet AddOne(value: Long) => (result: Long)
    /** Multiplies two values together. */
    event facet Multiply(a: Long, b: Long) => (result: Long)
    /** Generates a greeting message for the given name. */
    event facet Greet(name: String) => (message: String)

    /** Simple workflow that adds one to the input value. */
    workflow AddOneWorkflow(input: Long) => (output: Long) andThen {
        added = AddOne(value = $.input)
        yield AddOneWorkflow(output = added.result)
    }

    /**
     * Adds one twice in sequence.
     * @param input The starting value.
     * @return output The input plus two.
     */
    workflow DoubleAddOne(input: Long) => (output: Long) andThen {
        first = AddOne(value = $.input)
        second = AddOne(value = first.result)
        yield DoubleAddOne(output = second.result)
    }

    /** Multiplies two values and then increments the product. */
    workflow MultiplyAndAdd(a: Long, b: Long) => (result: Long) andThen {
        product = Multiply(a = $.a, b = $.b)
        incremented = AddOne(value = product.result)
        yield MultiplyAndAdd(result = incremented.result)
    }

    /** Greets a user and returns a counter starting at one. */
    workflow GreetAndCount(name: String) => (greeting: String, count: Long) andThen {
        hello = Greet(name = $.name)
        one = AddOne(value = 0)
        yield GreetAndCount(greeting = hello.message, count = one.result)
    }
}
""",
    "chain-example": """
// Chain workflow - multiple steps in sequence
namespace chain {
    use handlers

    workflow ChainOfThree(start: Long) => (final: Long) andThen {
        step1 = handlers.AddOne(value = $.start)
        step2 = handlers.AddOne(value = step1.result)
        step3 = handlers.AddOne(value = step2.result)
        yield ChainOfThree(final = step3.result)
    }
}
""",
    "parallel-example": """
// Parallel workflow - demonstrates concurrent step execution
namespace parallel {
    use handlers

    // Two independent AddOne calls can execute in parallel
    workflow ParallelAdd(a: Long, b: Long) => (sumPlusTwo: Long) andThen {
        // These two steps have no dependencies on each other
        addedA = handlers.AddOne(value = $.a)
        addedB = handlers.AddOne(value = $.b)
        // This step depends on both previous steps
        product = handlers.Multiply(a = addedA.result, b = addedB.result)
        yield ParallelAdd(sumPlusTwo = product.result)
    }
}
""",
}


def _collect_workflows(node: dict, prefix: str = "") -> list[tuple[str, dict]]:
    """Collect all (qualified_name, workflow_dict) from compiled JSON."""
    results: list[tuple[str, dict]] = []

    for w in node.get("workflows", []):
        qname = f"{prefix}{w['name']}" if prefix else w["name"]
        results.append((qname, w))

    for decl in node.get("declarations", []):
        if decl.get("type") == "WorkflowDecl":
            qname = f"{prefix}{decl['name']}" if prefix else decl["name"]
            results.append((qname, decl))
        elif decl.get("type") == "Namespace":
            ns_prefix = f"{prefix}{decl['name']}."
            results.extend(_collect_workflows(decl, ns_prefix))

    for ns in node.get("namespaces", []):
        ns_prefix = f"{prefix}{ns['name']}."
        results.extend(_collect_workflows(ns, ns_prefix))

    return results


def _extract_flow_structure(program_dict: dict):
    """Extract structural definitions from compiled JSON for FlowDefinition.

    Returns (namespaces, facets, blocks, statements) lists.
    """
    from facetwork.runtime.entities import (
        BlockDefinition,
        FacetDefinition,
        NamespaceDefinition,
        Parameter,
        StatementDefinition,
    )
    from facetwork.runtime.types import generate_id

    namespaces = []
    facets = []
    blocks = []
    statements = []

    def _walk_namespace(ns: dict, prefix: str = "") -> None:
        ns_id = generate_id()
        ns_name = f"{prefix}{ns['name']}" if prefix else ns["name"]
        namespaces.append(
            NamespaceDefinition(
                uuid=ns_id,
                name=ns_name,
                documentation=ns.get("doc"),
            )
        )

        # Walk declarations only (superset of eventFacets + workflows)
        for decl in ns.get("declarations", []):
            decl_type = decl.get("type", "")
            if decl_type in ("EventFacetDecl", "FacetDecl"):
                params = [
                    Parameter(name=p["name"], value=None, type_hint=p.get("type", "Any"))
                    for p in decl.get("params", [])
                ]
                ret_type = None
                if decl.get("returns"):
                    ret_names = [r.get("name", "") for r in decl["returns"]]
                    ret_type = ", ".join(ret_names)
                facets.append(
                    FacetDefinition(
                        uuid=decl.get("id", generate_id()),
                        name=f"{ns_name}.{decl['name']}",
                        namespace_id=ns_id,
                        parameters=params,
                        return_type=ret_type,
                        documentation=decl.get("doc"),
                    )
                )
            elif decl_type == "WorkflowDecl":
                _walk_workflow_body(decl, ns_name)
            elif decl_type == "Namespace":
                _walk_namespace(decl, f"{ns_name}.")

    def _walk_workflow_body(wf: dict, ns_name: str) -> None:
        body = wf.get("body")
        if not body or not isinstance(body, dict):
            return
        blk_type = body.get("type", "")
        if blk_type in ("AndThenBlock", "AndMapBlock", "AndMatchBlock"):
            blk_id = generate_id()
            blocks.append(
                BlockDefinition(
                    uuid=blk_id,
                    name=f"{ns_name}.{wf['name']}.body",
                    block_type=blk_type.replace("Block", ""),
                )
            )
            for step in body.get("steps", []):
                statements.append(
                    StatementDefinition(
                        uuid=step.get("id", generate_id()),
                        name=step.get("name", ""),
                        statement_type="VariableAssignment",
                        block_id=blk_id,
                    )
                )
            if body.get("yield"):
                y = body["yield"]
                statements.append(
                    StatementDefinition(
                        uuid=y.get("id", generate_id()),
                        name="yield",
                        statement_type="YieldAssignment",
                        block_id=blk_id,
                    )
                )

    # Use only 'namespaces' at top level (avoids duplicates with 'declarations')
    for ns in program_dict.get("namespaces", []):
        _walk_namespace(ns)

    return namespaces, facets, blocks, statements


def seed_inline_source(name: str, source: str, store) -> tuple[str, int]:
    """Seed a single inline FFL source. Returns (flow_id, workflow_count)."""
    from facetwork.emitter import JSONEmitter
    from facetwork.parser import FFLParser
    from facetwork.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from facetwork.runtime.types import generate_id

    parser = FFLParser()
    ast = parser.parse(source, filename=f"{name}.ffl")

    emitter = JSONEmitter(include_locations=False)
    program_json = emitter.emit(ast)
    program_dict = json.loads(program_json)

    workflows = _collect_workflows(program_dict)
    if not workflows:
        return ("", 0)

    now_ms = int(time.time() * 1000)
    flow_id = generate_id()

    ns_defs, facet_defs, block_defs, stmt_defs = _extract_flow_structure(program_dict)

    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name=name, path=SEED_PATH, uuid=flow_id),
        compiled_sources=[SourceText(name=f"{name}.ffl", content=source)],
        compiled_ast=program_dict,
        namespaces=ns_defs,
        facets=facet_defs,
        blocks=block_defs,
        statements=stmt_defs,
    )
    store.save_flow(flow)

    for qname, wf_dict in workflows:
        wf_id = generate_id()
        workflow = WorkflowDefinition(
            uuid=wf_id,
            name=qname,
            namespace_id=SEED_PATH,
            facet_id=wf_id,
            flow_id=flow_id,
            starting_step="",
            version="1.0",
            date=now_ms,
            documentation=wf_dict.get("doc"),
        )
        store.save_workflow(workflow)

    return (flow_id, len(workflows))


def seed_example_directory(name: str, afl_files: list[str], store) -> int:
    """Seed an example directory's FFL files. Returns workflow count."""
    from facetwork.ast import Program
    from facetwork.emitter import JSONEmitter
    from facetwork.parser import FFLParser
    from facetwork.runtime.entities import (
        FlowDefinition,
        FlowIdentity,
        SourceText,
        WorkflowDefinition,
    )
    from facetwork.runtime.types import generate_id

    parser = FFLParser()
    programs = []
    source_parts = []

    for path in afl_files:
        with open(path) as f:
            text = f.read()
        source_parts.append(text)
        programs.append(parser.parse(text, filename=path))

    merged = Program.merge(programs)

    emitter = JSONEmitter(include_locations=False)
    program_json = emitter.emit(merged)
    program_dict = json.loads(program_json)

    workflows = _collect_workflows(program_dict)
    if not workflows:
        return 0

    now_ms = int(time.time() * 1000)
    flow_id = generate_id()
    combined_source = "\n".join(source_parts)

    ns_defs, facet_defs, block_defs, stmt_defs = _extract_flow_structure(program_dict)

    flow = FlowDefinition(
        uuid=flow_id,
        name=FlowIdentity(name=name, path=SEED_PATH, uuid=flow_id),
        compiled_sources=[SourceText(name="source.ffl", content=combined_source)],
        compiled_ast=program_dict,
        namespaces=ns_defs,
        facets=facet_defs,
        blocks=block_defs,
        statements=stmt_defs,
    )
    store.save_flow(flow)

    for qname, wf_dict in workflows:
        wf_id = generate_id()
        workflow = WorkflowDefinition(
            uuid=wf_id,
            name=qname,
            namespace_id=SEED_PATH,
            facet_id=wf_id,
            flow_id=flow_id,
            starting_step="",
            version="1.0",
            date=now_ms,
            documentation=wf_dict.get("doc"),
        )
        store.save_workflow(workflow)

    return len(workflows)


def seed_handler_registrations(store) -> int:
    """Seed handler registrations for inline event facets. Returns count."""
    from facetwork.runtime.entities import HandlerRegistration

    now_ms = int(time.time() * 1000)
    handlers = [
        HandlerRegistration(
            facet_name="handlers.AddOne",
            module_uri="agents.addone_agent",
            entrypoint="handle_addone",
            metadata={"seeded_by": SEED_PATH},
            created=now_ms,
            updated=now_ms,
        ),
        HandlerRegistration(
            facet_name="handlers.Multiply",
            module_uri="agents.addone_agent",
            entrypoint="handle_multiply",
            metadata={"seeded_by": SEED_PATH},
            created=now_ms,
            updated=now_ms,
        ),
        HandlerRegistration(
            facet_name="handlers.Greet",
            module_uri="agents.addone_agent",
            entrypoint="handle_greet",
            metadata={"seeded_by": SEED_PATH},
            created=now_ms,
            updated=now_ms,
        ),
    ]
    for h in handlers:
        store.save_handler_registration(h)
    return len(handlers)


def seed_sample_runner(flow_id: str, store) -> tuple[str, list[str]]:
    """Seed a completed sample runner for AddOneWorkflow(input=5).

    Returns (runner_id, [workflow_id_used]).
    """
    from facetwork.runtime.entities import (
        LogDefinition,
        Parameter,
        RunnerDefinition,
        TaskDefinition,
        WorkflowDefinition,
    )
    from facetwork.runtime.persistence import EventDefinition
    from facetwork.runtime.states import EventState, StepState
    from facetwork.runtime.step import StepDefinition
    from facetwork.runtime.types import (
        AttributeValue,
        FacetAttributes,
        ObjectType,
        WorkflowId,
        event_id,
        generate_id,
        step_id,
    )

    now_ms = int(time.time() * 1000)

    # Find the AddOneWorkflow definition
    db = store._db
    wf_doc = db.workflows.find_one({"name": "handlers.AddOneWorkflow", "flow_id": flow_id})
    if not wf_doc:
        logger.warning("  Could not find handlers.AddOneWorkflow for sample runner")
        return ("", [])

    wf_id = wf_doc["uuid"]
    workflow = WorkflowDefinition(
        uuid=wf_id,
        name="handlers.AddOneWorkflow",
        namespace_id=SEED_PATH,
        facet_id=wf_id,
        flow_id=flow_id,
        starting_step="",
        version="1.0",
        date=wf_doc.get("date", now_ms),
    )

    runner_id = generate_id()
    start_time = now_ms - 142
    end_time = now_ms

    # -- Runner --
    runner = RunnerDefinition(
        uuid=runner_id,
        workflow_id=wf_id,
        workflow=workflow,
        parameters=[Parameter(name="input", value=5, type_hint="Long")],
        start_time=start_time,
        end_time=end_time,
        duration=142,
        state="completed",
    )
    store.save_runner(runner)

    # -- Steps --
    added_step_id = step_id()
    added_step = StepDefinition(
        id=added_step_id,
        object_type=ObjectType.VARIABLE_ASSIGNMENT,
        workflow_id=WorkflowId(wf_id),
        statement_name="added",
        facet_name="handlers.AddOne",
        state=StepState.STATEMENT_COMPLETE,
        attributes=FacetAttributes(
            params={"value": AttributeValue(name="value", value=5, type_hint="Long")},
            returns={"result": AttributeValue(name="result", value=6, type_hint="Long")},
        ),
        start_time=start_time,
        last_modified=start_time + 100,
    )
    store.save_step(added_step)

    yield_step_id = step_id()
    yield_step = StepDefinition(
        id=yield_step_id,
        object_type=ObjectType.YIELD_ASSIGNMENT,
        workflow_id=WorkflowId(wf_id),
        statement_name="yield",
        facet_name="handlers.AddOneWorkflow",
        state=StepState.STATEMENT_COMPLETE,
        attributes=FacetAttributes(
            params={"output": AttributeValue(name="output", value=6, type_hint="Long")},
        ),
        start_time=start_time + 100,
        last_modified=end_time,
    )
    store.save_step(yield_step)

    # -- Event --
    evt_id = event_id()
    event = EventDefinition(
        id=evt_id,
        step_id=added_step_id,
        workflow_id=WorkflowId(wf_id),
        state=EventState.COMPLETED,
        event_type="handlers.AddOne",
        payload={"value": 5},
    )
    store.save_event(event)

    # -- Task --
    task = TaskDefinition(
        uuid=generate_id(),
        name="handlers.AddOne",
        runner_id=runner_id,
        workflow_id=wf_id,
        flow_id=flow_id,
        step_id=str(added_step_id),
        state="completed",
        created=start_time,
        updated=end_time,
    )
    store.save_task(task)

    # -- Logs --
    logs = [
        LogDefinition(
            uuid=generate_id(),
            order=1,
            runner_id=runner_id,
            message="Workflow handlers.AddOneWorkflow started with input=5",
            note_type="info",
            state="running",
            time=start_time,
        ),
        LogDefinition(
            uuid=generate_id(),
            order=2,
            runner_id=runner_id,
            step_id=str(added_step_id),
            message="Step added completed: AddOne(value=5) => {result: 6}",
            note_type="info",
            state="completed",
            time=start_time + 100,
        ),
        LogDefinition(
            uuid=generate_id(),
            order=3,
            runner_id=runner_id,
            message="Workflow handlers.AddOneWorkflow completed with output=6",
            note_type="info",
            state="completed",
            time=end_time,
        ),
    ]
    for log in logs:
        store.save_log(log)

    return (runner_id, [wf_id])


def seed_server(store) -> None:
    """Seed a server registration for the addone-agent."""
    from facetwork.runtime.entities import HandledCount, ServerDefinition
    from facetwork.runtime.types import generate_id

    now_ms = int(time.time() * 1000)
    server = ServerDefinition(
        uuid=generate_id(),
        server_group="docker:seed",
        service_name="addone-agent",
        server_name="addone-agent-1",
        server_ips=["172.18.0.4"],
        start_time=now_ms - 5000,
        ping_time=now_ms,
        handlers=["handlers.AddOne", "handlers.Multiply", "handlers.Greet"],
        handled=[HandledCount(handler="handlers.AddOne", handled=1)],
        state="running",
    )
    store.save_server(server)


def seed_published_source(store) -> None:
    """Seed a published source for the inline examples namespace."""
    from facetwork.runtime.entities import PublishedSource
    from facetwork.runtime.types import generate_id

    now_ms = int(time.time() * 1000)
    combined_source = "\n".join(INLINE_SOURCES.values())
    source = PublishedSource(
        uuid=generate_id(),
        namespace_name="handlers",
        source_text=combined_source,
        namespaces_defined=["handlers", "chain", "parallel"],
        version="latest",
        published_at=now_ms,
        origin=SEED_PATH,
    )
    store.save_published_source(source)


def clean_seeds(store) -> tuple[int, int]:
    """Remove all previously seeded data."""
    db = store._db
    flow_docs = list(db.flows.find({"name.path": SEED_PATH}, {"uuid": 1}))
    flow_ids = [doc["uuid"] for doc in flow_docs]

    workflows_deleted = 0
    seed_workflow_ids = []
    if flow_ids:
        wf_docs = list(db.workflows.find({"flow_id": {"$in": flow_ids}}, {"uuid": 1}))
        seed_workflow_ids = [doc["uuid"] for doc in wf_docs]

        result = db.workflows.delete_many({"flow_id": {"$in": flow_ids}})
        workflows_deleted = result.deleted_count

    # Clean runners and their dependent data
    seed_runner_ids = []
    if seed_workflow_ids:
        runner_docs = list(
            db.runners.find({"workflow_id": {"$in": seed_workflow_ids}}, {"uuid": 1})
        )
        seed_runner_ids = [doc["uuid"] for doc in runner_docs]

        db.steps.delete_many({"workflow_id": {"$in": seed_workflow_ids}})
        db.events.delete_many({"workflow_id": {"$in": seed_workflow_ids}})

    if seed_runner_ids:
        db.runners.delete_many({"uuid": {"$in": seed_runner_ids}})
        db.tasks.delete_many({"runner_id": {"$in": seed_runner_ids}})
        db.logs.delete_many({"runner_id": {"$in": seed_runner_ids}})

    result = db.flows.delete_many({"name.path": SEED_PATH})
    flows_deleted = result.deleted_count

    # Also clean up legacy seed documents (from old seed.py format)
    legacy = db.flows.delete_many({"name": {"$regex": "^seed-"}})
    flows_deleted += legacy.deleted_count

    # Clean handler registrations
    db.handler_registrations.delete_many({"metadata.seeded_by": SEED_PATH})

    # Clean servers
    db.servers.delete_many({"server_group": "docker:seed"})

    # Clean published sources
    db.afl_sources.delete_many({"origin": SEED_PATH})

    return flows_deleted, workflows_deleted


def seed_database():
    """Seed the database with example workflows."""
    from facetwork.runtime.mongo_store import MongoStore

    mongodb_url = os.environ.get("AFL_MONGODB_URL", "mongodb://localhost:27017")
    database = os.environ.get("AFL_MONGODB_DATABASE", "facetwork")

    logger.info("Connecting to %s/%s", mongodb_url, database)
    store = MongoStore(connection_string=mongodb_url, database_name=database)

    # Clean existing seed data first
    flows_del, wfs_del = clean_seeds(store)
    if flows_del > 0 or wfs_del > 0:
        logger.info("Cleaned %d flow(s) and %d workflow(s)", flows_del, wfs_del)

    total_flows = 0
    total_workflows = 0
    inline_flow_id = ""

    # 1. Seed inline examples
    logger.info("Seeding inline examples...")
    # chain-example and parallel-example depend on addone-example's namespace,
    # so combine all inline sources into a single compilation unit
    combined_source = "\n".join(INLINE_SOURCES.values())
    try:
        inline_flow_id, wf_count = seed_inline_source(
            "inline-examples",
            combined_source,
            store,
        )
        total_flows += 1
        total_workflows += wf_count
        logger.info("  inline-examples: %d workflows", wf_count)
    except Exception as e:
        logger.error("  inline-examples: ERROR: %s", e)

    # 2. Seed examples/ directories
    examples_dir = "/app/examples"
    if os.path.isdir(examples_dir):
        logger.info("Seeding example directories...")
        for entry in sorted(os.listdir(examples_dir)):
            afl_dir = os.path.join(examples_dir, entry, "ffl")
            if not os.path.isdir(afl_dir):
                continue

            afl_files = sorted(glob.glob(os.path.join(afl_dir, "*.ffl")))
            if not afl_files:
                continue

            try:
                wf_count = seed_example_directory(entry, afl_files, store)
                if wf_count > 0:
                    total_flows += 1
                    total_workflows += wf_count
                    logger.info(
                        "  %-20s %2d files  %3d workflows   OK", entry, len(afl_files), wf_count
                    )
                else:
                    logger.info("  %-20s %2d files    0 workflows   SKIP", entry, len(afl_files))
            except Exception as e:
                logger.warning("  %-20s ERROR: %s", entry, e)

    # 3. Seed handler registrations
    logger.info("Seeding handler registrations...")
    try:
        handler_count = seed_handler_registrations(store)
        logger.info("  %d handler registrations", handler_count)
    except Exception as e:
        logger.error("  Handler registrations ERROR: %s", e)

    # 4. Seed sample runner execution trace
    runner_id = ""
    if inline_flow_id:
        logger.info("Seeding sample runner...")
        try:
            runner_id, _wf_ids = seed_sample_runner(inline_flow_id, store)
            if runner_id:
                logger.info("  1 runner, 2 steps, 1 event, 1 task, 3 logs")
            else:
                logger.warning("  Skipped (workflow not found)")
        except Exception as e:
            logger.error("  Sample runner ERROR: %s", e)

    # 5. Seed server registration
    logger.info("Seeding server registration...")
    try:
        seed_server(store)
        logger.info("  1 server (addone-agent)")
    except Exception as e:
        logger.error("  Server registration ERROR: %s", e)

    # 6. Seed published source
    logger.info("Seeding published source...")
    try:
        seed_published_source(store)
        logger.info("  1 published source (handlers)")
    except Exception as e:
        logger.error("  Published source ERROR: %s", e)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Seed Complete!")
    logger.info("=" * 60)
    logger.info("Flows:     %d", total_flows)
    logger.info("Workflows: %d", total_workflows)
    logger.info("")
    logger.info("View the dashboard at: http://localhost:8080")
    logger.info("=" * 60)

    store.close()


if __name__ == "__main__":
    seed_database()
