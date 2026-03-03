"""Handler registration for the noaa-weather example."""

from __future__ import annotations

from .analysis.analysis_handlers import register_analysis_handlers
from .discovery.discovery_handlers import register_discovery_handlers
from .geocode.geocode_handlers import register_geocode_handlers
from .ingest.ingest_handlers import register_ingest_handlers
from .interpret.interpret_handlers import register_interpret_handlers
from .qc.qc_handlers import register_qc_handlers
from .report.report_handlers import register_report_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_discovery_handlers(poller)
    register_ingest_handlers(poller)
    register_qc_handlers(poller)
    register_analysis_handlers(poller)
    register_geocode_handlers(poller)
    register_interpret_handlers(poller)
    register_report_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .analysis.analysis_handlers import register_handlers as reg_analysis
    from .discovery.discovery_handlers import register_handlers as reg_discovery
    from .geocode.geocode_handlers import register_handlers as reg_geocode
    from .ingest.ingest_handlers import register_handlers as reg_ingest
    from .interpret.interpret_handlers import register_handlers as reg_interpret
    from .qc.qc_handlers import register_handlers as reg_qc
    from .report.report_handlers import register_handlers as reg_report

    reg_discovery(runner)
    reg_ingest(runner)
    reg_qc(runner)
    reg_analysis(runner)
    reg_geocode(runner)
    reg_interpret(runner)
    reg_report(runner)
