"""AgentPoller entry point for the doc-processing example (legacy).

Usage:
    PYTHONPATH=. python examples/doc-processing/agent.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers import register_all_handlers

from afl.runtime.agent_poller import AgentPoller, AgentPollerConfig


def main() -> None:
    """Start the AgentPoller with all doc-processing handlers."""
    poller = AgentPoller(config=AgentPollerConfig(service_name="doc-processing"))
    register_all_handlers(poller)
    print("Document Processing AgentPoller started")
    poller.run()


if __name__ == "__main__":
    main()
