"""Handler registration for the noaa-weather example.

Imports are deferred to function bodies to avoid import-lock deadlocks
when the RegistryRunner concurrently imports handler modules from
separate threads (each triggers handlers/__init__.py which would
otherwise transitively import all siblings).
"""

from __future__ import annotations


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    from .analysis.analysis_handlers import register_analysis_handlers
    from .catalog.catalog_handlers import register_catalog_handlers
    from .geocode.geocode_handlers import register_geocode_handlers
    from .ingest.ingest_handlers import register_ingest_handlers

    register_catalog_handlers(poller)
    register_ingest_handlers(poller)
    register_analysis_handlers(poller)
    register_geocode_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .analysis.analysis_handlers import register_handlers as reg_analysis
    from .catalog.catalog_handlers import register_handlers as reg_catalog
    from .geocode.geocode_handlers import register_handlers as reg_geocode
    from .ingest.ingest_handlers import register_handlers as reg_ingest

    reg_catalog(runner)
    reg_ingest(runner)
    reg_analysis(runner)
    reg_geocode(runner)
