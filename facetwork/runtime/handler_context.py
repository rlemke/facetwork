"""Typed handler context injected into every handler payload.

Provides a structured, discoverable interface to the runtime metadata
that runners inject alongside step parameters. Handlers can use either
the typed ``HandlerContext`` (via ``payload["_ctx"]``) or the flat
``payload["_step_log"]`` / ``payload["_task_heartbeat"]`` keys.

Example usage::

    def handle(payload: dict) -> dict:
        ctx = HandlerContext.from_payload(payload)
        ctx.heartbeat(progress_message="Starting work")

        if ctx.is_retry:
            ctx.step_log(f"Retry #{ctx.retry_count}: checking prior work")

        # ... do work ...
        return {"result": ...}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HandlerContext:
    """Typed context injected by the runner into every handler dispatch.

    Attributes:
        facet_name: Qualified event facet name (e.g. ``"osm.ops.PostGisImport"``).
        task_uuid: Unique task identifier.
        retry_count: Number of prior attempts (0 on first execution).
        is_retry: ``True`` when reclaiming a previously-attempted task.
        step_log: Callback to emit a step log visible in the dashboard.
            Signature: ``(message: str, level: str = "info", details: dict | None = None) -> None``
        heartbeat: Callback to signal liveness and avoid timeout reaping.
            Signature: ``(progress_pct: int | None = None, progress_message: str | None = None) -> None``
        metadata: Handler registration metadata dict (from ``HandlerRegistration.metadata``).
    """

    facet_name: str = ""
    task_uuid: str = ""
    retry_count: int = 0
    is_retry: bool = False
    step_log: Callable[..., None] = field(default=lambda *a, **kw: None, repr=False)
    heartbeat: Callable[..., None] = field(default=lambda *a, **kw: None, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict) -> HandlerContext:
        """Extract a HandlerContext from a handler payload dict.

        Works with both the typed ``_ctx`` key and the flat injected keys,
        preferring ``_ctx`` if present.
        """
        ctx = payload.get("_ctx")
        if isinstance(ctx, HandlerContext):
            return ctx
        return cls(
            facet_name=payload.get("_facet_name", ""),
            task_uuid=payload.get("_task_uuid", ""),
            retry_count=payload.get("_retry_count", 0),
            is_retry=payload.get("_is_retry", False),
            step_log=payload.get("_step_log", lambda *a, **kw: None),
            heartbeat=payload.get("_task_heartbeat", lambda *a, **kw: None),
            metadata=payload.get("_handler_metadata", {}),
        )

    def to_payload_keys(self) -> dict[str, Any]:
        """Return the flat payload keys for backward compatibility."""
        return {
            "_facet_name": self.facet_name,
            "_task_uuid": self.task_uuid,
            "_retry_count": self.retry_count,
            "_is_retry": self.is_retry,
            "_step_log": self.step_log,
            "_task_heartbeat": self.heartbeat,
            "_handler_metadata": self.metadata,
            "_ctx": self,
        }
