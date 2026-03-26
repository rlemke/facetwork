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

"""FastAPI application factory for the AgentFlow Dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .filters import register_filters

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


def create_app(config_path: str | None = None) -> FastAPI:
    """Build and return the configured FastAPI application.

    Args:
        config_path: Optional path to an AFL config file.
    """

    async def _reaper_loop() -> None:
        """Periodically reap orphaned and stuck tasks.

        Runs independently of runners so stale tasks are cleaned up even
        when all runners are at capacity or offline.
        """
        interval = int(os.environ.get("AFL_DASHBOARD_REAP_INTERVAL_S", "60"))
        reaper_timeout = int(os.environ.get("AFL_REAPER_TIMEOUT_MS", "300000"))
        stuck_timeout = int(os.environ.get("AFL_STUCK_TIMEOUT_MS", "14400000"))

        # Delay import to avoid circular deps / missing optional packages
        await asyncio.sleep(5)
        try:
            from .dependencies import _get_store

            store = _get_store(config_path)
        except Exception:
            logger.debug("Dashboard reaper: could not get store", exc_info=True)
            return

        while True:
            try:
                await asyncio.sleep(interval)
                reaped = store.reap_orphaned_tasks(down_timeout_ms=reaper_timeout)
                if reaped:
                    logger.warning(
                        "Dashboard reaper: reset %d orphaned task(s)", len(reaped)
                    )
                stuck = store.reap_stuck_tasks(default_stuck_ms=stuck_timeout)
                if stuck:
                    logger.warning(
                        "Dashboard reaper: reset %d stuck task(s)", len(stuck)
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("Dashboard reaper cycle failed", exc_info=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Store config path for dependency injection
        app.state.config_path = config_path
        # Start background reaper so stale tasks are cleaned up even
        # when runners are stuck or offline.
        reaper_task = asyncio.create_task(_reaper_loop())
        yield
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="AgentFlow Dashboard",
        description="Monitoring UI for AgentFlow workflows",
        lifespan=lifespan,
    )

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    register_filters(templates.env)
    app.state.templates = templates

    # Register routes
    from .routes import register_routes

    register_routes(app)

    return app
