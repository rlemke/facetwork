"""Handler registration for the sentinel2-landchange example.

Imports are deferred to function bodies to avoid import-lock contention when a
RegistryRunner imports handler modules from separate threads.
"""

from __future__ import annotations


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    from .analyze.analyze_handlers import register_analyze_handlers
    from .render.render_handlers import register_render_handlers
    from .source.source_handlers import register_source_handlers

    register_source_handlers(poller)
    register_analyze_handlers(poller)
    register_render_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner (the in-repo discovery hook)."""
    from .analyze.analyze_handlers import register_handlers as reg_analyze
    from .render.render_handlers import register_handlers as reg_render
    from .source.source_handlers import register_handlers as reg_source

    reg_source(runner)
    reg_analyze(runner)
    reg_render(runner)
