"""MongoDB ingestion handlers for census data."""

from .ingestion_handlers import register_handlers, register_ingestion_handlers

__all__ = ["register_handlers", "register_ingestion_handlers"]
