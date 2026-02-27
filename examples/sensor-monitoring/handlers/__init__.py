"""Sensor Monitoring handlers -- registration aggregator."""

from __future__ import annotations

from .ingestion.ingestion_handlers import register_ingestion_handlers
from .analysis.analysis_handlers import register_analysis_handlers
from .reporting.reporting_handlers import register_reporting_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_ingestion_handlers(poller)
    register_analysis_handlers(poller)
    register_reporting_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .ingestion.ingestion_handlers import register_handlers as reg_ingestion
    from .analysis.analysis_handlers import register_handlers as reg_analysis
    from .reporting.reporting_handlers import register_handlers as reg_reporting

    reg_ingestion(runner)
    reg_analysis(runner)
    reg_reporting(runner)
