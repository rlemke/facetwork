"""Broken link detection handlers for the site-analyzer example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.site_utils import detect_broken_links

NAMESPACE = "site.Links"


def handle_detect_broken_links(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DetectBrokenLinks event facet."""
    url = params.get("url", "")
    html = params.get("html", "")
    site_id = params.get("site_id", "")

    link_report = detect_broken_links(url, html, site_id)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = (
            f"Scanned {url}: {link_report['total_links']} links "
            f"({link_report['internal_links']} internal, "
            f"{link_report['external_links']} external, "
            f"{link_report['broken_count']} broken)"
        )
        level = "warning" if link_report["broken_count"] > 0 else "success"
        if callable(step_log):
            step_log(msg, level)
        else:
            step_log.append({"message": msg, "level": level})

    return {"link_report": link_report}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DetectBrokenLinks": handle_detect_broken_links,
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


def register_links_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
