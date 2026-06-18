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

"""CLI entry point for the FFL runner service.

Usage:
    python -m afl.runtime.runner [options]
"""

import argparse
import logging
import os
import signal
from pathlib import Path

from .service import RunnerConfig, RunnerService

_drift_logger = logging.getLogger("facetwork.runtime.runner.drift")


class _FacetNameCollector:
    """Duck-typed stand-in for ``RegistryRunner`` that records facet names
    without writing to MongoDB.

    Every ``register_handlers(runner)`` in the example packages calls
    ``runner.register_handler(facet_name=..., module_uri=..., entrypoint=...)``.
    Running them against this collector yields the source-of-truth facet set
    in-process — which we then diff against what's in the registry to flag
    drift.
    """

    def __init__(self) -> None:
        self.names: set[str] = set()

    def register_handler(self, facet_name: str, **_: object) -> None:
        self.names.add(facet_name)

    def _refresh_registry(self) -> None:  # pragma: no cover — defensive stub
        pass


def _warn_registry_drift(loaded_facet_names: list[str]) -> None:
    """Compare in-process declared facets against the registry-loaded set.

    Logs a WARNING listing facets present in installed example code but
    missing from ``handler_registrations`` — the silent-failure mode where
    a new facet was added in source but no one re-seeded.
    Best-effort: any discovery failure is debug-logged and swallowed.
    """
    try:
        from facetwork.examples import discover_all_examples
    except Exception as exc:  # pragma: no cover — defensive
        _drift_logger.debug("Drift detection skipped (examples API unavailable): %s", exc)
        return

    try:
        repo_root = Path(os.environ.get("REPO_ROOT") or os.getcwd())
        packages = discover_all_examples(repo_root)
    except Exception as exc:  # pragma: no cover — defensive
        _drift_logger.debug("Drift detection skipped (discovery failed): %s", exc)
        return

    loaded = set(loaded_facet_names)
    # Per-package collection: only flag drift for packages the user clearly
    # chose to host (≥1 facet already registered). An example fully absent
    # from the registry is one the user didn't seed — flagging it would just
    # be noise (every installed example shows up otherwise).
    drifted: dict[str, list[str]] = {}  # pkg.name -> sorted missing
    fully_unseeded: list[str] = []
    skipped: list[str] = []
    total_expected = 0

    for pkg in packages:
        if getattr(pkg, "handlers_path", None) is None and getattr(pkg, "source", "") == "local":
            continue
        per_pkg = _FacetNameCollector()
        try:
            pkg.register_handlers(per_pkg)
        except Exception as exc:
            # Module not importable in THIS process — same filter that
            # preload(verify=True) applies on the other side. Expected for
            # examples this runner wasn't built to host.
            skipped.append(f"{pkg.name} ({type(exc).__name__})")
            continue
        declared = per_pkg.names
        total_expected += len(declared)
        if not declared:
            continue
        present = declared & loaded
        missing = sorted(declared - loaded)
        if not missing:
            continue
        if not present:
            # No facets from this example in the registry → user didn't seed
            # it, presumably on purpose. Don't WARN, just debug.
            fully_unseeded.append(pkg.name)
            continue
        drifted[pkg.name] = missing

    if not drifted:
        if total_expected:
            _drift_logger.info(
                "Registry drift check: OK (every facet from chosen example(s) present in registry)"
            )
        if fully_unseeded:
            _drift_logger.debug(
                "Installed example(s) with zero facets in the registry (not hosted here): %s",
                ", ".join(fully_unseeded),
            )
        return

    total_missing = sum(len(v) for v in drifted.values())
    parts = []
    for name, missing in drifted.items():
        preview = ", ".join(missing[:6]) + (f", … (+{len(missing)-6} more)" if len(missing) > 6 else "")
        parts.append(f"{name}: {preview}")
    _drift_logger.warning(
        "Registry drift: %d facet(s) declared in installed example code but NOT in "
        "handler_registrations — tasks for these will sit pending with server_id=None. "
        "Re-seed with `python -m facetwork.examples <name>` "
        "(or `scripts/start-runner --example <name>`). %s",
        total_missing,
        "; ".join(parts),
    )
    if skipped:
        _drift_logger.debug(
            "Drift detection skipped %d non-loadable example(s) here: %s",
            len(skipped),
            "; ".join(skipped),
        )


def main() -> None:
    """Run the FFL runner service."""
    parser = argparse.ArgumentParser(
        description="AFL distributed runner service",
        prog="afl-runner",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to FFL config file",
    )
    parser.add_argument(
        "--server-group",
        default=os.environ.get("AFL_SERVER_GROUP", "default"),
        help="Server group name (env AFL_SERVER_GROUP; default: default). "
        "Groups servers by role in fleet status / the dashboard. Every Facetwork "
        "server is a runner (there is no 'master'); infra services (MongoDB, MinIO, "
        "dashboard) are addressed by URL, not run as fleet servers.",
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
        default=8090,
        help="HTTP status port (auto-increments if in use; default: 8090)",
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
    parser.add_argument(
        "--continuation",
        default=None,
        choices=["inline", "off", "shared"],
        help="Orchestration role (env AFL_CONTINUATION_MODE; default inline). "
        "inline = poll the shared _fw_continue backlog + run the stuck-step sweep "
        "(today's behaviour); off = handler-only (keep inline cascade after own "
        "handler, but don't poll the backlog or sweep); shared = dedicated "
        "ffl-runner that owns the backlog + sweep. "
        "See docs/architecture/ffl-runner-orchestration-tier.md.",
    )

    args = parser.parse_args()

    # Configure logging
    from facetwork.logging import configure_logging

    configure_logging(
        level=args.log_level,
        log_file=args.log_file,
        log_format=args.log_format,
    )

    # Load FFL config for MongoDB connection
    from facetwork.config import load_config
    from facetwork.runtime import Evaluator, Telemetry
    from facetwork.runtime.agent import ToolRegistry
    from facetwork.runtime.mongo_store import MongoStore

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

    # Load handler registrations from MongoDB when --registry is set.
    # Only register (and therefore claim tasks for) handlers this process can
    # actually load — a runner must never accept a task whose handler it can't
    # run. In a multi-runner deployment each runner hosts a subset of the
    # handler universe, so verify=True drops the rest.
    if args.registry:
        from facetwork.runtime.dispatcher import RegistryDispatcher

        def _make_proxy(d: RegistryDispatcher, name: str):
            def _proxy(payload: dict) -> dict | None:
                return d.dispatch(name, payload)

            return _proxy

        dispatcher = RegistryDispatcher(persistence=store)
        loadable = dispatcher.preload(verify=True)
        for facet_name in dispatcher.dispatchable_facets():
            tool_registry.register(facet_name, _make_proxy(dispatcher, facet_name))
        if loadable:
            print(f"  Registry handlers: {loadable} loadable (cached)")

        # Registry-drift detection: a facet declared in installed example code
        # but missing from handler_registrations means tasks for that facet
        # will sit pending with server_id=None forever (claim_task is
        # name-filtered, no runner advertises an unregistered name).
        # This is the silent-failure mode that bit the NA tiled render: a new
        # facet was added in source but the registry wasn't re-seeded.
        # Surface it at startup, before workflows hang on it.
        _warn_registry_drift(dispatcher.dispatchable_facets())

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
        continuation_mode=args.continuation or "",
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

    print(f"Starting FFL runner: {runner_config.service_name}")
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
