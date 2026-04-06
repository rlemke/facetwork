"""Reporting handlers for the site-analyzer example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.site_utils import generate_site_report

NAMESPACE = "site.Reporting"


def handle_generate_site_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateSiteReport event facet."""
    site_id = params.get("site_id", "")
    page_count = params.get("page_count", 0)
    if isinstance(page_count, str):
        page_count = int(page_count)

    report_path, report = generate_site_report(site_id, page_count)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = (
            f"Generated site report: {report['page_count']} pages, "
            f"{report['total_broken_links']} broken links → {report_path}"
        )
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"report": report}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateSiteReport": handle_generate_site_report,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint."""
    facet = payload["_facet_name"]
    handler = _DISPATCH[facet]
    return handler(payload)


def register_handlers(runner) -> None:
    """Register with RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_reporting_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
