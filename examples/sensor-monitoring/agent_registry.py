"""Sensor Monitoring -- RegistryRunner entry point (RECOMMENDED).

This is the primary way to run the sensor monitoring agent.
It uses RegistryRunner to auto-load handlers from DB registrations.

Usage:
    PYTHONPATH=. python examples/sensor-monitoring/agent_registry.py
"""

from __future__ import annotations

import sys
import os

# Ensure handlers are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from afl.runtime.registry_runner import RegistryRunner
from handlers import register_all_registry_handlers


def main() -> None:
    """Start the RegistryRunner with all sensor monitoring handlers."""
    runner = RegistryRunner(service_name="sensor-monitoring")
    register_all_registry_handlers(runner)
    print(f"Sensor monitoring RegistryRunner started with {runner.handler_count} handlers")
    runner.run()


if __name__ == "__main__":
    main()
