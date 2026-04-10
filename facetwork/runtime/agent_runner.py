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

"""Shared agent bootstrap helper.

Provides ``make_store()``, ``AgentConfig``, and ``run_agent()`` so that
example agent scripts can avoid duplicating store creation, evaluator
setup, signal handling, and the registry/poller branching logic.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .evaluator import Evaluator
    from .persistence import PersistenceAPI


def make_store(database: str = "") -> PersistenceAPI:
    """Create a persistence store from environment configuration.

    Uses ``AFL_MONGODB_URL`` to connect to MongoDB when set, otherwise
    falls back to an in-memory store.

    Args:
        database: MongoDB database name.  When empty, reads
            ``AFL_MONGODB_DATABASE`` (default ``"facetwork"``).
    """
    mongodb_url = os.environ.get("AFL_MONGODB_URL")
    db_name = database or os.environ.get("AFL_MONGODB_DATABASE", "facetwork")

    if mongodb_url:
        from .mongo_store import MongoStore

        print(f"Using MongoDB: {mongodb_url}/{db_name}")
        return MongoStore(connection_string=mongodb_url, database_name=db_name)

    print("Using in-memory store (set AFL_MONGODB_URL for MongoDB)")
    from .memory_store import MemoryStore

    return MemoryStore()


_SENTINEL = -1


@dataclass
class AgentConfig:
    """Lightweight configuration for ``run_agent()``.

    Only the fields that vary across example agents are exposed here;
    everything else uses the defaults from ``RegistryRunnerConfig`` /
    ``AgentPollerConfig``.
    """

    service_name: str
    server_group: str
    poll_interval_ms: int = _SENTINEL
    max_concurrent: int = _SENTINEL
    mongodb_database: str = ""

    def __post_init__(self) -> None:
        if self.poll_interval_ms == _SENTINEL:
            from ..config import get_config

            self.poll_interval_ms = get_config().runner.poll_interval_ms
        if self.max_concurrent == _SENTINEL:
            from ..config import get_config

            self.max_concurrent = get_config().runner.max_concurrent


def run_agent(
    config: AgentConfig,
    register: Callable[..., object],
) -> None:
    """Bootstrap and run an FFL agent.

    1. Creates a persistence store (MongoDB or in-memory).
    2. Creates an ``Evaluator`` with telemetry enabled.
    3. Branches on ``AFL_USE_REGISTRY`` env var:
       - ``"1"``: creates a ``RegistryRunner``, calls
         ``register(runner=runner)``, starts the runner.
       - otherwise: creates an ``AgentPoller``, calls
         ``register(poller=poller)``, starts the poller.
    4. Installs SIGTERM/SIGINT handlers for graceful shutdown.

    Args:
        config: Agent identity and tuning knobs.
        register: Callback invoked with either ``poller=`` or ``runner=``
            keyword argument so the caller can wire up handlers.
    """
    from ..config import get_config
    from .evaluator import Evaluator
    from .telemetry import Telemetry

    store = make_store(database=config.mongodb_database)
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=True))

    global_cfg = get_config()
    use_registry = global_cfg.runner.use_registry

    topics = list(global_cfg.runner.topics)

    if use_registry:
        _run_registry(config, store, evaluator, topics, register)
    else:
        _run_poller(config, store, evaluator, register)


def _run_registry(
    config: AgentConfig,
    store: PersistenceAPI,
    evaluator: Evaluator,
    topics: list[str],
    register: Callable[..., object],
) -> None:
    from .registry_runner import RegistryRunner, RegistryRunnerConfig

    runner_config = RegistryRunnerConfig(
        service_name=config.service_name,
        server_group=config.server_group,
        poll_interval_ms=config.poll_interval_ms,
        max_concurrent=config.max_concurrent,
        topics=topics,
    )

    runner = RegistryRunner(persistence=store, evaluator=evaluator, config=runner_config)
    register(runner=runner)

    def shutdown(signum: int, frame: object) -> None:
        print("\nShutting down...")
        runner.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if topics:
        print(f"Topic filter: {topics}")
    print(f"{config.service_name} started (RegistryRunner mode). Press Ctrl+C to stop.")
    runner.start()


def _run_poller(
    config: AgentConfig,
    store: PersistenceAPI,
    evaluator: Evaluator,
    register: Callable[..., object],
) -> None:
    from .agent_poller import AgentPoller, AgentPollerConfig

    poller_config = AgentPollerConfig(
        service_name=config.service_name,
        server_group=config.server_group,
        poll_interval_ms=config.poll_interval_ms,
        max_concurrent=config.max_concurrent,
    )

    poller = AgentPoller(persistence=store, evaluator=evaluator, config=poller_config)
    register(poller=poller)

    def shutdown(signum: int, frame: object) -> None:
        print("\nShutting down...")
        poller.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"{config.service_name} started. Press Ctrl+C to stop.")
    poller.start()
