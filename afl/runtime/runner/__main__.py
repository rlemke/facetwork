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

"""CLI entry point for the AFL runner service.

Usage:
    python -m afl.runtime.runner [options]
"""

import argparse
import signal

from .service import RunnerConfig, RunnerService


def main() -> None:
    """Run the AFL runner service."""
    parser = argparse.ArgumentParser(
        description="AFL distributed runner service",
        prog="afl-runner",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to AFL config file",
    )
    parser.add_argument(
        "--server-group",
        default="default",
        help="Server group name (default: default)",
    )
    parser.add_argument(
        "--service-name",
        default="afl-runner",
        help="Service name (default: afl-runner)",
    )
    parser.add_argument(
        "--server-name",
        default="",
        help="Server hostname (default: auto-detect)",
    )
    parser.add_argument(
        "--topics",
        nargs="*",
        default=[],
        help="Qualified event facet names to handle, e.g. 'ns.CountDocuments' (default: all with handlers)",
    )
    parser.add_argument(
        "--task-list",
        default="default",
        help="Task list to poll (default: default)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Poll interval in ms (default: from config or 1000)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=None,
        help="Heartbeat interval in ms (default: from config or 10000)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=None,
        help="Max concurrent work items (default: from config or 2)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP status port (auto-increments if in use; default: 8080)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="FILE",
        help="Log to file instead of stderr",
    )
    parser.add_argument(
        "--log-format",
        default="json",
        choices=["json", "text"],
        help="Log format (default: json)",
    )
    parser.add_argument(
        "--registry",
        action="store_true",
        default=None,
        help="Load handler registrations from MongoDB (env: AFL_USE_REGISTRY=1)",
    )

    args = parser.parse_args()

    # Configure logging
    from afl.logging import configure_logging

    configure_logging(
        level=args.log_level,
        log_file=args.log_file,
        log_format=args.log_format,
    )

    # Load AFL config for MongoDB connection
    from afl.config import load_config
    from afl.runtime import Evaluator, Telemetry
    from afl.runtime.agent import ToolRegistry
    from afl.runtime.mongo_store import MongoStore

    config = load_config(args.config)

    # Resolve argparse defaults from config
    if args.poll_interval is None:
        args.poll_interval = config.runner.poll_interval_ms
    if args.heartbeat_interval is None:
        args.heartbeat_interval = config.runner.heartbeat_interval_ms
    if args.max_concurrent is None:
        args.max_concurrent = config.runner.max_concurrent
    if args.registry is None:
        args.registry = config.runner.use_registry

    store = MongoStore.from_config(config.mongodb)
    telemetry = Telemetry(enabled=True)
    evaluator = Evaluator(persistence=store, telemetry=telemetry)
    tool_registry = ToolRegistry()

    # Load handler registrations from MongoDB when --registry is set
    if args.registry:
        from afl.runtime.dispatcher import RegistryDispatcher

        dispatcher = RegistryDispatcher(persistence=store)
        dispatcher.preload()
        registrations = store.list_handler_registrations()
        for reg in registrations:

            def _make_proxy(d: RegistryDispatcher, name: str):  # noqa: E301
                def _proxy(payload: dict) -> dict | None:
                    return d.dispatch(name, payload)

                return _proxy

            tool_registry.register(reg.facet_name, _make_proxy(dispatcher, reg.facet_name))
        if registrations:
            print(f"  Registry handlers: {len(registrations)} loaded (cached)")

    runner_config = RunnerConfig(
        server_group=args.server_group,
        service_name=args.service_name,
        server_name=args.server_name,
        topics=args.topics,
        task_list=args.task_list,
        poll_interval_ms=args.poll_interval,
        heartbeat_interval_ms=args.heartbeat_interval,
        max_concurrent=args.max_concurrent,
        http_port=args.port,
    )

    service = RunnerService(
        persistence=store,
        evaluator=evaluator,
        config=runner_config,
        tool_registry=tool_registry,
    )

    # Signal handlers for graceful shutdown
    def handle_signal(signum: int, frame: object) -> None:
        service.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    print(f"Starting AFL runner: {runner_config.service_name}")
    print(f"  Server group: {runner_config.server_group}")
    print(f"  Server name:  {runner_config.server_name}")
    print(f"  Task list:    {runner_config.task_list}")
    print(f"  Max workers:  {runner_config.max_concurrent}")
    print(f"  Poll interval: {runner_config.poll_interval_ms}ms")
    print(f"  HTTP port:    {runner_config.http_port} (auto-increments if in use)")

    try:
        service.start()
    except KeyboardInterrupt:
        service.stop()


if __name__ == "__main__":
    main()
