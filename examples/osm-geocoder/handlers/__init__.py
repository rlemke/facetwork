"""OSM Geocoder event handlers.

Registers handlers for all OSM, Census TIGER, GraphHopper, elevation, and visualization event facets.

Handler modules are organized into functional subdirectories (amenities/, boundaries/, etc.).
For backward compatibility, ``sys.modules`` aliases map the old flat import paths
(e.g. ``handlers.cache_handlers``) to the new subpackage paths
(e.g. ``handlers.cache.cache_handlers``).
"""

import importlib
import importlib.util
import os
import sys

# ---------------------------------------------------------------------------
# sys.modules aliasing: map old flat paths -> new subpackage paths
# ---------------------------------------------------------------------------

_MODULE_MAP = {
    # shared utilities
    "handlers._output": "handlers.shared._output",
    "handlers.downloader": "handlers.shared.downloader",
    "handlers.region_resolver": "handlers.shared.region_resolver",
    # amenities
    "handlers.amenity_extractor": "handlers.amenities.amenity_extractor",
    "handlers.amenity_handlers": "handlers.amenities.amenity_handlers",
    "handlers.airquality_handlers": "handlers.amenities.airquality_handlers",
    # boundaries
    "handlers.boundary_extractor": "handlers.boundaries.boundary_extractor",
    "handlers.boundary_handlers": "handlers.boundaries.boundary_handlers",
    # buildings
    "handlers.building_extractor": "handlers.buildings.building_extractor",
    "handlers.building_handlers": "handlers.buildings.building_handlers",
    # cache
    "handlers.cache_handlers": "handlers.cache.cache_handlers",
    "handlers.region_handlers": "handlers.cache.region_handlers",
    # downloads
    "handlers.operations_handlers": "handlers.downloads.operations_handlers",
    "handlers.postgis_importer": "handlers.downloads.postgis_importer",
    "handlers.postgis_handlers": "handlers.downloads.postgis_handlers",
    "handlers.pgrouting_handlers": "handlers.downloads.pgrouting_handlers",
    "handlers.summary_handlers": "handlers.downloads.summary_handlers",
    # filters
    "handlers.filter_handlers": "handlers.filters.filter_handlers",
    "handlers.radius_filter": "handlers.filters.radius_filter",
    "handlers.osm_type_filter": "handlers.filters.osm_type_filter",
    "handlers.validation_handlers": "handlers.filters.validation_handlers",
    "handlers.osmose_verifier": "handlers.filters.osmose_verifier",
    "handlers.osmose_handlers": "handlers.filters.osmose_handlers",
    # graphhopper
    "handlers.graphhopper_handlers": "handlers.graphhopper.graphhopper_handlers",
    # parks
    "handlers.park_extractor": "handlers.parks.park_extractor",
    "handlers.park_handlers": "handlers.parks.park_handlers",
    # poi
    "handlers.poi_handlers": "handlers.poi.poi_handlers",
    # population
    "handlers.population_filter": "handlers.population.population_filter",
    "handlers.population_handlers": "handlers.population.population_handlers",
    # roads
    "handlers.road_extractor": "handlers.roads.road_extractor",
    "handlers.road_handlers": "handlers.roads.road_handlers",
    "handlers.zoom_graph": "handlers.roads.zoom_graph",
    "handlers.zoom_sbs": "handlers.roads.zoom_sbs",
    "handlers.zoom_detection": "handlers.roads.zoom_detection",
    "handlers.zoom_selection": "handlers.roads.zoom_selection",
    "handlers.zoom_builder": "handlers.roads.zoom_builder",
    "handlers.zoom_handlers": "handlers.roads.zoom_handlers",
    # routes
    "handlers.route_extractor": "handlers.routes.route_extractor",
    "handlers.route_handlers": "handlers.routes.route_handlers",
    "handlers.elevation_handlers": "handlers.routes.elevation_handlers",
    "handlers.routing_handlers": "handlers.routes.routing_handlers",
    "handlers.gtfs_extractor": "handlers.routes.gtfs_extractor",
    "handlers.gtfs_handlers": "handlers.routes.gtfs_handlers",
    # visualization
    "handlers.visualization_handlers": "handlers.visualization.visualization_handlers",
    "handlers.map_renderer": "handlers.visualization.map_renderer",
    # voting
    "handlers.tiger_downloader": "handlers.voting.tiger_downloader",
    "handlers.tiger_handlers": "handlers.voting.tiger_handlers",
    # sources
    "handlers.source_handlers": "handlers.sources.source_handlers",
    "handlers.pbf_source": "handlers.sources.pbf_source",
    "handlers.postgis_source": "handlers.sources.postgis_source",
    "handlers.geojson_source": "handlers.sources.geojson_source",
    # routing adapters
    "handlers.routing_adapter_handlers": "handlers.routing.routing_adapter_handlers",
    "handlers.api_router": "handlers.routing.api_router",
    "handlers.osrm_router": "handlers.routing.osrm_router",
}


