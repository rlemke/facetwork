"""Handler registration for the hiv-drug-resistance example."""

from __future__ import annotations

from .analysis.analysis_handlers import register_analysis_handlers
from .interpretation.interpretation_handlers import register_interpretation_handlers
from .reporting.reporting_handlers import register_reporting_handlers
from .sequencing.sequencing_handlers import register_sequencing_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_sequencing_handlers(poller)
    register_analysis_handlers(poller)
    register_interpretation_handlers(poller)
    register_reporting_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .analysis.analysis_handlers import register_handlers as reg_analysis
    from .interpretation.interpretation_handlers import register_handlers as reg_interpretation
    from .reporting.reporting_handlers import register_handlers as reg_reporting
    from .sequencing.sequencing_handlers import register_handlers as reg_sequencing

    reg_sequencing(runner)
    reg_analysis(runner)
    reg_interpretation(runner)
    reg_reporting(runner)
