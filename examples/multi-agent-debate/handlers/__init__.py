"""Multi-Agent Debate handlers — registration aggregator."""

from __future__ import annotations

from .framing.framing_handlers import register_framing_handlers
from .argumentation.argumentation_handlers import register_argumentation_handlers
from .evaluation.evaluation_handlers import register_evaluation_handlers
from .synthesis.synthesis_handlers import register_synthesis_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_framing_handlers(poller)
    register_argumentation_handlers(poller)
    register_evaluation_handlers(poller)
    register_synthesis_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .framing.framing_handlers import register_handlers as reg_framing
    from .argumentation.argumentation_handlers import register_handlers as reg_argumentation
    from .evaluation.evaluation_handlers import register_handlers as reg_evaluation
    from .synthesis.synthesis_handlers import register_handlers as reg_synthesis

    reg_framing(runner)
    reg_argumentation(runner)
    reg_evaluation(runner)
    reg_synthesis(runner)
