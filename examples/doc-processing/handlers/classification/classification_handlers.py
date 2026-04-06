"""Classification handlers for the doc-processing example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.doc_utils import classify_document

NAMESPACE = "doc.Classification"


def handle_classify_document(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ClassifyDocument event facet."""
    text = params.get("text", "")
    file_path = params.get("file_path", "")

    classification = classify_document(text, file_path)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Classified as {classification['category']} (confidence={classification['confidence']})"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"classification": classification}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ClassifyDocument": handle_classify_document,
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


def register_classification_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
