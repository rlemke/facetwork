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

"""Tests for afl.runtime.agent_runner (make_store, AgentConfig, run_agent)."""

from unittest.mock import MagicMock, patch

import pytest

from facetwork.config import _reset_config_cache
from facetwork.runtime.agent_runner import AgentConfig, make_store, run_agent
from facetwork.runtime.memory_store import MemoryStore


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset the config singleton so monkeypatched env vars take effect."""
    _reset_config_cache()
    yield
    _reset_config_cache()


class TestMakeStore:
    """Tests for make_store()."""

    def test_make_store_memory(self, monkeypatch):
        """No AFL_MONGODB_URL → returns MemoryStore."""
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        store = make_store()
        assert isinstance(store, MemoryStore)

    def test_make_store_memory_with_database(self, monkeypatch):
        """Database arg is ignored when no MongoDB URL is set."""
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        store = make_store(database="mydb")
        assert isinstance(store, MemoryStore)

    def test_make_store_mongodb_url(self, monkeypatch):
        """With AFL_MONGODB_URL set, creates MongoStore with correct args."""
        monkeypatch.setenv("AFL_MONGODB_URL", "mongodb://testhost:27017")
        monkeypatch.delenv("AFL_MONGODB_DATABASE", raising=False)

        with patch("facetwork.runtime.mongo_store.MongoStore") as MockMongoStore:
            MockMongoStore.return_value = MagicMock()
            _store = make_store()
            MockMongoStore.assert_called_once_with(
                connection_string="mongodb://testhost:27017",
                database_name="facetwork",
            )

    def test_make_store_mongodb_custom_database(self, monkeypatch):
        """Database arg takes precedence over AFL_MONGODB_DATABASE env."""
        monkeypatch.setenv("AFL_MONGODB_URL", "mongodb://localhost:27017")
        monkeypatch.setenv("AFL_MONGODB_DATABASE", "env_db")

        with patch("facetwork.runtime.mongo_store.MongoStore") as MockMongoStore:
            MockMongoStore.return_value = MagicMock()
            make_store(database="arg_db")
            MockMongoStore.assert_called_once_with(
                connection_string="mongodb://localhost:27017",
                database_name="arg_db",
            )

    def test_make_store_mongodb_env_database(self, monkeypatch):
        """AFL_MONGODB_DATABASE env is used when database arg is empty."""
        monkeypatch.setenv("AFL_MONGODB_URL", "mongodb://localhost:27017")
        monkeypatch.setenv("AFL_MONGODB_DATABASE", "env_db")

        with patch("facetwork.runtime.mongo_store.MongoStore") as MockMongoStore:
            MockMongoStore.return_value = MagicMock()
            make_store()
            MockMongoStore.assert_called_once_with(
                connection_string="mongodb://localhost:27017",
                database_name="env_db",
            )


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_defaults(self):
        """Verify default values."""
        cfg = AgentConfig(service_name="test-agent", server_group="test")
        assert cfg.service_name == "test-agent"
        assert cfg.server_group == "test"
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2
        assert cfg.mongodb_database == ""

    def test_custom_values(self):
        """Verify custom overrides."""
        cfg = AgentConfig(
            service_name="my-agent",
            server_group="grp",
            poll_interval_ms=5000,
            max_concurrent=10,
            mongodb_database="custom_db",
        )
        assert cfg.poll_interval_ms == 5000
        assert cfg.max_concurrent == 10
        assert cfg.mongodb_database == "custom_db"


class TestRunAgent:
    """Tests for run_agent()."""

    def test_run_agent_registry_calls_register(self, monkeypatch):
        """In registry mode, register(runner=...) is called."""
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        monkeypatch.setenv("AFL_USE_REGISTRY", "1")
        monkeypatch.delenv("AFL_RUNNER_TOPICS", raising=False)

        config = AgentConfig(service_name="test-agent", server_group="test")
        register_mock = MagicMock()

        with patch("facetwork.runtime.registry_runner.RegistryRunner") as MockRunner:
            runner_instance = MockRunner.return_value
            runner_instance.start = MagicMock()
            runner_instance.stop = MagicMock()

            run_agent(config, register_mock)

            register_mock.assert_called_once()
            call_kwargs = register_mock.call_args[1]
            assert "runner" in call_kwargs
            assert call_kwargs["runner"] is runner_instance
            runner_instance.start.assert_called_once()

    def test_run_agent_poller_calls_register(self, monkeypatch):
        """In poller mode, register(poller=...) is called."""
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        monkeypatch.delenv("AFL_USE_REGISTRY", raising=False)
        monkeypatch.delenv("AFL_RUNNER_TOPICS", raising=False)

        config = AgentConfig(service_name="test-agent", server_group="test")
        register_mock = MagicMock()

        with patch("facetwork.runtime.agent_poller.AgentPoller") as MockPoller:
            poller_instance = MockPoller.return_value
            poller_instance.start = MagicMock()
            poller_instance.stop = MagicMock()

            run_agent(config, register_mock)

            register_mock.assert_called_once()
            call_kwargs = register_mock.call_args[1]
            assert "poller" in call_kwargs
            assert call_kwargs["poller"] is poller_instance
            poller_instance.start.assert_called_once()

    def test_run_agent_passes_topics_in_registry_mode(self, monkeypatch):
        """AFL_RUNNER_TOPICS env is parsed and passed to RegistryRunnerConfig."""
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        monkeypatch.setenv("AFL_USE_REGISTRY", "1")
        monkeypatch.setenv("AFL_RUNNER_TOPICS", "topic.a, topic.b")

        config = AgentConfig(service_name="test-agent", server_group="test")

        with (
            patch("facetwork.runtime.registry_runner.RegistryRunner") as MockRunner,
            patch("facetwork.runtime.registry_runner.RegistryRunnerConfig") as MockConfig,
        ):
            runner_instance = MockRunner.return_value
            runner_instance.start = MagicMock()

            run_agent(config, MagicMock())

            call_kwargs = MockConfig.call_args[1]
            assert call_kwargs["topics"] == ["topic.a", "topic.b"]

    def test_run_agent_passes_config_fields(self, monkeypatch):
        """AgentConfig fields are forwarded to RegistryRunnerConfig."""
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        monkeypatch.setenv("AFL_USE_REGISTRY", "1")
        monkeypatch.delenv("AFL_RUNNER_TOPICS", raising=False)

        config = AgentConfig(
            service_name="my-svc",
            server_group="my-grp",
            poll_interval_ms=3000,
            max_concurrent=8,
        )

        with (
            patch("facetwork.runtime.registry_runner.RegistryRunner") as MockRunner,
            patch("facetwork.runtime.registry_runner.RegistryRunnerConfig") as MockConfig,
        ):
            runner_instance = MockRunner.return_value
            runner_instance.start = MagicMock()

            run_agent(config, MagicMock())

            call_kwargs = MockConfig.call_args[1]
            assert call_kwargs["service_name"] == "my-svc"
            assert call_kwargs["server_group"] == "my-grp"
            assert call_kwargs["poll_interval_ms"] == 3000
            assert call_kwargs["max_concurrent"] == 8
