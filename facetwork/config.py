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

"""AFL configuration management.

Provides configuration dataclasses for external service connections
and a loader that reads from config files or environment variables.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MongoDBConfig:
    """MongoDB connection configuration.

    Attributes:
        url: MongoDB connection URL
        username: Authentication username
        password: Authentication password
        auth_source: Authentication database name
        database: Target database name (e.g. "facetwork", "facetwork_test")
    """

    url: str = "mongodb://afl-mongodb:27017"
    username: str = ""
    password: str = ""
    auth_source: str = "admin"
    database: str = "facetwork"

    def connection_string(self) -> str:
        """Build the effective connection string.

        If username/password differ from those embedded in the URL,
        the explicit username/password and auth_source are used to
        construct a new connection string.

        Returns:
            A MongoDB connection URI.
        """
        return self.url

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MongoDBConfig:
        """Create from a dictionary.

        Keys may use either snake_case (``auth_source``) or
        camelCase (``authSource``).
        """
        return cls(
            url=data.get("url", cls.url),
            username=data.get("username", cls.username),
            password=data.get("password", cls.password),
            auth_source=data.get("auth_source", data.get("authSource", cls.auth_source)),
            database=data.get("database", cls.database),
        )

    @classmethod
    def from_env(cls) -> MongoDBConfig:
        """Create from environment variables.

        Recognised variables (all optional – defaults apply for missing vars):
            AFL_MONGODB_URL
            AFL_MONGODB_USERNAME
            AFL_MONGODB_PASSWORD
            AFL_MONGODB_AUTH_SOURCE
            AFL_MONGODB_DATABASE
        """
        defaults = cls()
        return cls(
            url=os.environ.get("AFL_MONGODB_URL", defaults.url),
            username=os.environ.get("AFL_MONGODB_USERNAME", defaults.username),
            password=os.environ.get("AFL_MONGODB_PASSWORD", defaults.password),
            auth_source=os.environ.get("AFL_MONGODB_AUTH_SOURCE", defaults.auth_source),
            database=os.environ.get("AFL_MONGODB_DATABASE", defaults.database),
        )


@dataclass
class RunnerConfig:
    """Runner/poller configuration.

    Attributes:
        poll_interval_ms: Polling interval in milliseconds
        max_concurrent: Maximum concurrent work items
        heartbeat_interval_ms: Heartbeat interval in milliseconds
        sweep_interval_ms: Stuck-step sweep interval in milliseconds
        use_registry: Use RegistryRunner mode
        topics: Topic/glob filters for handler selection
    """

    poll_interval_ms: int = 1000
    max_concurrent: int = 2
    heartbeat_interval_ms: int = 10000
    sweep_interval_ms: int = 5000
    use_registry: bool = False
    topics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary (camelCase keys)."""
        return {
            "pollIntervalMs": self.poll_interval_ms,
            "maxConcurrent": self.max_concurrent,
            "heartbeatIntervalMs": self.heartbeat_interval_ms,
            "sweepIntervalMs": self.sweep_interval_ms,
            "useRegistry": self.use_registry,
            "topics": list(self.topics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunnerConfig:
        """Create from a dictionary (supports camelCase and snake_case keys)."""

        def _int(key_camel: str, key_snake: str, default: int) -> int:
            return int(data.get(key_snake, data.get(key_camel, default)))

        def _bool(key_camel: str, key_snake: str, default: bool) -> bool:
            val = data.get(key_snake, data.get(key_camel, default))
            if isinstance(val, str):
                return val.lower() in ("true", "1")
            return bool(val)

        topics_raw = data.get("topics", [])
        if isinstance(topics_raw, str):
            topics_raw = [t.strip() for t in topics_raw.split(",") if t.strip()]

        return cls(
            poll_interval_ms=_int("pollIntervalMs", "poll_interval_ms", 1000),
            max_concurrent=_int("maxConcurrent", "max_concurrent", 2),
            heartbeat_interval_ms=_int("heartbeatIntervalMs", "heartbeat_interval_ms", 10000),
            sweep_interval_ms=_int("sweepIntervalMs", "sweep_interval_ms", 5000),
            use_registry=_bool("useRegistry", "use_registry", False),
            topics=list(topics_raw),
        )

    @classmethod
    def from_env(cls) -> RunnerConfig:
        """Create from environment variables.

        Recognised variables (all optional):
            AFL_POLL_INTERVAL_MS
            AFL_MAX_CONCURRENT
            AFL_HEARTBEAT_INTERVAL_MS
            AFL_SWEEP_INTERVAL_MS
            AFL_USE_REGISTRY  ("true"/"1" to enable)
            AFL_RUNNER_TOPICS  (comma-separated)
        """
        defaults = cls()
        topics_str = os.environ.get("AFL_RUNNER_TOPICS", "")
        topics = [t.strip() for t in topics_str.split(",") if t.strip()] if topics_str else []
        return cls(
            poll_interval_ms=int(
                os.environ.get("AFL_POLL_INTERVAL_MS", str(defaults.poll_interval_ms))
            ),
            max_concurrent=int(os.environ.get("AFL_MAX_CONCURRENT", str(defaults.max_concurrent))),
            heartbeat_interval_ms=int(
                os.environ.get("AFL_HEARTBEAT_INTERVAL_MS", str(defaults.heartbeat_interval_ms))
            ),
            sweep_interval_ms=int(
                os.environ.get("AFL_SWEEP_INTERVAL_MS", str(defaults.sweep_interval_ms))
            ),
            use_registry=os.environ.get("AFL_USE_REGISTRY", "").strip().lower() in ("true", "1"),
            topics=topics or defaults.topics,
        )


_OUTPUT_BASE_DEFAULT = "/Volumes/afl_data/output"


def get_output_base() -> str:
    """Return the base output directory.

    Checks ``AFL_OUTPUT_BASE``, then ``AFL_LOCAL_OUTPUT_DIR`` for backward
    compatibility, then falls back to ``/Volumes/afl_data/output``.
    """
    return os.environ.get(
        "AFL_OUTPUT_BASE",
        os.environ.get("AFL_LOCAL_OUTPUT_DIR", _OUTPUT_BASE_DEFAULT),
    )


def get_temp_dir() -> str:
    """Return temporary directory under the output base.

    Creates the directory if it does not exist.
    """
    d = os.path.join(get_output_base(), "tmp")
    Path(d).mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class StorageConfig:
    """Storage configuration.

    Attributes:
        local_output_dir: Base directory for local handler output files
        hdfs_webhdfs_port: WebHDFS port (default 9870)
        hdfs_max_retries: Maximum retries for transient HDFS errors
        hdfs_retry_delay: Base delay in seconds for HDFS retry backoff
        hdfs_user: HDFS user name for WebHDFS requests
    """

    local_output_dir: str = _OUTPUT_BASE_DEFAULT
    hdfs_webhdfs_port: int = 9870
    hdfs_max_retries: int = 3
    hdfs_retry_delay: float = 1.0
    hdfs_user: str = "root"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary (camelCase keys)."""
        return {
            "localOutputDir": self.local_output_dir,
            "hdfsWebhdfsPort": self.hdfs_webhdfs_port,
            "hdfsMaxRetries": self.hdfs_max_retries,
            "hdfsRetryDelay": self.hdfs_retry_delay,
            "hdfsUser": self.hdfs_user,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StorageConfig:
        """Create from a dictionary (supports camelCase and snake_case keys)."""
        return cls(
            local_output_dir=str(
                data.get("local_output_dir", data.get("localOutputDir", _OUTPUT_BASE_DEFAULT))
            ),
            hdfs_webhdfs_port=int(data.get("hdfs_webhdfs_port", data.get("hdfsWebhdfsPort", 9870))),
            hdfs_max_retries=int(data.get("hdfs_max_retries", data.get("hdfsMaxRetries", 3))),
            hdfs_retry_delay=float(data.get("hdfs_retry_delay", data.get("hdfsRetryDelay", 1.0))),
            hdfs_user=str(data.get("hdfs_user", data.get("hdfsUser", "root"))),
        )

    @classmethod
    def from_env(cls) -> StorageConfig:
        """Create from environment variables.

        Recognised variables (all optional):
            AFL_LOCAL_OUTPUT_DIR
            AFL_WEBHDFS_PORT
            AFL_HDFS_MAX_RETRIES
            AFL_HDFS_RETRY_DELAY
            HADOOP_USER_NAME
        """
        defaults = cls()
        return cls(
            local_output_dir=get_output_base(),
            hdfs_webhdfs_port=int(
                os.environ.get("AFL_WEBHDFS_PORT", str(defaults.hdfs_webhdfs_port))
            ),
            hdfs_max_retries=int(
                os.environ.get("AFL_HDFS_MAX_RETRIES", str(defaults.hdfs_max_retries))
            ),
            hdfs_retry_delay=float(
                os.environ.get("AFL_HDFS_RETRY_DELAY", str(defaults.hdfs_retry_delay))
            ),
            hdfs_user=os.environ.get("HADOOP_USER_NAME", defaults.hdfs_user),
        )


@dataclass
class ResolverConfig:
    """Dependency resolver configuration.

    Attributes:
        source_paths: Additional directories to scan for FFL sources
        auto_resolve: Enable automatic dependency resolution
        mongodb_resolve: Enable MongoDB namespace lookup during resolution
    """

    source_paths: list[str] = field(default_factory=list)
    auto_resolve: bool = False
    mongodb_resolve: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolverConfig:
        """Create from a dictionary."""
        return cls(
            source_paths=data.get("source_paths", []),
            auto_resolve=data.get("auto_resolve", False),
            mongodb_resolve=data.get("mongodb_resolve", False),
        )

    @classmethod
    def from_env(cls) -> ResolverConfig:
        """Create from environment variables.

        Recognised variables (all optional):
            AFL_RESOLVER_SOURCE_PATHS  (colon-separated list of paths)
            AFL_RESOLVER_AUTO_RESOLVE  ("true"/"1" to enable)
            AFL_RESOLVER_MONGODB_RESOLVE  ("true"/"1" to enable)
        """
        paths_str = os.environ.get("AFL_RESOLVER_SOURCE_PATHS", "")
        source_paths = [p for p in paths_str.split(":") if p] if paths_str else []
        auto_resolve = os.environ.get("AFL_RESOLVER_AUTO_RESOLVE", "").lower() in ("true", "1")
        mongodb_resolve = os.environ.get("AFL_RESOLVER_MONGODB_RESOLVE", "").lower() in (
            "true",
            "1",
        )
        return cls(
            source_paths=source_paths,
            auto_resolve=auto_resolve,
            mongodb_resolve=mongodb_resolve,
        )


@dataclass
class FFLConfig:
    """Top-level FFL configuration.

    Attributes:
        mongodb: MongoDB connection settings
        runner: Runner/poller settings
        storage: Storage settings
        resolver: Dependency resolver settings
    """

    mongodb: MongoDBConfig = field(default_factory=MongoDBConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    resolver: ResolverConfig = field(default_factory=ResolverConfig)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "mongodb": self.mongodb.to_dict(),
            "runner": self.runner.to_dict(),
            "storage": self.storage.to_dict(),
            "resolver": self.resolver.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FFLConfig:
        """Create from a dictionary (e.g. parsed JSON)."""
        return cls(
            mongodb=MongoDBConfig.from_dict(data.get("mongodb", {})),
            runner=RunnerConfig.from_dict(data.get("runner", {})),
            storage=StorageConfig.from_dict(data.get("storage", {})),
            resolver=ResolverConfig.from_dict(data.get("resolver", {})),
        )

    @classmethod
    def from_env(cls) -> FFLConfig:
        """Create from environment variables."""
        return cls(
            mongodb=MongoDBConfig.from_env(),
            runner=RunnerConfig.from_env(),
            storage=StorageConfig.from_env(),
            resolver=ResolverConfig.from_env(),
        )


# -- Config file loading -----------------------------------------------------

DEFAULT_CONFIG_FILENAME = "facetwork.config.json"

_SEARCH_PATHS = [
    Path.cwd,  # current directory
    lambda: Path.home() / ".ffl",  # user home
    lambda: Path("/etc/afl"),  # system-wide
]


def _find_config_file(filename: str = DEFAULT_CONFIG_FILENAME) -> Path | None:
    """Search well-known locations for a config file.

    Search order:
        1. ``$AFL_CONFIG`` environment variable (explicit path)
        2. Current working directory
        3. ``~/.afl/``
        4. ``/etc/ffl/``

    Returns:
        Path to the first config file found, or ``None``.
    """
    explicit = os.environ.get("AFL_CONFIG")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        return None

    for path_fn in _SEARCH_PATHS:
        candidate = path_fn() / filename
        if candidate.is_file():
            return candidate
    return None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge *overlay* into *base* at section level.

    For each top-level key in *overlay*, if both values are dicts they are
    merged field-by-field; otherwise the overlay value replaces the base.

    Returns:
        A new dict with the merged result (does not mutate inputs).
    """
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def _apply_env_overrides(config: FFLConfig) -> FFLConfig:
    """Apply environment variable overrides to an existing config.

    Only overrides fields for which the corresponding env var is set.
    Returns a new :class:`FFLConfig` (does not mutate *config*).
    """
    # MongoDB env overrides
    mongodb = config.mongodb
    env_map_mongo = {
        "AFL_MONGODB_URL": "url",
        "AFL_MONGODB_USERNAME": "username",
        "AFL_MONGODB_PASSWORD": "password",
        "AFL_MONGODB_AUTH_SOURCE": "auth_source",
        "AFL_MONGODB_DATABASE": "database",
    }
    mongo_overrides = {}
    for env_var, field_name in env_map_mongo.items():
        val = os.environ.get(env_var)
        if val is not None:
            mongo_overrides[field_name] = val
    if mongo_overrides:
        d = mongodb.to_dict()
        d.update(mongo_overrides)
        mongodb = MongoDBConfig.from_dict(d)

    # Runner env overrides
    runner = config.runner
    runner_changed = False
    runner_dict = {
        "poll_interval_ms": runner.poll_interval_ms,
        "max_concurrent": runner.max_concurrent,
        "heartbeat_interval_ms": runner.heartbeat_interval_ms,
        "sweep_interval_ms": runner.sweep_interval_ms,
        "use_registry": runner.use_registry,
        "topics": list(runner.topics),
    }
    env_map_runner: list[tuple[str, str, type]] = [
        ("AFL_POLL_INTERVAL_MS", "poll_interval_ms", int),
        ("AFL_MAX_CONCURRENT", "max_concurrent", int),
        ("AFL_HEARTBEAT_INTERVAL_MS", "heartbeat_interval_ms", int),
        ("AFL_SWEEP_INTERVAL_MS", "sweep_interval_ms", int),
    ]
    for env_var, field_name, conv in env_map_runner:
        val = os.environ.get(env_var)
        if val is not None:
            runner_dict[field_name] = conv(val)
            runner_changed = True
    use_reg = os.environ.get("AFL_USE_REGISTRY")
    if use_reg is not None:
        runner_dict["use_registry"] = use_reg.strip().lower() in ("true", "1")
        runner_changed = True
    topics_env = os.environ.get("AFL_RUNNER_TOPICS")
    if topics_env is not None:
        runner_dict["topics"] = [t.strip() for t in topics_env.split(",") if t.strip()]
        runner_changed = True
    if runner_changed:
        runner = RunnerConfig.from_dict(runner_dict)

    # Storage env overrides
    storage = config.storage
    storage_changed = False
    storage_dict = {
        "local_output_dir": storage.local_output_dir,
        "hdfs_webhdfs_port": storage.hdfs_webhdfs_port,
        "hdfs_max_retries": storage.hdfs_max_retries,
        "hdfs_retry_delay": storage.hdfs_retry_delay,
        "hdfs_user": storage.hdfs_user,
    }
    env_map_storage: list[tuple[str, str, type]] = [
        ("AFL_LOCAL_OUTPUT_DIR", "local_output_dir", str),
        ("AFL_WEBHDFS_PORT", "hdfs_webhdfs_port", int),
        ("AFL_HDFS_MAX_RETRIES", "hdfs_max_retries", int),
        ("AFL_HDFS_RETRY_DELAY", "hdfs_retry_delay", float),
        ("HADOOP_USER_NAME", "hdfs_user", str),
    ]
    for env_var, field_name, conv in env_map_storage:
        val = os.environ.get(env_var)
        if val is not None:
            storage_dict[field_name] = conv(val)
            storage_changed = True
    if storage_changed:
        storage = StorageConfig.from_dict(storage_dict)

    # Resolver env overrides
    resolver = config.resolver
    resolver_env = ResolverConfig.from_env()
    resolver_changed = False
    if os.environ.get("AFL_RESOLVER_SOURCE_PATHS"):
        resolver_changed = True
    if os.environ.get("AFL_RESOLVER_AUTO_RESOLVE"):
        resolver_changed = True
    if os.environ.get("AFL_RESOLVER_MONGODB_RESOLVE"):
        resolver_changed = True
    if resolver_changed:
        resolver = resolver_env

    return FFLConfig(
        mongodb=mongodb,
        runner=runner,
        storage=storage,
        resolver=resolver,
    )


def _load_json(path: Path | None) -> dict[str, Any] | None:
    """Read and parse a JSON file, returning None on any error."""
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_config(path: str | Path | None = None) -> FFLConfig:
    """Load FFL configuration.

    Resolution order (highest to lowest priority):
        1. Environment variables (``AFL_*``)
        2. ``AFL_ENV`` overlay file (``afl.config.{AFL_ENV}.json``)
        3. Base config file (explicit *path*, or found via search)
        4. Built-in defaults

    Args:
        path: Optional explicit path to a JSON config file.

    Returns:
        Populated :class:`FFLConfig` instance.
    """
    config_path: Path | None = Path(path) if path else _find_config_file()
    base_data = _load_json(config_path) or {}

    # Apply AFL_ENV overlay
    env_name = os.environ.get("AFL_ENV", "")
    if env_name:
        overlay_filename = f"facetwork.config.{env_name}.json"
        # Search relative to the base config file's directory first
        overlay_path: Path | None = None
        if config_path and config_path.is_file():
            candidate = config_path.parent / overlay_filename
            if candidate.is_file():
                overlay_path = candidate
        if overlay_path is None:
            overlay_path = _find_config_file(overlay_filename)
        overlay_data = _load_json(overlay_path)
        if overlay_data:
            base_data = _deep_merge(base_data, overlay_data)

    if base_data:
        config = FFLConfig.from_dict(base_data)
    else:
        config = FFLConfig()

    # Env vars always override file values
    return _apply_env_overrides(config)


# -- Singleton access --------------------------------------------------------

_config_cache: FFLConfig | None = None


def get_config() -> FFLConfig:
    """Return the global FFL configuration singleton.

    On first call, loads config via :func:`load_config`. Subsequent calls
    return the cached instance. Use :func:`_reset_config_cache` in tests
    to force re-loading.
    """
    global _config_cache
    if _config_cache is None:
        _config_cache = load_config()
    return _config_cache


def _reset_config_cache() -> None:
    """Clear the cached config singleton (for testing)."""
    global _config_cache
    _config_cache = None
