#!/usr/bin/env python3
"""Continental LZ Pipeline Agent — RegistryRunner mode.

Registers the subset of handlers needed for the LZ road infrastructure
and GTFS transit analysis pipelines across US, Canada, and Europe.

Handler modules reused from examples/osm-geocoder/handlers/:
  - cache_handlers:       ~250 region cache facets (OSM PBF downloads)
  - operations_handlers:  13 operations (download orchestration)
  - graphhopper_handlers: ~200 GraphHopper facets (routing graph builds)
  - population_handlers:  11 population facets (LZ anchor cities)
  - zoom_handlers:        9 zoom builder facets (LZ pipeline stages)
  - gtfs_handlers:        12 GTFS facets (transit analysis)

Usage:
    AFL_USE_REGISTRY=1 AFL_MONGODB_URL=mongodb://localhost:27017 \\
        PYTHONPATH=../.. python agent.py

For Docker mode, environment is configured via docker-compose.yml.
"""

from facetwork.runtime.agent_runner import AgentConfig, run_agent

config = AgentConfig(
    service_name="continental-lz",
    server_group="continental",
    max_concurrent=4,  # GraphHopper is memory-intensive
    mongodb_database="afl_continental_lz",
)


def register(poller=None, runner=None):
    """Register Continental LZ handlers with the active runner."""
    from handlers.cache_handlers import register_handlers as reg_cache
    from handlers.graphhopper_handlers import register_handlers as reg_graphhopper
    from handlers.gtfs_handlers import register_handlers as reg_gtfs
    from handlers.operations_handlers import register_handlers as reg_operations
    from handlers.population_handlers import register_handlers as reg_population
    from handlers.zoom_handlers import register_handlers as reg_zoom

    if runner:
        for reg in [reg_cache, reg_operations, reg_graphhopper, reg_population, reg_zoom, reg_gtfs]:
            reg(runner)


if __name__ == "__main__":
    run_agent(config, register)
