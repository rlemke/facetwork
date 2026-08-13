# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Handlers that ship with Facetwork itself.

Domain handlers live in their own ``fwh_*`` packages and are registered by
``fw install domain``. These are different: they are part of the framework, so a
workflow can use them without installing anything or writing a handler.

Registration is idempotent and driven from the FFL that declares the facets, so
the two cannot drift — a facet added to ``facetwork/ffl/file.ffl`` without a
handler (or the reverse) is a registration-time error rather than a runtime
"no handler for facet" hours later.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Built-in facet modules: the handler module, and the FFL that declares its
# facets. Both are listed so registration can CHECK them against each other.
_BUILTIN_MODULES: list[tuple[str, str]] = [
    ("facetwork.handlers.file_handlers", "file.ffl"),
    ("facetwork.handlers.http_handlers", "http.ffl"),
    ("facetwork.handlers.archive_handlers", "archive.ffl"),
]

# Generous relative to the work: a Copy or Hash of a large artifact is legitimate
# and must not be cut off by a default sized for a quick computation.
_BUILTIN_TIMEOUT_MS = 600_000


def _ffl_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ffl", filename)


def builtin_facets() -> dict[str, str]:
    """Map of facet name -> handler module URI, for every built-in facet."""
    import importlib

    out: dict[str, str] = {}
    for module_name, _ffl in _BUILTIN_MODULES:
        module = importlib.import_module(module_name)
        uri = f"file://{os.path.abspath(module.__file__)}"
        for facet in module.facet_names():
            out[facet] = uri
    return out


def declared_facets() -> set[str]:
    """Facet names declared in the shipped FFL.

    Parsed rather than hardcoded: the FFL is the contract, and a list repeated
    here would be one more thing to forget to update.
    """
    from facetwork import parse

    names: set[str] = set()
    for _module_name, ffl_name in _BUILTIN_MODULES:
        path = _ffl_path(ffl_name)
        if not os.path.isfile(path):
            continue
        with open(path) as fh:
            program = parse(fh.read())
        for ns in getattr(program, "namespaces", []) or []:
            prefix = f"{ns.name}." if getattr(ns, "name", "") else ""
            for decl in getattr(ns, "event_facets", None) or []:
                sig = getattr(decl, "sig", None)
                if sig is not None and getattr(sig, "name", ""):
                    names.add(f"{prefix}{sig.name}")
    return names


def register_builtin_handlers(persistence: Any) -> int:
    """Register the framework's own handlers. Idempotent.

    Called at runner startup, so every runner can serve these regardless of
    which domains it has installed. Returns the number registered.

    Raises when the FFL and the handlers disagree. That check is the point: the
    failure it prevents is a facet that compiles, dispatches, and then dies on a
    runner with "no handler", far from the change that caused it.
    """
    import time

    from facetwork.runtime.entities import HandlerRegistration

    handlers = builtin_facets()
    declared = declared_facets()
    if declared:
        missing_handler = declared - set(handlers)
        missing_decl = set(handlers) - declared
        if missing_handler or missing_decl:
            raise RuntimeError(
                "built-in facets and handlers disagree — "
                f"declared without a handler: {sorted(missing_handler)}; "
                f"handled but not declared: {sorted(missing_decl)}"
            )

    now = str(int(time.time() * 1000))
    count = 0
    for facet_name, module_uri in sorted(handlers.items()):
        persistence.save_handler_registration(
            HandlerRegistration(
                facet_name=facet_name,
                module_uri=module_uri,
                entrypoint="handle",
                version="1.0.0",
                checksum="",
                timeout_ms=_BUILTIN_TIMEOUT_MS,
                requirements=[],
                metadata={"builtin": "true"},
                created=now,
                updated=now,
            )
        )
        count += 1
    logger.info("Registered %d built-in handler(s)", count)
    return count
