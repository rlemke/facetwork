"""Extraction handlers for the doc-processing example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.doc_utils import extract_text

NAMESPACE = "doc.Extraction"


def handle_extract_text(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ExtractText event facet."""
    file_path = params.get("file_path", "")
    file_type = params.get("file_type", "txt")
    encoding = params.get("encoding", "utf-8")

    extracted = extract_text(file_path, file_type, encoding)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Extracted {extracted['word_count']} words via {extracted['method']}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"extracted": extracted}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ExtractText": handle_extract_text,
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


def register_extraction_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
