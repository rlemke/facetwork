"""Standalone Facetwork domain — registered via the ``facetwork.domains``
entry point. Copy this directory to a new repo, rename ``domain_template``
to your domain's import name, and adjust ``pyproject.toml`` accordingly.
"""

from __future__ import annotations

from pathlib import Path

from facetwork.domains import DomainPackage

from .handlers import register_all_registry_handlers

domain = DomainPackage(
    name="domain-template",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
)
