#!/usr/bin/env python3
"""Genomics Cohort Analysis Agent — handles bioinformatics processing events.

This agent polls for event tasks across all genomics namespaces:
- genomics.Facets: core pipeline steps (IngestReference, QcReads, etc.)
- genomics.cache.*: reference, annotation, and SRA cache handlers
- genomics.cache.index.*: aligner index builders (bwa, star, bowtie2)
- genomics.cache.Resolve: name-based resource resolution
- genomics.cache.Operations: low-level cache operations

Usage:
    PYTHONPATH=. python examples/genomics/agent.py

For Docker/MongoDB mode, set environment variables:
    AFL_MONGODB_URL=mongodb://localhost:27017
    AFL_MONGODB_DATABASE=afl

For RegistryRunner mode:
    AFL_USE_REGISTRY=1
"""

from facetwork.runtime.agent_runner import AgentConfig, run_agent

config = AgentConfig(service_name="genomics-agent", server_group="genomics")


def register(poller=None, runner=None):
    """Register genomics handlers with the active poller or runner."""
    from handlers import register_all_handlers, register_all_registry_handlers

    if poller:
        register_all_handlers(poller)
    if runner:
        register_all_registry_handlers(runner)


if __name__ == "__main__":
    run_agent(config, register)