class _AliasImporter:
    """Lazy sys.modules aliasing — imports on first access only.

    Uses the modern importlib.abc.MetaPathFinder protocol (find_spec)
    for compatibility with Python 3.12+.
    """

    def find_spec(self, fullname, path, target=None):
        if fullname not in _MODULE_MAP:
            return None
        # Only redirect when the active 'handlers' package is from osm-geocoder
        # (avoids intercepting genomics, jenkins, etc. handler imports).
        handlers_mod = sys.modules.get("handlers")
        if handlers_mod is None:
            return None
        handlers_file = os.path.realpath(getattr(handlers_mod, "__file__", "") or "")
        if "osm-geocoder" not in handlers_file:
            return None
        return importlib.util.spec_from_loader(fullname, loader=self)

    def create_module(self, spec):
        return None  # use default module creation

    def exec_module(self, module):
        real_name = _MODULE_MAP[module.__name__]
        real_mod = importlib.import_module(real_name)
        # Replace the module in sys.modules with the real one
        sys.modules[module.__name__] = real_mod
        # Copy attributes so the module object behaves like the real one
        module.__dict__.update(real_mod.__dict__)
        module.__file__ = getattr(real_mod, "__file__", None)
        module.__path__ = getattr(real_mod, "__path__", [])
        module.__loader__ = getattr(real_mod, "__loader__", None)


sys.meta_path.insert(0, _AliasImporter())

# ---------------------------------------------------------------------------
# Public imports (using new paths)
# ---------------------------------------------------------------------------

from .amenities.airquality_handlers import register_airquality_handlers
from .amenities.amenity_handlers import register_amenity_handlers
from .boundaries.boundary_handlers import register_boundary_handlers
from .buildings.building_handlers import register_building_handlers
from .cache.region_handlers import register_region_handlers
from .combined.combined_handlers import register_combined_handlers
from .db.import_handlers import register_import_handlers
from .downloads.operations_handlers import register_operations_handlers
from .downloads.pgrouting_handlers import register_pgrouting_handlers
from .downloads.postgis_handlers import register_postgis_handlers
from .downloads.summary_handlers import register_summary_handlers
from .filters.filter_handlers import register_filter_handlers
from .filters.osmose_handlers import register_osmose_handlers
from .filters.validation_handlers import register_validation_handlers
from .graphhopper.graphhopper_handlers import register_graphhopper_handlers
from .parks.park_handlers import register_park_handlers
from .poi.poi_handlers import register_poi_handlers
from .population.population_handlers import register_population_handlers
from .roads.road_handlers import register_road_handlers
from .roads.zoom_handlers import register_zoom_handlers
from .routes.elevation_handlers import register_elevation_handlers
from .routes.gtfs_handlers import register_gtfs_handlers
from .routes.route_handlers import register_route_handlers
from .routes.routing_handlers import register_routing_handlers
from .shared.pbf_cache import download_region  # noqa: F401
from .routing.routing_adapter_handlers import register_routing_adapter_handlers
from .sources.source_handlers import register_source_handlers
from .visualization.visualization_handlers import register_visualization_handlers
from .voting.tiger_handlers import register_tiger_handlers

