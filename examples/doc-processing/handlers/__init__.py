"""Handler registration for the doc-processing example."""

from __future__ import annotations

from .chunking.chunking_handlers import register_chunking_handlers
from .classification.classification_handlers import register_classification_handlers
from .detection.detection_handlers import register_detection_handlers
from .extraction.extraction_handlers import register_extraction_handlers
from .reporting.reporting_handlers import register_reporting_handlers
from .summarization.summarization_handlers import register_summarization_handlers


def register_all_handlers(poller) -> None:
    """Register all handlers with an AgentPoller."""
    register_detection_handlers(poller)
    register_extraction_handlers(poller)
    register_chunking_handlers(poller)
    register_summarization_handlers(poller)
    register_classification_handlers(poller)
    register_reporting_handlers(poller)


def register_all_registry_handlers(runner) -> None:
    """Register all handlers with a RegistryRunner."""
    from .chunking.chunking_handlers import register_handlers as reg_chunking
    from .classification.classification_handlers import register_handlers as reg_classification
    from .detection.detection_handlers import register_handlers as reg_detection
    from .extraction.extraction_handlers import register_handlers as reg_extraction
    from .reporting.reporting_handlers import register_handlers as reg_reporting
    from .summarization.summarization_handlers import register_handlers as reg_summarization

    reg_detection(runner)
    reg_extraction(runner)
    reg_chunking(runner)
    reg_summarization(runner)
    reg_classification(runner)
    reg_reporting(runner)
