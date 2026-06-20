"""Trivial example handler — prints a greeting and returns it.

Replace this with the real handlers for your example.
"""

from __future__ import annotations


def greet(name: str) -> dict[str, str]:
    return {"message": f"Hello, {name}!"}


def register_handlers(runner) -> None:
    runner.register_handler(
        facet_name="example_template.Greet",
        module_uri="example_template.handlers.greeter_handlers",
        entrypoint="greet",
    )
