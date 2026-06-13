"""Facet capability index — the discovery layer for LLM-composed workflows.

Turns a compiled FFL program (the ``emit_dict`` / stored ``compiled_ast`` shape)
into a queryable catalogue of *facets*: for each, a one-line purpose (from its
doc comment), its typed parameters and returns, its namespace, and whether it is
an event facet. This is the facet-level analogue of the workflow catalog search —
it lets an LLM ask "what operates on a GeoJSON path?" or "what extracts
amenities?" and get back precise, composable primitives rather than guessing.

See ``docs/architecture/composable-facet-library.md`` §6.
"""

from .index import (
    FacetCapability,
    author_and_teams,
    index_program,
    index_programs,
    search,
)

__all__ = [
    "FacetCapability",
    "author_and_teams",
    "index_program",
    "index_programs",
    "search",
]