__all__ = [
    "register_all_handlers",
    "register_all_registry_handlers",
    "register_combined_handlers",
    "register_import_handlers",
    "register_airquality_handlers",
    "register_amenity_handlers",
    "register_boundary_handlers",
    "register_building_handlers",
    "register_elevation_handlers",
    "register_filter_handlers",
    "register_graphhopper_handlers",
    "register_gtfs_handlers",
    "register_operations_handlers",
    "register_osmose_handlers",
    "register_park_handlers",
    "register_pgrouting_handlers",
    "register_postgis_handlers",
    "register_summary_handlers",
    "register_poi_handlers",
    "register_population_handlers",
    "register_region_handlers",
    "register_road_handlers",
    "register_route_handlers",
    "register_routing_handlers",
    "register_tiger_handlers",
    "register_validation_handlers",
    "register_visualization_handlers",
    "register_zoom_handlers",
    "register_routing_adapter_handlers",
    "register_source_handlers",
    "download_region",
]


def register_all_handlers(poller) -> None:
    """Register all event facet handlers with the given poller."""
    register_airquality_handlers(poller)
    register_amenity_handlers(poller)
    register_boundary_handlers(poller)
    register_building_handlers(poller)
    register_elevation_handlers(poller)
    register_filter_handlers(poller)
    register_graphhopper_handlers(poller)
    register_gtfs_handlers(poller)
    register_operations_handlers(poller)
    register_osmose_handlers(poller)
    register_park_handlers(poller)
    register_pgrouting_handlers(poller)
    register_postgis_handlers(poller)
    register_summary_handlers(poller)
    register_poi_handlers(poller)
    register_population_handlers(poller)
    register_region_handlers(poller)
    register_road_handlers(poller)
    register_route_handlers(poller)
    register_routing_handlers(poller)
    register_tiger_handlers(poller)
    register_validation_handlers(poller)
    register_visualization_handlers(poller)
    register_zoom_handlers(poller)
    register_combined_handlers(poller)
    register_import_handlers(poller)
    register_source_handlers(poller)
    register_routing_adapter_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all event facet handlers with a RegistryRunner."""
    from .amenities.airquality_handlers import register_handlers as reg_airquality
    from .amenities.amenity_handlers import register_handlers as reg_amenity
    from .boundaries.boundary_handlers import register_handlers as reg_boundary
    from .buildings.building_handlers import register_handlers as reg_building
    from .cache.region_handlers import register_handlers as reg_region
    from .downloads.operations_handlers import register_handlers as reg_operations
    from .downloads.pgrouting_handlers import register_handlers as reg_pgrouting
    from .downloads.postgis_handlers import register_handlers as reg_postgis
    from .downloads.summary_handlers import register_handlers as reg_summary
    from .filters.filter_handlers import register_handlers as reg_filter
    from .filters.osmose_handlers import register_handlers as reg_osmose
    from .filters.validation_handlers import register_handlers as reg_validation
    from .graphhopper.graphhopper_handlers import register_handlers as reg_graphhopper
    from .parks.park_handlers import register_handlers as reg_park
    from .poi.poi_handlers import register_handlers as reg_poi
    from .population.population_handlers import register_handlers as reg_population
    from .roads.road_handlers import register_handlers as reg_road
    from .roads.zoom_handlers import register_handlers as reg_zoom
    from .routes.elevation_handlers import register_handlers as reg_elevation
    from .routes.gtfs_handlers import register_handlers as reg_gtfs
    from .routes.route_handlers import register_handlers as reg_route
    from .routes.routing_handlers import register_handlers as reg_routing
    from .visualization.visualization_handlers import register_handlers as reg_visualization
    from .voting.tiger_handlers import register_handlers as reg_tiger

    reg_airquality(runner)
    reg_amenity(runner)
    reg_boundary(runner)
    reg_building(runner)
    reg_elevation(runner)
    reg_filter(runner)
    reg_graphhopper(runner)
    reg_gtfs(runner)
    reg_operations(runner)
    reg_osmose(runner)
    reg_park(runner)
    reg_pgrouting(runner)
    reg_postgis(runner)
    reg_summary(runner)
    reg_poi(runner)
    reg_population(runner)
    reg_region(runner)
    reg_road(runner)
    reg_route(runner)
    reg_routing(runner)
    reg_tiger(runner)
    reg_validation(runner)
    reg_visualization(runner)
    reg_zoom(runner)

    from .combined.combined_handlers import register_handlers as reg_combined
    from .db.import_handlers import register_handlers as reg_db_import
    from .routing.routing_adapter_handlers import register_handlers as reg_routing_adapter
    from .sources.source_handlers import register_handlers as reg_source

    reg_combined(runner)
    reg_db_import(runner)
    reg_source(runner)
    reg_routing_adapter(runner)
