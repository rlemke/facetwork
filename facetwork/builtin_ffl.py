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

"""The FFL that ships with the framework itself (``facetwork/ffl/*.ffl``).

``fw.file`` and ``fw.http`` are not libraries an author installs or passes with
``--library``: they ship in the wheel and every runner registers their handlers
at startup. So the compiler has to know them the same way it knows the grammar —
otherwise ``use fw.file`` is ``USE_UNKNOWN_NAMESPACE`` for a namespace that is
always present, and the author is told to fix something that is not broken.

Parsed once and cached; these files change only when the framework does.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from facetwork.ast_nodes import Program

logger = logging.getLogger(__name__)

FFL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffl")


def builtin_ffl_paths() -> list[str]:
    """Every shipped ``.ffl`` file, sorted for a deterministic load order."""
    if not os.path.isdir(FFL_DIR):
        return []
    return [
        os.path.join(FFL_DIR, name) for name in sorted(os.listdir(FFL_DIR)) if name.endswith(".ffl")
    ]


@lru_cache(maxsize=1)
def builtin_programs() -> tuple[Program, ...]:
    """The parsed shipped FFL.

    A parse failure here is a packaging fault, not an author's problem, so it is
    logged and skipped rather than raised: a broken shipped file must not stop
    someone from compiling their own workflow. The drift check in
    ``facetwork.handlers`` is where it surfaces as a hard error.
    """
    from facetwork import parse

    programs = []
    for path in builtin_ffl_paths():
        try:
            with open(path) as fh:
                programs.append(parse(fh.read()))
        except Exception as exc:  # noqa: BLE001 — see docstring
            logger.warning("shipped FFL %s did not parse: %s", os.path.basename(path), exc)
    return tuple(programs)


def builtin_namespaces() -> set[str]:
    return {ns.name for program in builtin_programs() for ns in program.namespaces}


@lru_cache(maxsize=1)
def builtin_declarations() -> tuple[dict, ...]:
    """The shipped FFL in emitted (runtime) form.

    The runtime resolves a step's facet against the workflow's STORED AST, which
    contains only what was compiled. `fw.*` is not in there — it is ambient, like
    the handlers — so without this a `fw.file.List` step fails with "cannot
    resolve facet in program AST" on a runner that is perfectly able to run it.

    Resolution falls back to these declarations, which means it also works for
    workflows compiled and stored BEFORE the built-ins existed.
    """
    from facetwork.emitter import emit_dict

    out: list[dict] = []
    for program in builtin_programs():
        out.extend(emit_dict(program, include_locations=False).get("declarations", []))
    return tuple(out)
