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

"""Claude agent runner for FFL workflow execution.

Wraps the Evaluator to automatically dispatch event facets to Claude
via the Anthropic API with tool use, or to custom registered handlers.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import TokenBudgetExceededError
from .evaluator import Evaluator, ExecutionResult, ExecutionStatus
from .persistence import PersistenceAPI
from .states import StepState
from .step import StepDefinition

try:
    import anthropic  # noqa: F401

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


@dataclass
class TokenUsage:
    """Tracks cumulative token usage across API calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    api_calls: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        """Accumulate usage from a single API call."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.api_calls += 1

    def to_dict(self) -> dict:
        """Serialize for telemetry/logging."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "api_calls": self.api_calls,
        }


# Mapping from FFL type names to JSON Schema types
FFL_TYPE_MAP: dict[str, dict] = {
    "Long": {"type": "integer"},
    "Int": {"type": "integer"},
    "Double": {"type": "number"},
    "String": {"type": "string"},
    "Boolean": {"type": "boolean"},
    "List": {"type": "array"},
    "Map": {"type": "object"},
}


@dataclass
class ToolDefinition:
    """Definition of a tool derived from an EventFacetDecl.

    The tool's input_schema is built from the facet's returns (what Claude produces).
    The facet's params provide context for Claude's message.
    """

    name: str
    description: str
    input_schema: dict
    param_names: list[str]
    return_names: list[str]
    prompt_block: dict | None = None


