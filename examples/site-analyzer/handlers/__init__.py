"""Handler registration for the site-analyzer example."""

from __future__ import annotations

from .classification.classification_handlers import register_classification_handlers
from .crawl.crawl_handlers import register_crawl_handlers
from .links.links_handlers import register_links_handlers
from .metadata.metadata_handlers import register_metadata_handlers
from .reporting.reporting_handlers import register_reporting_handlers
from .summarization.summarization_handlers import register_summarization_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_crawl_handlers(poller)
    register_metadata_handlers(poller)
    register_classification_handlers(poller)
    register_summarization_handlers(poller)
    register_links_handlers(poller)
    register_reporting_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .classification.classification_handlers import register_handlers as reg_classification
    from .crawl.crawl_handlers import register_handlers as reg_crawl
    from .links.links_handlers import register_handlers as reg_links
    from .metadata.metadata_handlers import register_handlers as reg_metadata
    from .reporting.reporting_handlers import register_handlers as reg_reporting
    from .summarization.summarization_handlers import register_handlers as reg_summarization

    reg_crawl(runner)
    reg_metadata(runner)
    reg_classification(runner)
    reg_summarization(runner)
    reg_links(runner)
    reg_reporting(runner)
