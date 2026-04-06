"""Metadata extraction handlers for the site-analyzer example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.site_utils import extract_metadata

NAMESPACE = "site.Metadata"


def handle_extract_metadata(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ExtractMetadata event facet."""
    url = params.get("url", "")
    html = params.get("html", "")
    site_id = params.get("site_id", "")

    meta = extract_metadata(url, html, site_id)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f'Extracted metadata for {url}: "{meta["title"]}" ({meta["word_count"]} words)'
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"meta": meta}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ExtractMetadata": handle_extract_metadata,
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


def register_metadata_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
