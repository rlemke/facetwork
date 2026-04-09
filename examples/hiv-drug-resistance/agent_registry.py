"""RegistryRunner entry point for the hiv-drug-resistance example.

Usage:
    PYTHONPATH=. python examples/hiv-drug-resistance/agent_registry.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_registry_handlers

from facetwork.runtime.registry_runner import create_registry_runner


def main() -> None:
    """Start the RegistryRunner with all resistance handlers."""
    runner = create_registry_runner("hiv-drug-resistance")
    register_all_registry_handlers(runner)
    print(
        f"HIV Drug Resistance RegistryRunner started with {len(runner.registered_names())} handlers"
    )
    runner.start()


if __name__ == "__main__":
    main()
