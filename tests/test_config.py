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

"""Tests for FFL configuration."""

import json

from facetwork.config import (
    FFLConfig,
    MongoDBConfig,
    RunnerConfig,
    StorageConfig,
    _deep_merge,
    _reset_config_cache,
    get_config,
    load_config,
)


class TestMongoDBConfig:
    """Tests for MongoDBConfig."""

    def test_defaults(self):
        cfg = MongoDBConfig()
        assert cfg.url == "mongodb://localhost:27017"
        assert cfg.username == ""
        assert cfg.password == ""
        assert cfg.auth_source == "admin"
        assert cfg.database == "afl"

    def test_connection_string(self):
        cfg = MongoDBConfig()
        assert cfg.connection_string() == cfg.url

    def test_to_dict(self):
        cfg = MongoDBConfig()
        d = cfg.to_dict()
        assert d == {
            "url": "mongodb://localhost:27017",
            "username": "",
            "password": "",
            "auth_source": "admin",
            "database": "afl",
        }

    def test_from_dict(self):
        data = {
            "url": "mongodb://localhost:27017",
            "username": "user1",
            "password": "pass1",
            "auth_source": "mydb",
            "database": "custom_db",
        }
        cfg = MongoDBConfig.from_dict(data)
        assert cfg.url == "mongodb://localhost:27017"
        assert cfg.username == "user1"
        assert cfg.password == "pass1"
        assert cfg.auth_source == "mydb"
        assert cfg.database == "custom_db"

    def test_from_dict_camel_case_auth_source(self):
        data = {"authSource": "other_db"}
        cfg = MongoDBConfig.from_dict(data)
        assert cfg.auth_source == "other_db"

    def test_from_dict_partial(self):
        cfg = MongoDBConfig.from_dict({"username": "custom"})
        assert cfg.username == "custom"
        assert cfg.url == MongoDBConfig.url  # default

    def test_from_dict_database_defaults(self):
        cfg = MongoDBConfig.from_dict({"username": "x"})
        assert cfg.database == "afl"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("AFL_MONGODB_URL", "mongodb://envhost:9999")
        monkeypatch.setenv("AFL_MONGODB_USERNAME", "envuser")
        monkeypatch.setenv("AFL_MONGODB_PASSWORD", "envpass")
        monkeypatch.setenv("AFL_MONGODB_AUTH_SOURCE", "envdb")
        monkeypatch.setenv("AFL_MONGODB_DATABASE", "envdbname")
        cfg = MongoDBConfig.from_env()
        assert cfg.url == "mongodb://envhost:9999"
        assert cfg.username == "envuser"
        assert cfg.password == "envpass"
        assert cfg.auth_source == "envdb"
        assert cfg.database == "envdbname"

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        monkeypatch.delenv("AFL_MONGODB_USERNAME", raising=False)
        monkeypatch.delenv("AFL_MONGODB_PASSWORD", raising=False)
        monkeypatch.delenv("AFL_MONGODB_AUTH_SOURCE", raising=False)
        monkeypatch.delenv("AFL_MONGODB_DATABASE", raising=False)
        cfg = MongoDBConfig.from_env()
        assert cfg.url == MongoDBConfig.url
        assert cfg.database == "afl"


class TestFFLConfig:
    """Tests for FFLConfig."""

    def test_defaults(self):
        cfg = FFLConfig()
        assert isinstance(cfg.mongodb, MongoDBConfig)
        assert isinstance(cfg.runner, RunnerConfig)
        assert isinstance(cfg.storage, StorageConfig)

    def test_to_dict(self):
        cfg = FFLConfig()
        d = cfg.to_dict()
        assert "mongodb" in d
        assert "runner" in d
        assert "storage" in d
        assert d["mongodb"]["username"] == ""

    def test_from_dict(self):
        data = {"mongodb": {"url": "mongodb://custom:1234"}}
        cfg = FFLConfig.from_dict(data)
        assert cfg.mongodb.url == "mongodb://custom:1234"

    def test_from_dict_empty(self):
        cfg = FFLConfig.from_dict({})
        assert cfg.mongodb.url == MongoDBConfig.url

    def test_from_dict_with_runner_and_storage(self):
        data = {
            "runner": {"pollIntervalMs": 500, "maxConcurrent": 4},
            "storage": {"localOutputDir": "/data/output"},
        }
        cfg = FFLConfig.from_dict(data)
        assert cfg.runner.poll_interval_ms == 500
        assert cfg.runner.max_concurrent == 4
        assert cfg.storage.local_output_dir == "/data/output"


