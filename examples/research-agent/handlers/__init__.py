"""AI Research Agent handlers — registration aggregator."""

from __future__ import annotations

from .planning.planning_handlers import register_planning_handlers
from .gathering.gathering_handlers import register_gathering_handlers
from .analysis.analysis_handlers import register_analysis_handlers
from .writing.writing_handlers import register_writing_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_planning_handlers(poller)
    register_gathering_handlers(poller)
    register_analysis_handlers(poller)
    register_writing_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .planning.planning_handlers import register_handlers as reg_planning
    from .gathering.gathering_handlers import register_handlers as reg_gathering
    from .analysis.analysis_handlers import register_handlers as reg_analysis
    from .writing.writing_handlers import register_handlers as reg_writing

    reg_planning(runner)
    reg_gathering(runner)
    reg_analysis(runner)
    reg_writing(runner)
