"""RegistryRunner entry point for the noaa-weather example.

Usage:
    PYTHONPATH=. python examples/noaa-weather/agent_registry.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_registry_handlers

from afl.runtime.registry_runner import RegistryRunner


def main() -> None:
    """Start the RegistryRunner with all weather handlers."""
    runner = RegistryRunner(service_name="noaa-weather")
    register_all_registry_handlers(runner)
    print(f"NOAA Weather RegistryRunner started with {runner.handler_count} handlers")
    runner.run()


if __name__ == "__main__":
    main()