class ToolRegistry:
    """Registry of custom handlers for event facet types.

    Registry of handler functions keyed by facet name, with optional default fallback.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict], dict]] = {}
        self._default_handler: Callable[[str, dict], dict] | None = None

    def register(self, event_type: str, handler: Callable[[dict], dict]) -> None:
        """Register a handler for a specific event facet type.

        Args:
            event_type: The event facet name
            handler: Function (payload) -> result dict
        """
        self._handlers[event_type] = handler

    def set_default_handler(self, handler: Callable[[str, dict], dict]) -> None:
        """Set a fallback handler for unregistered event types.

        Args:
            handler: Function (event_type, payload) -> result dict
        """
        self._default_handler = handler

    def has_handler(self, event_type: str) -> bool:
        """Check if a specific or default handler exists."""
        return event_type in self._handlers or self._default_handler is not None

    def handle(self, event_type: str, payload: dict) -> dict | None:
        """Dispatch to registered handler, then default, or return None.

        Args:
            event_type: The event facet name
            payload: Parameter values for the event

        Returns:
            Result dict, or None if no handler available
        """
        handler = self._handlers.get(event_type)
        if handler is not None:
            return handler(payload)
        if self._default_handler is not None:
            return self._default_handler(event_type, payload)
        return None


class ClaudeAgentRunner:
    """Runs FFL workflows, dispatching event facets to Claude or custom handlers.

    Usage:
        store = MemoryStore()
        evaluator = Evaluator(persistence=store)
        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=anthropic.Anthropic(),
        )
        result = runner.run(workflow_ast, inputs={"x": 1}, program_ast=program_ast)
    """

    def __init__(
        self,
        evaluator: Evaluator,
        persistence: PersistenceAPI,
        *,
        anthropic_client: Any = None,
        model: str = "claude-sonnet-4-20250514",
        system_prompt: str | None = None,
        tool_registry: ToolRegistry | None = None,
        max_dispatches: int = 100,
        max_turns: int = 10,
        max_retries: int = 2,
        max_tokens: int = 4096,
        token_budget: int | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.persistence = persistence
        self.anthropic_client = anthropic_client
        self.model = model
        self.system_prompt = system_prompt
        self.tool_registry = tool_registry or ToolRegistry()
        self.max_dispatches = max_dispatches
        self.max_turns = max_turns
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.token_budget = token_budget
        self._token_usage = TokenUsage()

    @property
    def token_usage(self) -> TokenUsage:
        """Read-only access to cumulative token usage."""
        return self._token_usage

    def run(
        self,
        workflow_ast: dict,
        inputs: dict | None = None,
        program_ast: dict | None = None,
        *,
        task_description: str | None = None,
    ) -> ExecutionResult:
        """Execute a workflow end-to-end, dispatching event facets as needed.

        Args:
            workflow_ast: Compiled workflow AST
            inputs: Optional input parameter values
            program_ast: Optional program AST for facet lookups
            task_description: Optional description for Claude's context

        Returns:
            ExecutionResult with outputs or error
        """
        # Reset token usage for this run
        self._token_usage = TokenUsage()

        # Extract tool definitions from program AST
        tool_defs = self._extract_tool_definitions(program_ast)
        claude_tools = [self._to_anthropic_tool(td) for td in tool_defs]

        # Initial execution
        result = self.evaluator.execute(workflow_ast, inputs, program_ast)

        dispatch_count = 0
        workflow_name = workflow_ast.get("name", "unknown")

        try:
            while result.status == ExecutionStatus.PAUSED and dispatch_count < self.max_dispatches:
                # Find steps blocked at EVENT_TRANSMIT
                blocked_steps = self._find_blocked_steps(result.workflow_id)
                if not blocked_steps:
                    break

                for step in blocked_steps:
                    dispatch_count += 1
                    if dispatch_count > self.max_dispatches:
                        break

                    step_result = self._dispatch_single_step(
                        step,
                        claude_tools=claude_tools,
                        tool_defs=tool_defs,
                        workflow_name=workflow_name,
                        task_description=task_description,
                    )
                    self.evaluator.continue_step(step.id, step_result)

                # Resume evaluation
                result = self.evaluator.resume(
                    result.workflow_id,
                    workflow_ast,
                    program_ast,
                    inputs,
                )
        except TokenBudgetExceededError:
            result = ExecutionResult(
                success=False,
                workflow_id=result.workflow_id,
                status=ExecutionStatus.ERROR,
                error=TokenBudgetExceededError(
                    budget=self.token_budget or 0,
                    used=self._token_usage.total_tokens,
                ),
            )

        result.token_usage = self._token_usage.to_dict()
        return result

    def _find_blocked_steps(self, workflow_id: str) -> list[StepDefinition]:
        """Find steps blocked at EVENT_TRANSMIT for a workflow."""
        all_steps = self.persistence.get_steps_by_workflow(workflow_id)
        return [s for s in all_steps if s.state == StepState.EVENT_TRANSMIT and not s.is_terminal]

    def _dispatch_single_step(
        self,
        step: StepDefinition,
        *,
        claude_tools: list[dict],
        tool_defs: list[ToolDefinition],
        workflow_name: str,
        task_description: str | None,
    ) -> dict:
        """Dispatch a single blocked step via custom handler or Claude.

        Returns:
            Result dict to pass to continue_step
        """
        # Build payload from step params
        payload = {name: attr.value for name, attr in step.attributes.params.items()}

        # Try custom handler first
        custom_result = self.tool_registry.handle(step.facet_name, payload)
        if custom_result is not None:
            return custom_result

        # Fall back to Claude API
        if self.anthropic_client is None:
            if not HAS_ANTHROPIC:
                raise RuntimeError(
                    f"No handler registered for event facet '{step.facet_name}' "
                    "and the anthropic package is not installed. "
                    "Install it with: pip install anthropic"
                )
            raise RuntimeError(
                f"No handler registered for event facet '{step.facet_name}' "
                "and no anthropic_client was provided."
            )

        return self._call_claude(
            step=step,
            payload=payload,
            claude_tools=claude_tools,
            tool_defs=tool_defs,
            workflow_name=workflow_name,
            task_description=task_description,
        )

    def _evaluate_prompt_template(
        self,
        step: StepDefinition,
        tool_defs: list[ToolDefinition],
    ) -> tuple[str | None, str | None, str | None]:
        """Evaluate prompt template from the facet's PromptBlock.

        Args:
            step: The step being dispatched
            tool_defs: Available tool definitions

        Returns:
            Tuple of (system_prompt, user_template, model_override).
            Any element may be None if not specified in the prompt block.
        """
        # Find matching tool definition
        tool_def = None
        for td in tool_defs:
            if td.name == step.facet_name:
                tool_def = td
                break

        if tool_def is None or tool_def.prompt_block is None:
            return None, None, None

        block = tool_def.prompt_block

        # Build safe default dict for interpolation
        param_values: dict[str, Any] = {}
        for name, attr in step.attributes.params.items():
            param_values[name] = attr.value

        class SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return f"{{{key}}}"

        safe_params = SafeDict(param_values)

        # Extract system prompt
        system = block.get("system")

        # Evaluate template
        template = block.get("template")
        evaluated = None
        if template:
            evaluated = template.format_map(safe_params)

        # Model override
        model_override = block.get("model")

        return system, evaluated, model_override

    def _execute_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        tool_defs: list[ToolDefinition],
    ) -> dict:
        """Execute an intermediate tool call.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool

        Returns:
            Tool result dict
        """
        result = self.tool_registry.handle(tool_name, tool_input)
        if result is not None:
            return result
        # No handler — pass through input as result
        return tool_input

    def _build_retry_message(self, facet_name: str, attempt: int) -> str:
        """Build a retry message for Claude when it didn't use the target tool.

        Args:
            facet_name: The expected tool name
            attempt: Current retry attempt number

        Returns:
            Retry message string
        """
        return (
            f"You must use the '{facet_name}' tool to provide the result. "
            f"Please call the '{facet_name}' tool with the appropriate values. "
            f"(Retry attempt {attempt})"
        )

    def _multi_turn_loop(
        self,
        messages: list[dict],
        system: str,
        model: str,
        claude_tools: list[dict],
        tool_defs: list[ToolDefinition],
        step: StepDefinition,
    ) -> dict | None:
        """Run multi-turn conversation loop with tool use.

        Args:
            messages: Conversation messages so far
            system: System prompt
            model: Model to use
            claude_tools: Anthropic tool definitions
            tool_defs: Internal tool definitions
            step: Step being dispatched

        Returns:
            Result dict if target tool_use found, None if loop exhausted
        """
        for _turn in range(self.max_turns):
            # Budget check before API call
            if (
                self.token_budget is not None
                and self._token_usage.total_tokens >= self.token_budget
            ):
                raise TokenBudgetExceededError(
                    budget=self.token_budget,
                    used=self._token_usage.total_tokens,
                    step_id=step.id,
                )

            response = self.anthropic_client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                tools=claude_tools,
            )

            # Capture token usage
            usage = getattr(response, "usage", None)
            if usage is not None:
                self._token_usage.add(
                    getattr(usage, "input_tokens", 0),
                    getattr(usage, "output_tokens", 0),
                )

            # Check for target facet tool_use (the final answer)
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == step.facet_name:
                    return block.input

            # If stop_reason is not tool_use, Claude is done without calling target
            if response.stop_reason != "tool_use":
                return None

            # Execute intermediate tool calls and build assistant + tool_result messages
            assistant_content = []
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    result = self._execute_tool_call(block.name, block.input, tool_defs)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        }
                    )
                elif getattr(block, "type", None) == "text":
                    assistant_content.append(
                        {
                            "type": "text",
                            "text": block.text,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        # Max turns exhausted
        return None

    def _call_claude(
        self,
        *,
        step: StepDefinition,
        payload: dict,
        claude_tools: list[dict],
        tool_defs: list[ToolDefinition],
        workflow_name: str,
        task_description: str | None,
    ) -> dict:
        """Dispatch an event facet step to Claude with multi-turn and retry.

        Returns:
            Result dict from Claude's tool_use response
        """
        # Evaluate prompt template
        template_system, template_msg, model_override = self._evaluate_prompt_template(
            step, tool_defs
        )

        system = (
            template_system
            or self.system_prompt
            or (f"You are an agent in workflow '{workflow_name}'. Use tools to complete tasks.")
        )
        model = model_override or self.model

        # Build user message
        if template_msg:
            user_message = template_msg
        else:
            parts = [f"Workflow: {workflow_name}"]
            if task_description:
                parts.append(f"Task: {task_description}")
            parts.append(f"Event facet: {step.facet_name}")
            parts.append(f"Parameters: {payload}")
            parts.append("Please use the appropriate tool to provide the result.")
            user_message = "\n".join(parts)

        messages = [{"role": "user", "content": user_message}]

        # Multi-turn loop with retry
        for attempt in range(self.max_retries + 1):
            result = self._multi_turn_loop(
                list(messages),  # Copy to allow retry from scratch
                system,
                model,
                claude_tools,
                tool_defs,
                step,
            )
            if result is not None:
                return result

            # Append retry message for next attempt
            if attempt < self.max_retries:
                retry_msg = self._build_retry_message(step.facet_name, attempt + 1)
                messages.append({"role": "assistant", "content": "I wasn't able to use the tool."})
                messages.append({"role": "user", "content": retry_msg})

        # All retries exhausted
        return {}

    def _extract_tool_definitions(self, program_ast: dict | None) -> list[ToolDefinition]:
        """Extract ToolDefinitions from EventFacetDecl nodes in the program AST."""
        if not program_ast:
            return []
        declarations = program_ast.get("declarations", [])
        return self._search_for_event_facets(declarations)

    def _search_for_event_facets(self, declarations: list) -> list[ToolDefinition]:
        """Recursively search declarations for EventFacetDecl nodes."""
        results: list[ToolDefinition] = []
        for decl in declarations:
            decl_type = decl.get("type", "")
            if decl_type == "EventFacetDecl":
                results.append(self._build_tool_definition(decl))
            elif decl_type == "Namespace":
                nested = decl.get("declarations", [])
                results.extend(self._search_for_event_facets(nested))
        return results

    def _build_tool_definition(self, decl: dict) -> ToolDefinition:
        """Build a ToolDefinition from an EventFacetDecl dict."""
        name = decl.get("name", "")
        params = decl.get("params", [])
        returns = decl.get("returns", [])

        param_names = [p.get("name", "") for p in params]
        return_names = [r.get("name", "") for r in returns]

        # Build JSON Schema properties from returns (what Claude fills in)
        properties: dict[str, dict] = {}
        for ret in returns:
            ret_name = ret.get("name", "")
            ret_type = ret.get("type", "String")
            properties[ret_name] = FFL_TYPE_MAP.get(ret_type, {"type": "string"})

        # Build description with param info
        param_desc = ", ".join(f"{p.get('name', '')}: {p.get('type', 'Any')}" for p in params)
        description = f"Process {name}."
        if param_desc:
            description += f" Parameters: {param_desc}."
        description += " Return the result values."

        # Extract prompt block if present
        prompt_block = None
        body = decl.get("body")
        if body and body.get("type") == "PromptBlock":
            prompt_block = body

        return ToolDefinition(
            name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": return_names,
            },
            param_names=param_names,
            return_names=return_names,
            prompt_block=prompt_block,
        )

    def _to_anthropic_tool(self, tool_def: ToolDefinition) -> dict:
        """Convert a ToolDefinition to an Anthropic API tool dict."""
        return {
            "name": tool_def.name,
            "description": tool_def.description,
            "input_schema": tool_def.input_schema,
        }


@dataclass
class LLMHandlerConfig:
    """Configuration for LLMHandler.

    Attributes:
        model: Claude model to use
        system_prompt: System prompt for the conversation
        max_tokens: Maximum tokens for the response
        max_turns: Maximum multi-turn conversation rounds
        max_retries: Maximum retry attempts
        token_budget: Optional cumulative token budget (None = unlimited)
    """

    model: str = "claude-sonnet-4-20250514"
    system_prompt: str = "You are a helpful assistant. Use tools to provide results."
    max_tokens: int = 4096
    max_turns: int = 10
    max_retries: int = 2
    token_budget: int | None = None


class LLMHandler:
    """Standalone LLM-backed handler for use with AgentPoller.register().

    Provides a simple payload-in, result-out interface backed by Claude.
    No Evaluator or workflow awareness — just dispatches to the API
    with optional prompt templates, multi-turn tool use, and retry.

    Usage:
        handler = LLMHandler(
            anthropic_client=anthropic.Anthropic(),
            config=LLMHandlerConfig(model="claude-sonnet-4-20250514"),
            tool_definitions=[...],
        )
        result = handler.handle({"input": "some data"})
    """

    def __init__(
        self,
        anthropic_client: Any,
        config: LLMHandlerConfig | None = None,
        *,
        tool_definitions: list[dict] | None = None,
        tool_registry: ToolRegistry | None = None,
        prompt_template: str | None = None,
    ) -> None:
        self.anthropic_client = anthropic_client
        self.config = config or LLMHandlerConfig()
        self.tool_definitions = tool_definitions or []
        self.tool_registry = tool_registry or ToolRegistry()
        self.prompt_template = prompt_template
        self._token_usage = TokenUsage()

    @property
    def token_usage(self) -> TokenUsage:
        """Read-only access to cumulative token usage."""
        return self._token_usage

    def handle(self, payload: dict) -> dict:
        """Handle a payload by dispatching to Claude.

        Args:
            payload: Input data dict

        Returns:
            Result dict from Claude's response
        """
        # Build user message from template or payload
        if self.prompt_template:

            class SafeDict(dict):
                def __missing__(self, key: str) -> str:
                    return f"{{{key}}}"

            user_message = self.prompt_template.format_map(SafeDict(payload))
        else:
            user_message = f"Process the following input and use a tool to provide the result.\n\nInput: {payload}"

        messages: list[dict] = [{"role": "user", "content": user_message}]

        # Multi-turn loop with retry
        for attempt in range(self.config.max_retries + 1):
            result = self._multi_turn_loop(list(messages))
            if result is not None:
                return result

            if attempt < self.config.max_retries:
                messages.append({"role": "assistant", "content": "I wasn't able to use the tool."})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Please use a tool to provide the result. (Retry attempt {attempt + 1})",
                    }
                )

        return {}

    def _multi_turn_loop(self, messages: list[dict]) -> dict | None:
        """Run multi-turn conversation loop.

        Returns:
            Result dict from first tool_use block, or None if exhausted
        """
        for _turn in range(self.config.max_turns):
            # Budget check before API call
            if (
                self.config.token_budget is not None
                and self._token_usage.total_tokens >= self.config.token_budget
            ):
                raise TokenBudgetExceededError(
                    budget=self.config.token_budget,
                    used=self._token_usage.total_tokens,
                )

            response = self.anthropic_client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=self.config.system_prompt,
                messages=messages,
                tools=self.tool_definitions,
            )

            # Capture token usage
            usage = getattr(response, "usage", None)
            if usage is not None:
                self._token_usage.add(
                    getattr(usage, "input_tokens", 0),
                    getattr(usage, "output_tokens", 0),
                )

            # Check for tool_use blocks
            tool_use_blocks = [
                b for b in response.content if getattr(b, "type", None) == "tool_use"
            ]

            if tool_use_blocks:
                # If there are no intermediate tools to handle, return first result
                first = tool_use_blocks[0]

                # Check if tool registry can handle it (intermediate tool)
                handler_result = self.tool_registry.handle(first.name, first.input)
                if handler_result is None:
                    # No handler — this is the final answer
                    return first.input

                # Execute intermediate tools and continue
                assistant_content = []
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                        result = self.tool_registry.handle(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(result if result is not None else block.input),
                            }
                        )
                    elif getattr(block, "type", None) == "text":
                        assistant_content.append(
                            {
                                "type": "text",
                                "text": block.text,
                            }
                        )

                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})
                continue

            # No tool use — done
            return None

        return None

    async def handle_async(self, payload: dict) -> dict:
        """Async wrapper around handle().

        Args:
            payload: Input data dict

        Returns:
            Result dict from Claude's response
        """
        return self.handle(payload)
