#!/usr/bin/env python3
"""AWS Lambda + Step Functions Agent — handles AWS pipeline event tasks.

This agent polls for event tasks across AWS namespaces:
- aws.lambda: Lambda operations (CreateFunction, InvokeFunction, etc.)
- aws.stepfunctions: Step Functions operations (CreateStateMachine, StartExecution, etc.)

Usage:
    PYTHONPATH=. python examples/aws-lambda/agent.py

For Docker/MongoDB mode, set environment variables:
    AFL_MONGODB_URL=mongodb://localhost:27017
    AFL_MONGODB_DATABASE=facetwork

For RegistryRunner mode:
    AFL_USE_REGISTRY=1

LocalStack endpoint (default: http://localhost:4566):
    LOCALSTACK_URL=http://localhost:4566
"""

from facetwork.runtime.agent_runner import AgentConfig, run_agent

config = AgentConfig(service_name="aws-lambda-agent", server_group="aws-lambda")


def register(poller=None, runner=None):
    """Register AWS Lambda handlers with the active poller or runner."""
    from handlers import register_all_handlers, register_all_registry_handlers

    if poller:
        register_all_handlers(poller)
    if runner:
        register_all_registry_handlers(runner)


if __name__ == "__main__":
    run_agent(config, register)
