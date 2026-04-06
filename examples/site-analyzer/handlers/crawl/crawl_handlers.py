"""Crawl handlers for the site-analyzer example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.site_utils import fetch_page, prepare_crawl

NAMESPACE = "site.Crawl"


def handle_prepare_crawl(params: dict[str, Any]) -> dict[str, Any]:
    """Handle PrepareCrawl event facet."""
    raw = params.get("urls", [])
    if isinstance(raw, list):
        urls = raw
    elif isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                urls = json.loads(raw)
            except json.JSONDecodeError:
                urls = [raw]
        elif "," in raw:
            urls = [u.strip() for u in raw.split(",") if u.strip()]
        elif raw:
            urls = [raw]
        else:
            urls = []
    else:
        urls = []

    plan = prepare_crawl(urls)
    # Serialize urls list to JSON for AFL Json type
    plan_out = {**plan, "urls": json.dumps(plan["urls"])}

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Prepared crawl plan: {plan['page_count']} pages on {plan['base_domain']}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"plan": plan_out}


def handle_fetch_page(params: dict[str, Any]) -> dict[str, Any]:
    """Handle FetchPage event facet."""
    url = params.get("url", "")
    site_id = params.get("site_id", "")

    page = fetch_page(url, site_id)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Fetched {url}: {page['status_code']} ({page['fetch_time_ms']}ms)"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"page": page}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.PrepareCrawl": handle_prepare_crawl,
    f"{NAMESPACE}.FetchPage": handle_fetch_page,
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


def register_crawl_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
