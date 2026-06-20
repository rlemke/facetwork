"""Handler registration for example-template."""

from __future__ import annotations


def register_all_registry_handlers(runner) -> None:
    """Register every handler in this example with the RegistryRunner.

    Imports are deferred so concurrent module loads from the runner do not
    deadlock on the import lock.
    """
    from .greeter_handlers import register_handlers as reg_greeter

    reg_greeter(runner)
