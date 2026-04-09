"""AgentPoller entry point for the hiv-drug-resistance example (legacy).

Usage:
    PYTHONPATH=. python examples/hiv-drug-resistance/agent.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_handlers

from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main() -> None:
    """Start the AgentPoller with all resistance handlers."""
    poller = AgentPoller(config=AgentPollerConfig(service_name="hiv-drug-resistance"))
    register_all_handlers(poller)
    print("HIV Drug Resistance AgentPoller started")
    poller.run()


if __name__ == "__main__":
    main()
