"""Maven runner event facet handlers.

Handles the RunMavenArtifact and RunMavenPlugin event facets from the
maven.runner namespace.  Simulates resolving Maven artifacts and launching
JVM subprocesses or plugin goals.
"""

import logging
import os
from typing import Any

from facetwork.config import get_output_base

log = logging.getLogger(__name__)

_LOCAL_OUTPUT = get_output_base()

NAMESPACE = "maven.runner"


def _default_phase(goal: str) -> str:
    """Map a Maven plugin goal to its default lifecycle phase."""
    mapping = {
        "compile": "compile",
        "testCompile": "test-compile",
        "test": "test",
        "check": "verify",
        "jar": "package",
        "install": "install",
        "deploy": "deploy",
        "site": "site",
    }
    return mapping.get(goal, "verify")


def _run_maven_artifact_handler(payload: dict) -> dict[str, Any]:
    """Simulate running a Maven artifact as a JVM subprocess."""
    step_log = payload.get("_step_log")
    step_id = payload.get("step_id", "unknown")
    group_id = payload.get("group_id", "com.example")
    artifact_id = payload.get("artifact_id", "app")
    if step_log:
        step_log(f"RunMavenArtifact: {group_id}:{artifact_id}")
    version = payload.get("version", "1.0.0")
    classifier = payload.get("classifier", "")
    entrypoint = payload.get("entrypoint", "")
    jvm_args = payload.get("jvm_args", "")

    group_path = group_id.replace(".", "/")
    jar_name = f"{artifact_id}-{version}"
    if classifier:
        jar_name += f"-{classifier}"
    jar_name += ".jar"
    artifact_path = os.path.join(
        _LOCAL_OUTPUT, "maven-cache", group_path, artifact_id, version, jar_name
    )

    cmd = "java"
    if jvm_args:
        cmd += f" {jvm_args}"
    if entrypoint:
        cmd += f" -cp {artifact_path} {entrypoint} {step_id}"
    else:
        cmd += f" -jar {artifact_path} {step_id}"

    return {
        "result": {
            "exit_code": 0,
            "success": True,
            "duration_ms": 1250,
            "stdout": f"[{artifact_id}] Step {step_id} completed successfully",
            "stderr": "",
            "artifact_path": artifact_path,
        },
    }


def _run_maven_plugin_handler(payload: dict) -> dict[str, Any]:
    """Simulate running a Maven plugin goal within a workspace."""
    step_log = payload.get("_step_log")
    workspace_path = payload.get("workspace_path", os.path.join(_LOCAL_OUTPUT, "workspace"))
    plugin_group_id = payload.get("plugin_group_id", "org.apache.maven.plugins")
    if step_log:
        step_log(f"RunMavenPlugin: {payload.get('goal', 'compile')}")
    plugin_artifact_id = payload.get("plugin_artifact_id", "maven-compiler-plugin")
    plugin_version = payload.get("plugin_version", "3.11.0")
    goal = payload.get("goal", "compile")
    phase = payload.get("phase", "") or _default_phase(goal)
    _properties = payload.get("properties", "")

    plugin_key = f"{plugin_group_id}:{plugin_artifact_id}:{plugin_version}"

    return {
        "result": {
            "plugin_key": plugin_key,
            "goal": goal,
            "phase": phase,
            "exit_code": 0,
            "success": True,
            "duration_ms": 850,
            "output": f"[INFO] --- {plugin_artifact_id}:{plugin_version}:{goal} ({phase}) @ workspace ---",
            "artifact_path": workspace_path,
        },
    }


# RegistryRunner dispatch adapter
_DISPATCH = {
    f"{NAMESPACE}.RunMavenArtifact": _run_maven_artifact_handler,
    f"{NAMESPACE}.RunMavenPlugin": _run_maven_plugin_handler,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_runner_handlers(poller) -> None:
    """Register all runner event facet handlers with the poller."""
    for fqn, func in _DISPATCH.items():
        poller.register(fqn, func)
        log.debug("Registered runner handler: %s", fqn)
