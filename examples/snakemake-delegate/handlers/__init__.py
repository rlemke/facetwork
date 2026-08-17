"""Snakemake delegation handlers — registration aggregator.

`fw runner start --example snakemake-delegate` looks for these two names, so an
example whose handlers live in a submodule has to re-export them here or it
registers nothing at all (and the runner then advertises no facets rather than
failing loudly).
"""

__all__ = [
    "register_all_handlers",
    "register_all_registry_handlers",
    "register_snakemake_handlers",
]


def register_all_handlers(poller) -> None:
    """Register every facet with an AgentPoller."""
    from .snakemake_handlers import register_snakemake_handlers as reg

    reg(poller)


def register_all_registry_handlers(runner) -> None:
    """Register every facet with a RegistryRunner."""
    from .snakemake_handlers import register_handlers as reg

    reg(runner)


def register_snakemake_handlers(poller) -> None:
    """Back-compat alias for the poller registration."""
    register_all_handlers(poller)
