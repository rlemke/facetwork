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

"""Compiled-AST feature markers — a forward-compatibility guard.

A compiled AST is persisted (``flow.compiled_ast``) and executed later, possibly
by a DIFFERENT, OLDER runtime. Most AST additions are inert to an old runtime:
an unknown key it never reads changes nothing. Some are NOT — and the dangerous
ones are those that carry *semantics an old runtime would silently drop*.

``after`` is the motivating case. It contributes ordering edges; a runtime that
doesn't know the key still runs the workflow, but with the ordering removed —
producing a race that looks exactly like the bug ``after`` exists to prevent.
Silent wrong-order is worse than a hard failure.

So the compiler stamps ``"features": [...]`` on the program when it uses such a
construct, and this module lets a runtime refuse work it cannot faithfully
execute.

**What this guard can and cannot do.** It protects any runtime that has this
module: it will refuse rather than mis-execute. It cannot retroactively protect
runtimes older than the guard itself — they predate the check and will ignore
both the feature list and the field it describes. The operational rule for those
is unchanged and unavoidable: roll the fleet before using a new construct.
"""

from __future__ import annotations

from typing import Any

#: Features this runtime understands. Add a name here in the SAME change that
#: teaches the runtime to honor it — never earlier.
KNOWN_AST_FEATURES: frozenset[str] = frozenset({"after"})


class UnsupportedASTFeatureError(RuntimeError):
    """A compiled AST requires features this runtime does not implement."""

    def __init__(self, unknown: set[str]):
        self.unknown = set(unknown)
        names = ", ".join(sorted(self.unknown))
        super().__init__(
            f"This compiled workflow uses FFL feature(s) this runtime does not "
            f"support: {names}. It was compiled by a newer Facetwork; running it "
            f"here would silently drop those semantics. Upgrade this runner "
            f"(fw fleet rollout) rather than executing it degraded."
        )


def known_features() -> list[str]:
    """Features this runtime can execute faithfully — the claim-side capability.

    Module-level (not a runner method) because every runner class must report
    the same answer: RegistryRunner, AgentPoller and RunnerService all claim
    tasks, and a class that silently lacked the capability would claim work it
    cannot honor.
    """
    return sorted(KNOWN_AST_FEATURES)


def features_of(program_dict: dict[str, Any] | None) -> set[str]:
    """Return the feature markers a compiled program declares."""
    if not program_dict:
        return set()
    return {str(f) for f in program_dict.get("features", []) or []}


def unsupported_features(program_dict: dict[str, Any] | None) -> set[str]:
    """Return declared features this runtime does not know how to honor."""
    return features_of(program_dict) - KNOWN_AST_FEATURES


def check_supported(program_dict: dict[str, Any] | None) -> None:
    """Raise :class:`UnsupportedASTFeatureError` if the AST needs more than we have."""
    unknown = unsupported_features(program_dict)
    if unknown:
        raise UnsupportedASTFeatureError(unknown)


def collect_features_from_ast(node: Any) -> set[str]:
    """Walk an emitted (dict) AST and collect the features it actually uses.

    Structural walk rather than a compiler-side flag so the marker cannot drift
    from the tree: if a step carries ``after``, the program declares ``after``.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "StepStmt" and value.get("after"):
                found.add("after")
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(node)
    return found
