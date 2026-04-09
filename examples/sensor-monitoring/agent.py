"""Sensor Monitoring -- AgentPoller entry point (legacy/alternative).

For the recommended approach, use agent_registry.py which uses RegistryRunner.

Usage:
    PYTHONPATH=. python examples/sensor-monitoring/agent.py
"""

from __future__ import annotations

import os
import sys

# Ensure handlers are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_handlers

from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main() -> None:
    """Start the AgentPoller with all sensor monitoring handlers."""
    poller = AgentPoller(config=AgentPollerConfig(service_name="sensor-monitoring"))
    register_all_handlers(poller)
    print("Sensor monitoring AgentPoller started")
    poller.run()


if __name__ == "__main__":
    main()
