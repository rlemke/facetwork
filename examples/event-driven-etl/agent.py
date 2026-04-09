"""AgentPoller entry point for the event-driven ETL example (legacy).

Usage:
    PYTHONPATH=. python examples/event-driven-etl/agent.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_handlers

from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main() -> None:
    """Start the AgentPoller with all ETL handlers."""
    poller = AgentPoller(config=AgentPollerConfig(service_name="event-driven-etl"))
    register_all_handlers(poller)
    print("ETL AgentPoller started")
    poller.run()


if __name__ == "__main__":
    main()