class TestLoadConfig:
    """Tests for load_config."""

    def test_load_from_explicit_path(self, tmp_path):
        config_file = tmp_path / "test.json"
        config_file.write_text(
            json.dumps(
                {
                    "mongodb": {
                        "url": "mongodb://filehost:5555",
                        "username": "fileuser",
                        "password": "filepass",
                        "authSource": "filedb",
                    }
                }
            )
        )
        cfg = load_config(str(config_file))
        assert cfg.mongodb.url == "mongodb://filehost:5555"
        assert cfg.mongodb.username == "fileuser"
        assert cfg.mongodb.auth_source == "filedb"

    def test_load_defaults_when_no_file(self, monkeypatch):
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        monkeypatch.delenv("AFL_MONGODB_URL", raising=False)
        cfg = load_config()
        assert cfg.mongodb.url == MongoDBConfig.url

    def test_load_from_env_variable_path(self, tmp_path, monkeypatch):
        config_file = tmp_path / "env.json"
        config_file.write_text(json.dumps({"mongodb": {"url": "mongodb://envpath:7777"}}))
        monkeypatch.setenv("AFL_CONFIG", str(config_file))
        cfg = load_config()
        assert cfg.mongodb.url == "mongodb://envpath:7777"

    def test_load_with_runner_section(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AFL_POLL_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_MAX_CONCURRENT", raising=False)
        config_file = tmp_path / "test.json"
        config_file.write_text(json.dumps({"runner": {"pollIntervalMs": 250, "maxConcurrent": 10}}))
        cfg = load_config(str(config_file))
        assert cfg.runner.poll_interval_ms == 250
        assert cfg.runner.max_concurrent == 10

    def test_load_with_storage_section(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AFL_LOCAL_OUTPUT_DIR", raising=False)
        config_file = tmp_path / "test.json"
        config_file.write_text(
            json.dumps({"storage": {"localOutputDir": "/data/output", "hdfsMaxRetries": 5}})
        )
        cfg = load_config(str(config_file))
        assert cfg.storage.local_output_dir == "/data/output"
        assert cfg.storage.hdfs_max_retries == 5

    def test_env_vars_override_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "test.json"
        config_file.write_text(json.dumps({"runner": {"pollIntervalMs": 500, "maxConcurrent": 4}}))
        monkeypatch.setenv("AFL_POLL_INTERVAL_MS", "100")
        monkeypatch.setenv("AFL_MAX_CONCURRENT", "16")
        cfg = load_config(str(config_file))
        assert cfg.runner.poll_interval_ms == 100
        assert cfg.runner.max_concurrent == 16

    def test_afl_env_overlay(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AFL_POLL_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_MAX_CONCURRENT", raising=False)
        monkeypatch.delenv("AFL_LOCAL_OUTPUT_DIR", raising=False)
        base = tmp_path / "facetwork.config.json"
        base.write_text(
            json.dumps(
                {
                    "mongodb": {"database": "afl"},
                    "runner": {"pollIntervalMs": 1000, "maxConcurrent": 2},
                }
            )
        )
        overlay = tmp_path / "facetwork.config.staging.json"
        overlay.write_text(
            json.dumps(
                {
                    "mongodb": {"database": "afl_staging"},
                    "runner": {"maxConcurrent": 8},
                }
            )
        )
        monkeypatch.setenv("AFL_ENV", "staging")
        cfg = load_config(str(base))
        assert cfg.mongodb.database == "afl_staging"
        assert cfg.runner.max_concurrent == 8
        # Base value preserved for non-overridden field
        assert cfg.runner.poll_interval_ms == 1000

    def test_afl_env_missing_overlay(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AFL_POLL_INTERVAL_MS", raising=False)
        base = tmp_path / "facetwork.config.json"
        base.write_text(json.dumps({"runner": {"pollIntervalMs": 1000}}))
        monkeypatch.setenv("AFL_ENV", "nonexistent")
        cfg = load_config(str(base))
        # Falls back to base config
        assert cfg.runner.poll_interval_ms == 1000

    def test_env_overrides_overlay(self, tmp_path, monkeypatch):
        base = tmp_path / "facetwork.config.json"
        base.write_text(json.dumps({"runner": {"maxConcurrent": 2}}))
        overlay = tmp_path / "facetwork.config.prod.json"
        overlay.write_text(json.dumps({"runner": {"maxConcurrent": 8}}))
        monkeypatch.setenv("AFL_ENV", "prod")
        monkeypatch.setenv("AFL_MAX_CONCURRENT", "32")
        cfg = load_config(str(base))
        # Env var wins over overlay
        assert cfg.runner.max_concurrent == 32


class TestRunnerConfig:
    """Tests for RunnerConfig."""

    def test_defaults(self):
        cfg = RunnerConfig()
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2
        assert cfg.heartbeat_interval_ms == 10000
        assert cfg.sweep_interval_ms == 5000
        assert cfg.use_registry is False
        assert cfg.topics == []

    def test_from_dict_camel_case(self):
        data = {
            "pollIntervalMs": 500,
            "maxConcurrent": 8,
            "heartbeatIntervalMs": 5000,
            "sweepIntervalMs": 2000,
            "useRegistry": True,
            "topics": ["ns.*"],
        }
        cfg = RunnerConfig.from_dict(data)
        assert cfg.poll_interval_ms == 500
        assert cfg.max_concurrent == 8
        assert cfg.heartbeat_interval_ms == 5000
        assert cfg.sweep_interval_ms == 2000
        assert cfg.use_registry is True
        assert cfg.topics == ["ns.*"]

    def test_from_dict_snake_case(self):
        data = {"poll_interval_ms": 300, "max_concurrent": 6}
        cfg = RunnerConfig.from_dict(data)
        assert cfg.poll_interval_ms == 300
        assert cfg.max_concurrent == 6

    def test_from_dict_empty(self):
        cfg = RunnerConfig.from_dict({})
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("AFL_POLL_INTERVAL_MS", "250")
        monkeypatch.setenv("AFL_MAX_CONCURRENT", "16")
        monkeypatch.setenv("AFL_USE_REGISTRY", "true")
        monkeypatch.setenv("AFL_RUNNER_TOPICS", "ns.Foo,ns.Bar")
        cfg = RunnerConfig.from_env()
        assert cfg.poll_interval_ms == 250
        assert cfg.max_concurrent == 16
        assert cfg.use_registry is True
        assert cfg.topics == ["ns.Foo", "ns.Bar"]

    def test_from_env_defaults(self, monkeypatch):
        for var in (
            "AFL_POLL_INTERVAL_MS",
            "AFL_MAX_CONCURRENT",
            "AFL_USE_REGISTRY",
            "AFL_RUNNER_TOPICS",
            "AFL_HEARTBEAT_INTERVAL_MS",
            "AFL_LOCK_DURATION_MS",
            "AFL_SWEEP_INTERVAL_MS",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = RunnerConfig.from_env()
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2

    def test_to_dict(self):
        cfg = RunnerConfig(poll_interval_ms=500, use_registry=True, topics=["a"])
        d = cfg.to_dict()
        assert d["pollIntervalMs"] == 500
        assert d["useRegistry"] is True
        assert d["topics"] == ["a"]

    def test_from_dict_use_registry_string(self):
        cfg = RunnerConfig.from_dict({"useRegistry": "1"})
        assert cfg.use_registry is True

    def test_from_dict_topics_string(self):
        cfg = RunnerConfig.from_dict({"topics": "ns.A, ns.B"})
        assert cfg.topics == ["ns.A", "ns.B"]


class TestStorageConfig:
    """Tests for StorageConfig."""

    def test_defaults(self):
        cfg = StorageConfig()
        assert cfg.local_output_dir == "/Volumes/afl_data/output"
        assert cfg.hdfs_webhdfs_port == 9870
        assert cfg.hdfs_max_retries == 3
        assert cfg.hdfs_retry_delay == 1.0
        assert cfg.hdfs_user == "root"

    def test_from_dict_camel_case(self):
        data = {
            "localOutputDir": "/data/output",
            "hdfsWebhdfsPort": 9871,
            "hdfsMaxRetries": 5,
            "hdfsRetryDelay": 2.5,
            "hdfsUser": "hadoop",
        }
        cfg = StorageConfig.from_dict(data)
        assert cfg.local_output_dir == "/data/output"
        assert cfg.hdfs_webhdfs_port == 9871
        assert cfg.hdfs_max_retries == 5
        assert cfg.hdfs_retry_delay == 2.5
        assert cfg.hdfs_user == "hadoop"

    def test_from_dict_snake_case(self):
        data = {"local_output_dir": "/foo", "hdfs_max_retries": 7}
        cfg = StorageConfig.from_dict(data)
        assert cfg.local_output_dir == "/foo"
        assert cfg.hdfs_max_retries == 7

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", "/env/output")
        monkeypatch.setenv("AFL_WEBHDFS_PORT", "9999")
        monkeypatch.setenv("AFL_HDFS_MAX_RETRIES", "10")
        monkeypatch.setenv("AFL_HDFS_RETRY_DELAY", "3.0")
        monkeypatch.setenv("HADOOP_USER_NAME", "testuser")
        cfg = StorageConfig.from_env()
        assert cfg.local_output_dir == "/env/output"
        assert cfg.hdfs_webhdfs_port == 9999
        assert cfg.hdfs_max_retries == 10
        assert cfg.hdfs_retry_delay == 3.0
        assert cfg.hdfs_user == "testuser"

    def test_from_env_defaults(self, monkeypatch):
        for var in (
            "AFL_LOCAL_OUTPUT_DIR",
            "AFL_WEBHDFS_PORT",
            "AFL_HDFS_MAX_RETRIES",
            "AFL_HDFS_RETRY_DELAY",
            "HADOOP_USER_NAME",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("AFL_OUTPUT_BASE", raising=False)
        cfg = StorageConfig.from_env()
        assert cfg.local_output_dir == "/Volumes/afl_data/output"
        assert cfg.hdfs_user == "root"

    def test_to_dict(self):
        cfg = StorageConfig(local_output_dir="/x")
        d = cfg.to_dict()
        assert d["localOutputDir"] == "/x"
        assert d["hdfsWebhdfsPort"] == 9870


class TestDeepMerge:
    """Tests for _deep_merge."""

    def test_section_level_merge(self):
        base = {"mongodb": {"url": "a", "database": "b"}, "runner": {"pollIntervalMs": 1000}}
        overlay = {"mongodb": {"database": "c"}, "runner": {"maxConcurrent": 8}}
        result = _deep_merge(base, overlay)
        assert result["mongodb"]["url"] == "a"
        assert result["mongodb"]["database"] == "c"
        assert result["runner"]["pollIntervalMs"] == 1000
        assert result["runner"]["maxConcurrent"] == 8

    def test_new_section_added(self):
        base = {"mongodb": {"url": "a"}}
        overlay = {"storage": {"localOutputDir": "/data"}}
        result = _deep_merge(base, overlay)
        assert result["mongodb"]["url"] == "a"
        assert result["storage"]["localOutputDir"] == "/data"

    def test_non_dict_replacement(self):
        base = {"runner": {"topics": ["a"]}}
        overlay = {"runner": {"topics": ["b", "c"]}}
        result = _deep_merge(base, overlay)
        assert result["runner"]["topics"] == ["b", "c"]

    def test_does_not_mutate_inputs(self):
        base = {"mongodb": {"url": "a"}}
        overlay = {"mongodb": {"url": "b"}}
        result = _deep_merge(base, overlay)
        assert base["mongodb"]["url"] == "a"
        assert result["mongodb"]["url"] == "b"


class TestGetConfig:
    """Tests for get_config() singleton."""

    def test_returns_aflconfig(self, monkeypatch):
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        _reset_config_cache()
        cfg = get_config()
        assert isinstance(cfg, FFLConfig)

    def test_cached(self, monkeypatch):
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        _reset_config_cache()
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_reset_clears_cache(self, monkeypatch):
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        _reset_config_cache()
        cfg1 = get_config()
        _reset_config_cache()
        cfg2 = get_config()
        assert cfg1 is not cfg2

    def test_backward_compat_old_config(self, tmp_path, monkeypatch):
        """Config file with only mongodb section still works."""
        config_file = tmp_path / "old.json"
        config_file.write_text(json.dumps({"mongodb": {"url": "mongodb://old:1234"}}))
        monkeypatch.delenv("AFL_POLL_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_MAX_CONCURRENT", raising=False)
        _reset_config_cache()
        cfg = load_config(str(config_file))
        assert cfg.mongodb.url == "mongodb://old:1234"
        assert cfg.runner.poll_interval_ms == 1000  # default


class TestSentinelPattern:
    """Tests for sentinel-based config resolution in runtime dataclasses."""

    def test_registry_runner_config_defaults(self, monkeypatch):
        """RegistryRunnerConfig resolves defaults from get_config()."""
        monkeypatch.delenv("AFL_POLL_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_MAX_CONCURRENT", raising=False)
        monkeypatch.delenv("AFL_HEARTBEAT_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        _reset_config_cache()
        from facetwork.runtime.registry_runner import RegistryRunnerConfig

        cfg = RegistryRunnerConfig()
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2
        assert cfg.heartbeat_interval_ms == 10000

    def test_registry_runner_config_explicit(self, monkeypatch):
        """Explicit values bypass sentinel resolution."""
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        _reset_config_cache()
        from facetwork.runtime.registry_runner import RegistryRunnerConfig

        cfg = RegistryRunnerConfig(poll_interval_ms=500, max_concurrent=8)
        assert cfg.poll_interval_ms == 500
        assert cfg.max_concurrent == 8

    def test_agent_poller_config_defaults(self, monkeypatch):
        monkeypatch.delenv("AFL_POLL_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_MAX_CONCURRENT", raising=False)
        monkeypatch.delenv("AFL_HEARTBEAT_INTERVAL_MS", raising=False)
        monkeypatch.delenv("AFL_CONFIG", raising=False)
        _reset_config_cache()
        from facetwork.runtime.agent_poller import AgentPollerConfig

        cfg = AgentPollerConfig()
        assert cfg.poll_interval_ms == 1000
        assert cfg.max_concurrent == 2
