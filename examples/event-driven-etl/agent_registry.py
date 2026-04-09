"""RegistryRunner entry point for the event-driven ETL example.

Usage:
    PYTHONPATH=. python examples/event-driven-etl/agent_registry.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_registry_handlers

from facetwork.runtime.registry_runner import create_registry_runner


def main() -> None:
    """Start the RegistryRunner with all ETL handlers."""
    runner = create_registry_runner("event-driven-etl")
    register_all_registry_handlers(runner)
    print(f"ETL RegistryRunner started with {len(runner.registered_names())} handlers")
    runner.start()


if __name__ == "__main__":
    main()
