"""AgentPoller entry point for the site-analyzer example (legacy).

Usage:
    PYTHONPATH=. python examples/site-analyzer/agent.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_handlers

from afl.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main() -> None:
    """Start the AgentPoller with all site-analyzer handlers."""
    poller = AgentPoller(config=AgentPollerConfig(service_name="site-analyzer"))
    register_all_handlers(poller)
    print("Site Analyzer AgentPoller started")
    poller.run()


if __name__ == "__main__":
    main()
