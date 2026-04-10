#!/usr/bin/env python3
"""Jenkins CI/CD Pipeline Agent — handles Jenkins pipeline event tasks.

This agent polls for event tasks across all Jenkins namespaces:
- jenkins.scm: source control operations (GitCheckout, GitMerge)
- jenkins.build: build tools (MavenBuild, GradleBuild, NpmBuild, DockerBuild)
- jenkins.test: testing and quality (RunTests, CodeQuality, SecurityScan)
- jenkins.artifact: artifact management (ArchiveArtifacts, PublishToRegistry, DockerPush)
- jenkins.deploy: deployment (DeployToEnvironment, DeployToK8s, RollbackDeploy)
- jenkins.notify: notifications (SlackNotify, EmailNotify)

Usage:
    PYTHONPATH=. python examples/jenkins/agent.py

For Docker/MongoDB mode, set environment variables:
    AFL_MONGODB_URL=mongodb://localhost:27017
    AFL_MONGODB_DATABASE=facetwork

For RegistryRunner mode:
    AFL_USE_REGISTRY=1
"""

from facetwork.runtime.agent_runner import AgentConfig, run_agent

config = AgentConfig(service_name="jenkins-agent", server_group="jenkins")


def register(poller=None, runner=None):
    """Register Jenkins handlers with the active poller or runner."""
    from handlers import register_all_handlers, register_all_registry_handlers

    if poller:
        register_all_handlers(poller)
    if runner:
        register_all_registry_handlers(runner)


if __name__ == "__main__":
    run_agent(config, register)
