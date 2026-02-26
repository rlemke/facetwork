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

"""Script phase handlers.

Handles facet and statement script execution phases.
FacetScriptsBeginHandler executes ScriptBlock bodies when present.
"""

from typing import TYPE_CHECKING

from ..changers.base import StateChangeResult
from ..script_executor import ScriptExecutor
from .base import StateHandler

if TYPE_CHECKING:
    pass


class FacetScriptsBeginHandler(StateHandler):
    """Handler for state.facet.scripts.Begin.

    Executes facet-level scripts via ScriptExecutor when the facet
    has a ScriptBlock body. Otherwise passes through.
    """

    def process_state(self) -> StateChangeResult:
        """Begin facet scripts execution."""
        # Look up facet definition
        facet_def = self.context.get_facet_definition(self.step.facet_name)
        if facet_def is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Check for pre_script (new format) first, then fall back to body ScriptBlock
        script_def = facet_def.get("pre_script")
        is_pre_script = script_def is not None

        if script_def is None:
            body = facet_def.get("body")
            if body is not None and not isinstance(body, list) and body.get("type") == "ScriptBlock":
                script_def = body

        if script_def is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Build params dict from step attributes
        params: dict = {}
        for name, attr in self.step.attributes.params.items():
            params[name] = attr.value

        # Execute script
        code = script_def.get("code", "")
        language = script_def.get("language", "python")
        executor = ScriptExecutor()
        result = executor.execute(code, params, language)

        if not result.success:
            return self.error(RuntimeError(result.error or "Script execution failed"))

        # Pre-script writes back as params (modifies inputs for downstream);
        # legacy body script writes as returns (backward compat)
        for name, value in result.result.items():
            self.step.set_attribute(name, value, is_return=not is_pre_script)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class FacetScriptsEndHandler(StateHandler):
    """Handler for state.facet.scripts.End.

    Completes facet scripts phase.
    """

    def process_state(self) -> StateChangeResult:
        """End facet scripts execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class StatementScriptsBeginHandler(StateHandler):
    """Handler for state.statement.scripts.Begin.

    Executes statement-level scripts. Currently a pass-through.
    """

    def process_state(self) -> StateChangeResult:
        """Begin statement scripts execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class StatementScriptsEndHandler(StateHandler):
    """Handler for state.statement.scripts.End.

    Completes statement scripts phase.
    """

    def process_state(self) -> StateChangeResult:
        """End statement scripts execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)
