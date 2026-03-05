"""Site-Selection Debate -- RegistryRunner entry point (RECOMMENDED).

This is the primary way to run the site-selection debate agent.
It uses RegistryRunner to auto-load handlers from DB registrations.

Usage:
    PYTHONPATH=. python examples/site-selection-debate/agent_registry.py
"""

from __future__ import annotations

import os
import sys

# Ensure handlers are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_registry_handlers

from afl.runtime.registry_runner import create_registry_runner


def main() -> None:
    """Start the RegistryRunner with all site-selection debate handlers."""
    runner = create_registry_runner("site-selection-debate")
    register_all_registry_handlers(runner)
    print(
        f"Site-selection debate RegistryRunner started with {len(runner.registered_names())} handlers"
    )
    runner.start()


if __name__ == "__main__":
    main()
