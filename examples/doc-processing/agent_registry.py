"""RegistryRunner entry point for the doc-processing example.

Usage:
    PYTHONPATH=. python examples/doc-processing/agent_registry.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_registry_handlers

from facetwork.runtime.registry_runner import create_registry_runner


def main() -> None:
    """Start the RegistryRunner with all doc-processing handlers."""
    runner = create_registry_runner("doc-processing")
    register_all_registry_handlers(runner)
    print(
        f"Document Processing RegistryRunner started with {len(runner.registered_names())} handlers"
    )
    runner.start()


if __name__ == "__main__":
    main()
