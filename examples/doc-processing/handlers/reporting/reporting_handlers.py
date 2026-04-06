"""Reporting handlers for the doc-processing example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.doc_utils import generate_report

NAMESPACE = "doc.Reporting"


def handle_generate_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateReport event facet."""
    file_path = params.get("file_path", "")
    file_type = params.get("file_type", "txt")
    category = params.get("category", "other")
    summary = params.get("summary", "")
    key_phrases = params.get("key_phrases", [])
    chunk_count = params.get("chunk_count", 0)
    word_count = params.get("word_count", 0)

    if isinstance(key_phrases, str):
        key_phrases = json.loads(key_phrases)
    if isinstance(chunk_count, str):
        chunk_count = int(chunk_count)
    if isinstance(word_count, str):
        word_count = int(word_count)

    report_path, report = generate_report(
        file_path, file_type, category, summary, key_phrases, chunk_count, word_count
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Generated report: {report_path}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"report": report}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateReport": handle_generate_report,
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
