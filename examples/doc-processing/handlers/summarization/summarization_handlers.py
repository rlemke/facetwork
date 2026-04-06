"""Summarization handlers for the doc-processing example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.doc_utils import (
    merge_summaries,
    save_chunk_summary,
    summarize_chunk,
)

NAMESPACE = "doc.Summarization"


def handle_summarize_chunk(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SummarizeChunk event facet."""
    chunk_id = params.get("chunk_id", "")
    text = params.get("text", "")
    file_path = params.get("file_path", "")

    summary = summarize_chunk(chunk_id, text)

    # Persist to summary store for later merging
    if file_path:
        save_chunk_summary(file_path, summary)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Summarized {chunk_id}: {len(summary['key_phrases'])} key phrases"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"summary": summary}


def handle_merge_summaries(params: dict[str, Any]) -> dict[str, Any]:
    """Handle MergeSummaries event facet."""
    file_path = params.get("file_path", "")
    chunk_count = params.get("chunk_count", 0)
    if isinstance(chunk_count, str):
        chunk_count = int(chunk_count)

    result = merge_summaries(file_path, chunk_count)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Merged {result['total_chunks']} summaries, {len(result['key_phrases'])} unique phrases"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return result


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SummarizeChunk": handle_summarize_chunk,
    f"{NAMESPACE}.MergeSummaries": handle_merge_summaries,
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


def register_summarization_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
