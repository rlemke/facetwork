"""Chunking handlers for the doc-processing example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.doc_utils import split_into_chunks

NAMESPACE = "doc.Chunking"


def handle_split_into_chunks(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SplitIntoChunks event facet."""
    text = params.get("text", "")
    chunk_size = params.get("chunk_size", 1000)
    overlap = params.get("overlap", 100)
    if isinstance(chunk_size, str):
        chunk_size = int(chunk_size)
    if isinstance(overlap, str):
        overlap = int(overlap)

    chunks = split_into_chunks(text, chunk_size, overlap)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Split text into {len(chunks)} chunks (size={chunk_size}, overlap={overlap})"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"chunks": chunks, "chunk_count": len(chunks)}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SplitIntoChunks": handle_split_into_chunks,
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


def register_chunking_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
