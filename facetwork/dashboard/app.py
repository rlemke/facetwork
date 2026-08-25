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

"""FastAPI application factory for the Facetwork Dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
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
        config_path: Optional path to an FFL config file.
    """

    async def _reaper_loop() -> None:
        """Periodically reap orphaned and stuck tasks — in ONE instance at a time.

        Runs independently of runners so stale tasks are cleaned up even
        when all runners are at capacity or offline.

        The dashboard is stateless and several instances can serve at once (two
        do here), but this loop WRITES: it resets orphaned and stuck tasks. One
        copy per instance means N dashboards reap N times as often and enter the
        reap-vs-reclaim race N times as often.

        So instances contend for a short Mongo lease and only the holder reaps.
        A lease, not a config flag naming one host: a flag leaves nobody reaping
        whenever that host is down, and the whole point of this loop is to be the
        thing that still works when other things are not. `FW_DASHBOARD_REAPER=0`
        opts an instance out entirely.
        """
        interval = int(os.environ.get("FW_DASHBOARD_REAP_INTERVAL_S", "60"))
        reaper_timeout = int(os.environ.get("FW_REAPER_TIMEOUT_MS", "300000"))
        stuck_timeout = int(os.environ.get("FW_STUCK_TIMEOUT_MS", "14400000"))

        if os.environ.get("FW_DASHBOARD_REAPER", "1").strip().lower() in ("0", "false", "no"):
            logger.info("Dashboard reaper: disabled on this instance (FW_DASHBOARD_REAPER)")
            return

        # Identify the holder in a way a human can act on when it appears in a
        # log line — a bare uuid tells you nothing about which box to go look at.
        holder = f"{socket.gethostname()}:{os.getpid()}"
        # Outlive a cycle so a slow tick does not hand the lease away mid-reap,
        # but expire soon enough that a dead instance is replaced promptly.
        lease_ttl_ms = interval * 3 * 1000
        # None, not False: the FIRST decision must always be logged. Seeded with
        # False, an instance that stands down on its first cycle logs nothing at
        # all, and a silent standby is indistinguishable from a loop that died
        # or never started — which is the whole thing you go to the log to rule
        # out.
        was_holder: bool | None = None

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
                # Re-acquire every cycle: this both renews our own lease and
                # picks it up if the previous holder died.
                holding = store.try_acquire_lease(
                    "dashboard-reaper", holder, lease_ttl_ms)
                if holding != was_holder:
                    logger.info(
                        "Dashboard reaper: %s (holder=%s)",
                        "now reaping" if holding else "standing down, another instance holds the lease",
                        holder)
                    was_holder = holding
                if not holding:
                    continue
                reaped = store.reap_orphaned_tasks(down_timeout_ms=reaper_timeout)
                if reaped:
                    logger.warning("Dashboard reaper: reset %d orphaned task(s)", len(reaped))
                stuck = store.reap_stuck_tasks(default_stuck_ms=stuck_timeout)
                if stuck:
                    logger.warning("Dashboard reaper: reset %d stuck task(s)", len(stuck))
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
        title="Facetwork Dashboard",
        description="Monitoring UI for Facetwork workflows",
        lifespan=lifespan,
    )

    # Opt-in shared-token auth on mutating requests (no-op unless
    # FW_DASHBOARD_TOKEN is set). Gates the destructive + handler-registration
    # surface without breaking read-only monitoring.
    from .auth import DashboardAuthMiddleware

    app.add_middleware(DashboardAuthMiddleware)

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    register_filters(templates.env)
    templates.env.globals["now_ms"] = lambda: int(time.time() * 1000)
    app.state.templates = templates

    # Register routes
    from .routes import register_routes

    register_routes(app)

    return app
