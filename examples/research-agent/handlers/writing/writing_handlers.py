"""Writing handlers — DraftReport, ReviewDraft."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.research_utils import draft_report, review_draft

NAMESPACE = "research.Writing"


def handle_draft_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DraftReport event facet."""
    topic = params.get("topic", {})
    if isinstance(topic, str):
        topic = json.loads(topic)
    analysis = params.get("analysis", {})
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    gaps = params.get("gaps", [])
    if isinstance(gaps, str):
        gaps = json.loads(gaps)

    draft = draft_report(topic, analysis, gaps)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Drafted report: {draft['title']} ({draft['word_count']} words)", "level": "success"})

    return {"draft": draft}


def handle_review_draft(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ReviewDraft event facet."""
    draft = params.get("draft", {})
    if isinstance(draft, str):
        draft = json.loads(draft)
    topic = params.get("topic", {})
    if isinstance(topic, str):
        topic = json.loads(topic)
    analysis = params.get("analysis", {})
    if isinstance(analysis, str):
        analysis = json.loads(analysis)

    review = review_draft(draft, topic, analysis)

    step_log = params.get("_step_log")
    if step_log:
        status = "approved" if review["approved"] else "needs revision"
        step_log.append({"message": f"Reviewed draft: score {review['score']}/100 ({status})", "level": "success"})

    return {"review": review}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DraftReport": handle_draft_report,
    f"{NAMESPACE}.ReviewDraft": handle_review_draft,
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


def register_writing_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
