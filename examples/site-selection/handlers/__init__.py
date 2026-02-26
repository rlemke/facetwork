"""Site-selection handlers package.

Provides registration functions for all site-selection event facet handlers,
supporting both AgentPoller and RegistryRunner execution models.
"""

from .downloads.download_handlers import register_download_handlers
from .extract.extract_handlers import register_extract_handlers
from .scoring.scoring_handlers import register_scoring_handlers
from .output.output_handlers import register_output_handlers

__all__ = [
    "register_all_handlers",
    "register_all_registry_handlers",
    "register_download_handlers",
    "register_extract_handlers",
    "register_scoring_handlers",
    "register_output_handlers",
]


def register_all_handlers(poller) -> None:
    """Register all event facet handlers with the given poller."""
    register_download_handlers(poller)
    register_extract_handlers(poller)
    register_scoring_handlers(poller)
    register_output_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all facet handlers with a RegistryRunner."""
    from .downloads.download_handlers import register_handlers as reg_downloads
    from .extract.extract_handlers import register_handlers as reg_extract
    from .scoring.scoring_handlers import register_handlers as reg_scoring
    from .output.output_handlers import register_handlers as reg_output

    reg_downloads(runner)
    reg_extract(runner)
    reg_scoring(runner)
    reg_output(runner)
