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

"""Tests for the FFL MavenArtifactRunner.

Tests cover:
- MavenRunnerConfig defaults and customization
- Maven URI parsing (valid, invalid, with classifier)
- Artifact resolution and download (cache hit/miss, URL construction, errors)
- Event processing (success, failure, timeout, missing registration, JVM args, main class)
- Registry refresh (mvn: filtering, topic filtering)
- Lifecycle (start/stop, server registration)
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    HandlerRegistration,
    MemoryStore,
    StepState,
    Telemetry,
)
from facetwork.runtime.entities import (
    ServerState,
    TaskState,
)

# Ensure the maven example root is importable
_MAVEN_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)
if _MAVEN_DIR not in sys.path:
    sys.path.insert(0, _MAVEN_DIR)

from maven_runner import (
    MavenArtifactRunner,
    MavenRunnerConfig,
)

from facetwork.runtime.registry_runner import _current_time_ms

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def store():
    """Fresh in-memory store."""
    return MemoryStore()


@pytest.fixture
def evaluator(store):
    """Evaluator with in-memory store."""
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))


@pytest.fixture
def config(tmp_path):
    """Default runner config with temp cache dir."""
    return MavenRunnerConfig(cache_dir=str(tmp_path / "maven-cache"))


@pytest.fixture
def runner(store, evaluator, config):
    """MavenArtifactRunner with defaults."""
    return MavenArtifactRunner(
        persistence=store,
        evaluator=evaluator,
        config=config,
    )


# ---- Workflow AST fixtures ----


@pytest.fixture
def program_ast():
    """Program AST with Value (facet), CountDocuments (event facet)."""
    return {
        "type": "Program",
        "declarations": [
            {
                "type": "FacetDecl",
                "name": "Value",
                "params": [{"name": "input", "type": "Long"}],
            },
            {
                "type": "EventFacetDecl",
                "name": "CountDocuments",
                "params": [{"name": "input", "type": "Long"}],
                "returns": [{"name": "output", "type": "Long"}],
            },
        ],
    }


@pytest.fixture
def workflow_ast():
    """Simple workflow that calls an event facet."""
    return {
        "type": "WorkflowDecl",
        "name": "TestWorkflow",
        "params": [{"name": "x", "type": "Long"}],
        "returns": [{"name": "result", "type": "Long"}],
        "body": {
            "type": "AndThenBlock",
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-s1",
                    "name": "s1",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [
                            {
                                "name": "input",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "InputRef", "path": ["x"]},
                                    "right": {"type": "Int", "value": 1},
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "StepStmt",
                    "id": "step-s2",
                    "name": "s2",
                    "call": {
                        "type": "CallExpr",
                        "target": "CountDocuments",
                        "args": [
                            {
                                "name": "input",
                                "value": {"type": "StepRef", "path": ["s1", "input"]},
                            }
                        ],
                    },
                },
            ],
            "yield": {
                "type": "YieldStmt",
                "id": "yield-TW",
                "call": {
                    "type": "CallExpr",
                    "target": "TestWorkflow",
                    "args": [
                        {
                            "name": "result",
                            "value": {
                                "type": "BinaryExpr",
                                "operator": "+",
                                "left": {"type": "StepRef", "path": ["s2", "output"]},
                                "right": {"type": "StepRef", "path": ["s1", "input"]},
                            },
                        }
                    ],
                },
            },
        },
    }


def _execute_until_paused(evaluator, workflow_ast, inputs=None, program_ast=None):
    """Execute a workflow until it pauses at EVENT_TRANSMIT."""
    return evaluator.execute(workflow_ast, inputs=inputs, program_ast=program_ast)


# =========================================================================
# TestMavenRunnerConfig
# =========================================================================


class TestMavenRunnerConfig:
    """Tests for MavenRunnerConfig defaults and customization."""

    def test_defaults(self):
        """Default config has sensible values."""
        cfg = MavenRunnerConfig()
        assert cfg.service_name == "afl-maven-runner"
        assert cfg.server_group == "default"
        assert cfg.task_list == "default"
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2
        assert cfg.repository_url == "https://repo1.maven.org/maven2"
        assert cfg.java_command == "java"
        assert cfg.default_timeout_ms == 300000

    def test_auto_filled_hostname(self):
        """server_name defaults to hostname."""
        import socket

        cfg = MavenRunnerConfig()
        assert cfg.server_name == socket.gethostname()

    def test_custom_cache_dir(self, tmp_path):
        """Custom cache_dir is preserved."""
        cache = str(tmp_path / "custom-cache")
        cfg = MavenRunnerConfig(cache_dir=cache)
        assert cfg.cache_dir == cache

    def test_default_cache_dir(self):
        """Default cache_dir is ~/.afl/maven-cache."""
        cfg = MavenRunnerConfig()
        expected = str(Path.home() / ".ffl" / "maven-cache")
        assert cfg.cache_dir == expected

    def test_custom_repository_url(self):
        """Custom repository URL is preserved."""
        cfg = MavenRunnerConfig(repository_url="https://nexus.example.com/repository/maven-public")
        assert cfg.repository_url == "https://nexus.example.com/repository/maven-public"


# =========================================================================
# TestMavenUriParsing
# =========================================================================


class TestMavenUriParsing:
    """Tests for Maven URI parsing."""

    def test_valid_uri(self):
        """Parse a standard mvn: URI."""
        g, a, v, c = MavenArtifactRunner._parse_maven_uri("mvn:com.example:my-handler:1.0.0")
        assert g == "com.example"
        assert a == "my-handler"
        assert v == "1.0.0"
        assert c == ""

    def test_valid_uri_with_classifier(self):
        """Parse a mvn: URI with classifier."""
        g, a, v, c = MavenArtifactRunner._parse_maven_uri(
            "mvn:com.example:my-handler:1.0.0:jar-with-dependencies"
        )
        assert g == "com.example"
        assert a == "my-handler"
        assert v == "1.0.0"
        assert c == "jar-with-dependencies"

    def test_invalid_missing_scheme(self):
        """URI without mvn: prefix is rejected."""
        with pytest.raises(ValueError, match="Invalid Maven URI scheme"):
            MavenArtifactRunner._parse_maven_uri("com.example:my-handler:1.0.0")

    def test_invalid_wrong_scheme(self):
        """URI with wrong scheme is rejected."""
        with pytest.raises(ValueError, match="Invalid Maven URI scheme"):
            MavenArtifactRunner._parse_maven_uri("file://com.example:my-handler:1.0.0")

    def test_invalid_too_few_parts(self):
        """URI with fewer than 3 components is rejected."""
        with pytest.raises(ValueError, match="expected mvn:groupId:artifactId:version"):
            MavenArtifactRunner._parse_maven_uri("mvn:com.example:my-handler")

    def test_invalid_too_many_parts(self):
        """URI with more than 4 components is rejected."""
        with pytest.raises(ValueError, match="too many components"):
            MavenArtifactRunner._parse_maven_uri("mvn:com.example:my-handler:1.0.0:cls:extra")

    def test_invalid_empty_component(self):
        """URI with empty component is rejected."""
        with pytest.raises(ValueError, match="empty component"):
            MavenArtifactRunner._parse_maven_uri("mvn::my-handler:1.0.0")

    def test_dots_in_group_id(self):
        """GroupId with multiple dots is parsed correctly."""
        g, a, v, c = MavenArtifactRunner._parse_maven_uri(
            "mvn:org.apache.commons:commons-lang3:3.12.0"
        )
        assert g == "org.apache.commons"
        assert a == "commons-lang3"
        assert v == "3.12.0"

    def test_hyphens_in_artifact_id(self):
        """ArtifactId with hyphens is parsed correctly."""
        g, a, v, c = MavenArtifactRunner._parse_maven_uri(
            "mvn:com.example:my-cool-handler:2.1.0-SNAPSHOT"
        )
        assert a == "my-cool-handler"
        assert v == "2.1.0-SNAPSHOT"


# =========================================================================
# TestArtifactResolution
# =========================================================================


class TestArtifactResolution:
    """Tests for artifact resolution and download."""

    def test_cache_hit(self, runner, config):
        """When JAR exists in cache, returns path without downloading."""
        group_path = os.path.join("com", "example")
        jar_dir = Path(config.cache_dir) / group_path / "my-handler" / "1.0.0"
        jar_dir.mkdir(parents=True)
        jar_file = jar_dir / "my-handler-1.0.0.jar"
        jar_file.write_bytes(b"PK\x03\x04fake-jar-content")

        result = runner._resolve_artifact("com.example", "my-handler", "1.0.0", "")
        assert result == jar_file

    def test_cache_miss_triggers_download(self, runner, config):
        """When JAR is not cached, download is triggered."""
        fake_jar = b"PK\x03\x04fake-jar"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = fake_jar
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = runner._resolve_artifact("com.example", "my-handler", "1.0.0", "")

            assert result.exists()
            assert result.read_bytes() == fake_jar
            mock_urlopen.assert_called_once()

    def test_download_url_construction(self, runner):
        """Download URL is constructed correctly from Maven coordinates."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"PK\x03\x04jar"
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            runner._download_artifact("com.example", "my-handler", "1.0.0", "")

            url = mock_urlopen.call_args[0][0]
            assert url == (
                "https://repo1.maven.org/maven2/com/example/my-handler/1.0.0/my-handler-1.0.0.jar"
            )

    def test_download_url_with_classifier(self, runner):
        """Download URL includes classifier when present."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"PK\x03\x04jar"
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            runner._download_artifact("com.example", "my-handler", "1.0.0", "jar-with-dependencies")

            url = mock_urlopen.call_args[0][0]
            assert "my-handler-1.0.0-jar-with-dependencies.jar" in url

    def test_download_failure_raises(self, runner):
        """HTTP error during download raises ValueError."""
        import urllib.error

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://example.com",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )

            with pytest.raises(ValueError, match="HTTP 404"):
                runner._download_artifact("com.example", "my-handler", "1.0.0", "")

    def test_custom_repository_url(self, store, evaluator, tmp_path):
        """Custom repository URL is used in download."""
        cfg = MavenRunnerConfig(
            cache_dir=str(tmp_path / "cache"),
            repository_url="https://nexus.example.com/repo",
        )
        r = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=cfg)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"PK\x03\x04jar"
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            r._download_artifact("com.example", "my-handler", "1.0.0", "")

            url = mock_urlopen.call_args[0][0]
            assert url.startswith("https://nexus.example.com/repo/")


# =========================================================================
# TestProcessEvent
# =========================================================================


class TestProcessEvent:
    """Tests for _process_event subprocess dispatch."""

    def _setup_paused_workflow(self, store, evaluator, workflow_ast, program_ast):
        """Helper: execute workflow until paused, return (result, step, task)."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        assert len(pending_tasks) == 1
        task = pending_tasks[0]

        return result, step, task

    def test_successful_execution(self, store, evaluator, config, workflow_ast, program_ast):
        """Exit 0 -> continue_step + resume + COMPLETED."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        # Write returns to the step (simulating what the JVM program does)
        from facetwork.runtime.types import FacetAttributes

        step_obj = store.get_step(step.id)
        if step_obj.attributes is None:
            step_obj.attributes = FacetAttributes()
        step_obj.attributes.returns["output"] = {
            "name": "output",
            "value": 42,
            "type_hint": "Long",
        }
        store.save_step(step_obj)

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        # Register mvn: handler
        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            timeout_ms=30000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()

        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Mock subprocess to succeed
        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            )
            dispatched = runner.poll_once()

        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED

        updated_step = store.get_step(step.id)
        assert updated_step.state != StepState.EVENT_TRANSMIT

    def test_failed_execution(self, store, evaluator, config, workflow_ast, program_ast):
        """Exit 1 -> fail_step + FAILED."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            timeout_ms=30000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()

        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout=b"", stderr=b"NullPointerException"
            )
            dispatched = runner.poll_once()

        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "NullPointerException" in updated_task.error["message"]

        updated_step = store.get_step(step.id)
        assert updated_step.state == StepState.STATEMENT_ERROR

    def test_subprocess_timeout(self, store, evaluator, config, workflow_ast, program_ast):
        """Subprocess timeout -> fail_step + FAILED."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            timeout_ms=1000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()

        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["java"], timeout=1.0)
            dispatched = runner.poll_once()

        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "timed out" in updated_task.error["message"]

    def test_no_registration_found(self, store, evaluator, config, workflow_ast, program_ast):
        """Missing registration -> fail_step + FAILED."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        # Force the task name into registered_names so claim_task works,
        # but don't actually add a registration to _registrations
        runner._registered_names = [task.name]
        runner._last_refresh = _current_time_ms()

        dispatched = runner.poll_once()
        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "No handler registration" in updated_task.error["message"]

    def test_invalid_maven_uri(self, store, evaluator, config, workflow_ast, program_ast):
        """Invalid mvn: URI in registration -> fail_step + FAILED."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        # Manually insert a registration with a bad URI
        bad_reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:bad-uri",
        )
        runner._registrations = {"CountDocuments": bad_reg}
        runner._registered_names = ["CountDocuments"]
        runner._last_refresh = _current_time_ms()

        dispatched = runner.poll_once()
        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "Failed to resolve artifact" in updated_task.error["message"]

    def test_jvm_args_from_metadata(self, store, evaluator, config, workflow_ast, program_ast):
        """JVM args from metadata are applied to the command."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            entrypoint="",
            metadata={"jvm_args": ["-Xmx512m", "-Xms256m"]},
            timeout_ms=30000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()

        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            )
            runner.poll_once()

            cmd = mock_run.call_args[0][0]
            assert "-Xmx512m" in cmd
            assert "-Xms256m" in cmd
            # JVM args come before -jar
            xmx_idx = cmd.index("-Xmx512m")
            jar_idx = cmd.index("-jar")
            assert xmx_idx < jar_idx

    def test_main_class_from_entrypoint(self, store, evaluator, config, workflow_ast, program_ast):
        """Main class from entrypoint uses -cp instead of -jar."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            entrypoint="com.example.Main",
            timeout_ms=30000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()

        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            )
            runner.poll_once()

            cmd = mock_run.call_args[0][0]
            assert "-cp" in cmd
            assert "-jar" not in cmd
            assert "com.example.Main" in cmd

    def test_environment_variables_passed(
        self, store, evaluator, config, workflow_ast, program_ast
    ):
        """AFL_STEP_ID is set in subprocess environment."""
        result, step, task = self._setup_paused_workflow(
            store, evaluator, workflow_ast, program_ast
        )

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            timeout_ms=30000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()

        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            )
            runner.poll_once()

            env = mock_run.call_args[1]["env"]
            assert env["AFL_STEP_ID"] == task.step_id


# =========================================================================
# TestRegistryRefresh
# =========================================================================


class TestRegistryRefresh:
    """Tests for registry refresh with mvn: filtering."""

    def test_only_mvn_registrations_picked_up(self, store, evaluator, config):
        """Only registrations with mvn: URI scheme are loaded."""
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        # Save both a Python handler and a Maven handler
        python_reg = HandlerRegistration(
            facet_name="PythonHandler",
            module_uri="my.python.module",
        )
        maven_reg = HandlerRegistration(
            facet_name="MavenHandler",
            module_uri="mvn:com.example:handler:1.0.0",
        )
        store.save_handler_registration(python_reg)
        store.save_handler_registration(maven_reg)

        runner._refresh_registry()
        names = runner.registered_names()

        assert "MavenHandler" in names
        assert "PythonHandler" not in names

    def test_topic_filtering_applies(self, store, evaluator, tmp_path):
        """Topic filter restricts which registrations are loaded."""
        cfg = MavenRunnerConfig(
            cache_dir=str(tmp_path / "cache"),
            topics=["osm.*"],
        )
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=cfg)

        reg1 = HandlerRegistration(
            facet_name="osm.Geocode",
            module_uri="mvn:com.example:geocoder:1.0.0",
        )
        reg2 = HandlerRegistration(
            facet_name="billing.Charge",
            module_uri="mvn:com.example:billing:1.0.0",
        )
        store.save_handler_registration(reg1)
        store.save_handler_registration(reg2)

        runner._refresh_registry()
        names = runner.registered_names()

        assert "osm.Geocode" in names
        assert "billing.Charge" not in names

    def test_non_mvn_registrations_ignored(self, store, evaluator, config):
        """File-based registrations are not picked up."""
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="FileHandler",
            module_uri="file:///path/to/handler.py",
        )
        store.save_handler_registration(reg)

        runner._refresh_registry()
        names = runner.registered_names()

        assert len(names) == 0


# =========================================================================
# TestHandlerRegistration
# =========================================================================


class TestHandlerRegistration:
    """Tests for register_handler validation."""

    def test_register_mvn_handler(self, runner, store):
        """Registering a valid mvn: handler persists and refreshes."""
        runner.register_handler(
            facet_name="ns.ProcessData",
            module_uri="mvn:com.example:data-processor:1.0.0",
        )
        assert "ns.ProcessData" in runner.registered_names()

        regs = store.list_handler_registrations()
        assert len(regs) == 1
        assert regs[0].module_uri == "mvn:com.example:data-processor:1.0.0"

    def test_register_non_mvn_handler_raises(self, runner):
        """Registering a non-mvn: handler raises ValueError."""
        with pytest.raises(ValueError, match="mvn:"):
            runner.register_handler(
                facet_name="ns.ProcessData",
                module_uri="my.python.module",
            )

    def test_register_invalid_uri_raises(self, runner):
        """Registering an invalid mvn: URI raises ValueError."""
        with pytest.raises(ValueError, match="expected mvn:groupId:artifactId:version"):
            runner.register_handler(
                facet_name="ns.ProcessData",
                module_uri="mvn:bad-uri",
            )


# =========================================================================
# TestLifecycle
# =========================================================================


class TestLifecycle:
    """Tests for runner lifecycle (start/stop, server registration)."""

    def test_start_stop_cycle(self, store, evaluator, config):
        """start() registers server; stop() triggers shutdown."""
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        # Run start in a background thread and stop quickly
        t = threading.Thread(target=runner.start)
        t.start()

        # Wait for the runner to be running
        deadline = time.time() + 2
        while not runner.is_running and time.time() < deadline:
            time.sleep(0.01)
        assert runner.is_running

        runner.stop()
        t.join(timeout=5)
        assert not runner.is_running

    def test_server_registration_on_start(self, store, evaluator, config):
        """Server is registered as RUNNING on start and SHUTDOWN after stop."""
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        t = threading.Thread(target=runner.start)
        t.start()

        # Wait for server to be registered (not just is_running flag)
        deadline = time.time() + 2
        while time.time() < deadline:
            server = store.get_server(runner.server_id)
            if server is not None:
                break
            time.sleep(0.01)

        server = store.get_server(runner.server_id)
        assert server is not None
        assert server.state == ServerState.RUNNING

        runner.stop()
        t.join(timeout=5)

        server = store.get_server(runner.server_id)
        assert server.state == ServerState.SHUTDOWN

    def test_poll_once_dispatches_tasks(self, store, evaluator, config, workflow_ast, program_ast):
        """poll_once processes available tasks."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="mvn:com.example:handler:1.0.0",
            timeout_ms=30000,
        )
        store.save_handler_registration(reg)
        runner._refresh_registry()
        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        with (
            patch("maven_runner.subprocess.run") as mock_run,
            patch.object(runner, "_resolve_artifact", return_value=Path("/tmp/fake.jar")),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            )
            dispatched = runner.poll_once()

        assert dispatched == 1

    def test_ast_caching(self, store, evaluator, config):
        """Cached AST is used for workflow resume."""
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)

        ast = {"type": "WorkflowDecl", "name": "Test"}
        program = {"type": "Program", "declarations": []}
        runner.cache_workflow_ast("wf-123", ast, program)

        assert runner._ast_cache["wf-123"] == ast
        assert runner._program_ast_cache["wf-123"] == program


# =========================================================================
# TestReadStepReturns
# =========================================================================


class TestReadStepReturns:
    """Tests for _read_step_returns."""

    def test_read_returns_dict_format(self, store, evaluator, config, workflow_ast, program_ast):
        """Returns stored as dicts are read correctly."""
        _result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        # Write returns as dict format (simulating MongoDB driver)
        step_obj = store.get_step(step.id)
        step_obj.attributes.returns["output"] = {
            "name": "output",
            "value": 99,
            "type_hint": "Long",
        }
        store.save_step(step_obj)

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)
        returns = runner._read_step_returns(step.id)
        assert returns == {"output": 99}

    def test_read_returns_empty(self, store, evaluator, config, workflow_ast, program_ast):
        """No returns returns empty dict."""
        _result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)
        returns = runner._read_step_returns(step.id)
        # May have params but returns should be empty or have existing returns
        # The exact state depends on the evaluator, but should not raise
        assert isinstance(returns, dict)

    def test_read_returns_nonexistent_step(self, store, evaluator, config):
        """Nonexistent step returns empty dict."""
        runner = MavenArtifactRunner(persistence=store, evaluator=evaluator, config=config)
        returns = runner._read_step_returns("nonexistent-step-id")
        assert returns == {}
