"""osm.equity mapping-equity handlers - registration aggregator."""

from __future__ import annotations

from .acquisition.acquisition_handlers import register_acquisition_handlers
from .analysis.analysis_handlers import register_analysis_handlers
from .atlas.atlas_handlers import register_atlas_handlers
from .design.design_handlers import register_design_handlers
from .metrics.metrics_handlers import register_metrics_handlers
from .reporting.reporting_handlers import register_reporting_handlers


def register_all_handlers(poller) -> None:
    """Register every handler with an AgentPoller."""
    register_design_handlers(poller)
    register_acquisition_handlers(poller)
    register_metrics_handlers(poller)
    register_analysis_handlers(poller)
    register_reporting_handlers(poller)
    register_atlas_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register every handler with a RegistryRunner."""
    from .acquisition.acquisition_handlers import register_handlers as reg_acq
    from .analysis.analysis_handlers import register_handlers as reg_ana
    from .atlas.atlas_handlers import register_handlers as reg_atlas
    from .design.design_handlers import register_handlers as reg_des
    from .metrics.metrics_handlers import register_handlers as reg_met
    from .reporting.reporting_handlers import register_handlers as reg_rep

    reg_des(runner)
    reg_acq(runner)
    reg_met(runner)
    reg_ana(runner)
    reg_rep(runner)
    reg_atlas(runner)
