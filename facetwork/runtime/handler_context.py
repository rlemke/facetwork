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

        # Long-running stage with its own timeout budget
        with ctx.stage("pbf_scan", timeout_ms=30 * 60_000) as s:
            scan_pbf(..., on_progress=s.heartbeat)
            # May extend mid-stage if input is larger than expected
            s.extend(15 * 60_000)

        return {"result": ...}
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


@dataclass
class StageHandle:
    """Handle yielded by :meth:`HandlerContext.stage` for mid-stage control.

    Callers can extend the stage budget when they discover the input is
    larger than initially estimated, or emit heartbeats with progress.
    """

    ctx: "HandlerContext"
    name: str
    timeout_ms: int
    started_ms: float = field(default_factory=lambda: time.monotonic() * 1000.0)

    def extend(self, extra_ms: int) -> None:
        """Grow this stage's budget by ``extra_ms`` from now."""
        if extra_ms <= 0:
            return
        remaining = self._elapsed_buffer_ms()
        self.timeout_ms += int(extra_ms)
        self.ctx._set_stage_budget(remaining + int(extra_ms), self.name)
        self.ctx.step_log(
            f"↻ stage {self.name}: +{extra_ms/1000:.0f}s extension",
        )

    def heartbeat(
        self,
        progress_pct: int | None = None,
        progress_message: str | None = None,
    ) -> None:
        """Emit a heartbeat scoped to this stage."""
        msg = progress_message
        if msg is not None:
            msg = f"{self.name}: {msg}"
        self.ctx.heartbeat(progress_pct=progress_pct, progress_message=msg)

    def _elapsed_buffer_ms(self) -> int:
        """Remaining budget from the original call, in ms."""
        elapsed = int(time.monotonic() * 1000.0 - self.started_ms)
        return max(0, self.timeout_ms - elapsed)


def _noop_stage_budget(timeout_ms: int, stage_name: str = "") -> None:
    return None


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
    _set_stage_budget: Callable[[int, str], None] = field(
        default=_noop_stage_budget, repr=False
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        timeout_ms: int,
        progress: str | None = None,
    ) -> Iterator[StageHandle]:
        """Declare a long-running stage with its own timeout budget.

        While the context is active, the runner watchdog will not reap this
        task for inactivity as long as ``timeout_ms`` has not elapsed — even
        if the global execution timeout would otherwise apply. Renews the
        lease to cover the budget so other runners won't reclaim the task.

        Args:
            name: Short, human-readable stage name (e.g. ``"pbf_scan"``).
            timeout_ms: Budget for this stage, in milliseconds.
            progress: Optional initial progress message.
        """
        self._set_stage_budget(int(timeout_ms), name)
        msg = f"{name}: starting" if progress is None else f"{name}: {progress}"
        self.heartbeat(progress_message=msg)
        self.step_log(f"→ stage {name} (budget {timeout_ms/1000:.0f}s)")
        handle = StageHandle(ctx=self, name=name, timeout_ms=int(timeout_ms))
        t0 = time.monotonic()
        try:
            yield handle
        finally:
            elapsed = time.monotonic() - t0
            self.step_log(f"← stage {name} ({elapsed:.1f}s)")
            # Clear the budget so subsequent idle time is subject to the
            # normal global timeout again.
            self._set_stage_budget(0, "")

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
            _set_stage_budget=payload.get("_set_stage_budget", _noop_stage_budget),
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
            "_set_stage_budget": self._set_stage_budget,
            "_handler_metadata": self.metadata,
            "_ctx": self,
        }
