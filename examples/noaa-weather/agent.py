"""AgentPoller entry point for the noaa-weather example (legacy).

Usage:
    PYTHONPATH=. python examples/noaa-weather/agent.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_handlers

from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main() -> None:
    """Start the AgentPoller with all weather handlers."""
    poller = AgentPoller(config=AgentPollerConfig(service_name="noaa-weather"))
    register_all_handlers(poller)
    print("NOAA Weather AgentPoller started")
    poller.run()


if __name__ == "__main__":
    main()
