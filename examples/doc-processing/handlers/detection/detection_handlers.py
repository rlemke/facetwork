"""Detection handlers for the doc-processing example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.doc_utils import detect_file_type

NAMESPACE = "doc.Detection"


def handle_detect_file_type(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DetectFileType event facet."""
    file_path = params.get("file_path", "")
    info = detect_file_type(file_path)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Detected {info['file_type']} file: {info['file_size']} bytes, {info['page_count']} pages"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"info": info}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DetectFileType": handle_detect_file_type,
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


def register_detection_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
