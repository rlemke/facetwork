# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Route registration for the Facetwork Dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    """Include all route modules."""
    from .core.api import router as api_router
    from .core.health import router as health_router
    from .core.home import router as home_router
    from .execution.events import router as events_router
    from .execution.flows import router as flows_router
    from .execution.runners import router as runners_router
    from .execution.steps import router as steps_router
    from .execution.tasks import router as tasks_router
    from .execution.workflows import router as workflows_router
    from .monitoring.handlers import router as handlers_router
    from .monitoring.logs import router as logs_router
    from .monitoring.namespaces import router as namespaces_router
    from .monitoring.output import router as output_router
    from .monitoring.servers import router as servers_router
    from .monitoring.sources import router as sources_router
    from .domain.census_maps import router as census_maps_router
    from .domain.climate_trends import router as climate_trends_router
    from .domain.site_selection import router as site_selection_router
    from .v2.dashboard_v2 import router as dashboard_v2_router

    app.include_router(health_router)
    app.include_router(home_router)
    app.include_router(runners_router)
    app.include_router(steps_router)
    app.include_router(flows_router)
    app.include_router(servers_router)
    app.include_router(handlers_router)
    app.include_router(logs_router)
    app.include_router(tasks_router)
    app.include_router(events_router)
    app.include_router(sources_router)
    app.include_router(namespaces_router)
    app.include_router(api_router)
    app.include_router(workflows_router)
    app.include_router(dashboard_v2_router)
    app.include_router(output_router)
    app.include_router(census_maps_router)
    app.include_router(site_selection_router)
    app.include_router(climate_trends_router)
