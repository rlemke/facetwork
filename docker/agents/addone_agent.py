#!/usr/bin/env python3
"""
AddOne Agent - Sample agent that handles the AddOne event facet.

This agent demonstrates the AgentPoller pattern:
1. Registers handlers for event facets
2. Polls for tasks from MongoDB
3. Processes tasks and writes results back

The AddOne facet simply adds 1 to an input value.
"""

import logging
import os
import sys

# Add parent to path for afl imports
sys.path.insert(0, "/app")

from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig
from facetwork.runtime.evaluator import Evaluator
from facetwork.runtime.mongo_store import MongoStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("addone-agent")


def handle_addone(params: dict) -> dict:
    """
    Handle the AddOne event facet.

    Params:
        value: Long - the input value

    Returns:
        result: Long - the input value plus 1
    """
    value = params.get("value", 0)
    result = value + 1
    step_log = params.get("_step_log")
    if step_log:
        step_log(f"AddOne: {value} + 1 = {result}")
    logger.info(f"AddOne: {value} + 1 = {result}")
    return {"result": result}


def handle_multiply(params: dict) -> dict:
    """
    Handle the Multiply event facet.

    Params:
        a: Long - first value
        b: Long - second value

    Returns:
        result: Long - a * b
    """
    a = params.get("a", 0)
    b = params.get("b", 0)
    result = a * b
    step_log = params.get("_step_log")
    if step_log:
        step_log(f"Multiply: {a} * {b} = {result}")
    logger.info(f"Multiply: {a} * {b} = {result}")
    return {"result": result}


def handle_greet(params: dict) -> dict:
    """
    Handle the Greet event facet.

    Params:
        name: String - name to greet

    Returns:
        message: String - greeting message
    """
    name = params.get("name", "World")
    message = f"Hello, {name}!"
    step_log = params.get("_step_log")
    if step_log:
        step_log(f"Greet: {message}")
    logger.info(f"Greet: {message}")
    return {"message": message}


def main():
    # Configuration from environment
    mongodb_url = os.environ.get("AFL_MONGODB_URL", "mongodb://localhost:27017")
    database = os.environ.get("AFL_MONGODB_DATABASE", "facetwork")
    agent_name = os.environ.get("AFL_AGENT_NAME", "addone-agent")

    logger.info(f"Starting {agent_name}")
    logger.info(f"MongoDB: {mongodb_url}/{database}")

    # Create MongoDB store
    store = MongoStore(connection_string=mongodb_url, database_name=database)

    # Create evaluator (needed for workflow resumption)
    evaluator = Evaluator(store)

    # Create poller config
    config = AgentPollerConfig(
        service_name=agent_name,
        server_group="docker-agents",
        poll_interval_ms=1000,
        max_concurrent=5,
    )

    # Create poller and register handlers
    poller = AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=config,
    )

    # Register handlers for various event facets
    poller.register("handlers.AddOne", handle_addone)
    poller.register("handlers.Multiply", handle_multiply)
    poller.register("handlers.Greet", handle_greet)

    # Also register with short names for flexibility
    poller.register("AddOne", handle_addone)
    poller.register("Multiply", handle_multiply)
    poller.register("Greet", handle_greet)

    logger.info("Registered handlers: AddOne, Multiply, Greet")
    logger.info("Starting poll loop...")

    try:
        poller.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        poller.stop()


if __name__ == "__main__":
    main()
