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

"""Tests for FFL Claude agent runner.

Tests ToolRegistry, ToolDefinition extraction, and ClaudeAgentRunner
with both custom handlers and mock Anthropic client.
"""

import pytest

from facetwork.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    Telemetry,
)
from facetwork.runtime.agent import (
    ClaudeAgentRunner,
    LLMHandler,
    LLMHandlerConfig,
    ToolDefinition,
    ToolRegistry,
)

# =========================================================================
# ToolRegistry unit tests
# =========================================================================


class TestToolRegistry:
    """Unit tests for ToolRegistry."""

    def test_register_and_handle(self):
        """Custom handler called with correct payload."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": p["input"] * 2})

        result = registry.handle("CountDocuments", {"input": 5})
        assert result == {"output": 10}

    def test_unregistered_returns_none(self):
        """No handler returns None."""
        registry = ToolRegistry()
        result = registry.handle("Unknown", {"input": 1})
        assert result is None

    def test_default_handler(self):
        """Fallback default handler works."""
        registry = ToolRegistry()
        registry.set_default_handler(lambda event_type, payload: {"handled": event_type})

        result = registry.handle("SomeEvent", {"input": 1})
        assert result == {"handled": "SomeEvent"}

    def test_specific_overrides_default(self):
        """Specific handler takes priority over default."""
        registry = ToolRegistry()
        registry.register("Specific", lambda p: {"source": "specific"})
        registry.set_default_handler(lambda t, p: {"source": "default"})

        result = registry.handle("Specific", {})
        assert result == {"source": "specific"}

        result2 = registry.handle("Other", {})
        assert result2 == {"source": "default"}

    def test_has_handler_specific(self):
        registry = ToolRegistry()
        assert registry.has_handler("Foo") is False
        registry.register("Foo", lambda p: {})
        assert registry.has_handler("Foo") is True

    def test_has_handler_default(self):
        registry = ToolRegistry()
        assert registry.has_handler("Anything") is False
        registry.set_default_handler(lambda t, p: {})
        assert registry.has_handler("Anything") is True


# =========================================================================
# ToolDefinition extraction tests
# =========================================================================


class TestToolDefinitionExtraction:
    """Tests for extracting ToolDefinitions from program AST."""

    def _make_runner(self):
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        return ClaudeAgentRunner(evaluator=evaluator, persistence=store)

    def test_extract_event_facet_decl(self):
        """EventFacetDecl found, FacetDecl ignored."""
        runner = self._make_runner()
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "FacetDecl",
                    "name": "Value",
                    "params": [{"name": "input", "type": "Long"}],
                },
                {
                    "type": "EventFacetDecl",
                    "name": "CountDocuments",
                    "params": [{"name": "input", "type": "Long"}],
                    "returns": [{"name": "output", "type": "Long"}],
                },
            ],
        }

        tool_defs = runner._extract_tool_definitions(program_ast)
        assert len(tool_defs) == 1
        assert tool_defs[0].name == "CountDocuments"
        assert tool_defs[0].param_names == ["input"]
        assert tool_defs[0].return_names == ["output"]

    def test_nested_namespace_extraction(self):
        """EventFacetDecl inside Namespace found."""
        runner = self._make_runner()
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "example.ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "ProcessData",
                            "params": [{"name": "data", "type": "String"}],
                            "returns": [{"name": "result", "type": "String"}],
                        },
                    ],
                },
            ],
        }

        tool_defs = runner._extract_tool_definitions(program_ast)
        assert len(tool_defs) == 1
        assert tool_defs[0].name == "ProcessData"

    def test_type_mapping(self):
        """AFL types map to correct JSON Schema types."""
        runner = self._make_runner()
        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "MultiType",
                    "params": [],
                    "returns": [
                        {"name": "count", "type": "Long"},
                        {"name": "ratio", "type": "Double"},
                        {"name": "label", "type": "String"},
                        {"name": "active", "type": "Boolean"},
                    ],
                },
            ],
        }

        tool_defs = runner._extract_tool_definitions(program_ast)
        assert len(tool_defs) == 1
        schema = tool_defs[0].input_schema
        assert schema["properties"]["count"] == {"type": "integer"}
        assert schema["properties"]["ratio"] == {"type": "number"}
        assert schema["properties"]["label"] == {"type": "string"}
        assert schema["properties"]["active"] == {"type": "boolean"}
        assert schema["required"] == ["count", "ratio", "label", "active"]

    def test_no_program_ast(self):
        """None program_ast yields empty list."""
        runner = self._make_runner()
        assert runner._extract_tool_definitions(None) == []


# =========================================================================
# Example 4 fixtures (reused from test_evaluator.py)
# =========================================================================


@pytest.fixture
def example4_program_ast():
    """Program AST with Value, SomeFacet, CountDocuments (event), and Adder."""
    return {
        "type": "Program",
        "declarations": [
            {
                "type": "FacetDecl",
                "name": "Value",
                "params": [{"name": "input", "type": "Long"}],
            },
            {
                "type": "FacetDecl",
                "name": "SomeFacet",
                "params": [{"name": "input", "type": "Long"}],
                "returns": [{"name": "output", "type": "Long"}],
            },
            {
                "type": "EventFacetDecl",
                "name": "CountDocuments",
                "params": [{"name": "input", "type": "Long"}],
                "returns": [{"name": "output", "type": "Long"}],
            },
            {
                "type": "FacetDecl",
                "name": "Adder",
                "params": [
                    {"name": "a", "type": "Long"},
                    {"name": "b", "type": "Long"},
                ],
                "returns": [{"name": "sum", "type": "Long"}],
                "body": {
                    "type": "AndThenBlock",
                    "steps": [
                        {
                            "type": "StepStmt",
                            "id": "step-s1",
                            "name": "s1",
                            "call": {
                                "type": "CallExpr",
                                "target": "SomeFacet",
                                "args": [
                                    {
                                        "name": "input",
                                        "value": {"type": "InputRef", "path": ["a"]},
                                    }
                                ],
                            },
                            "body": {
                                "type": "AndThenBlock",
                                "steps": [
                                    {
                                        "type": "StepStmt",
                                        "id": "step-subStep1",
                                        "name": "subStep1",
                                        "call": {
                                            "type": "CallExpr",
                                            "target": "CountDocuments",
                                            "args": [
                                                {
                                                    "name": "input",
                                                    "value": {"type": "Int", "value": 3},
                                                }
                                            ],
                                        },
                                    },
                                ],
                                "yield": {
                                    "type": "YieldStmt",
                                    "id": "yield-SF",
                                    "call": {
                                        "type": "CallExpr",
                                        "target": "SomeFacet",
                                        "args": [
                                            {
                                                "name": "output",
                                                "value": {
                                                    "type": "BinaryExpr",
                                                    "operator": "+",
                                                    "left": {
                                                        "type": "StepRef",
                                                        "path": ["subStep1", "input"],
                                                    },
                                                    "right": {"type": "Int", "value": 10},
                                                },
                                            }
                                        ],
                                    },
                                },
                            },
                        },
                        {
                            "type": "StepStmt",
                            "id": "step-s2",
                            "name": "s2",
                            "call": {
                                "type": "CallExpr",
                                "target": "Value",
                                "args": [
                                    {
                                        "name": "input",
                                        "value": {"type": "InputRef", "path": ["b"]},
                                    }
                                ],
                            },
                        },
                    ],
                    "yield": {
                        "type": "YieldStmt",
                        "id": "yield-Adder",
                        "call": {
                            "type": "CallExpr",
                            "target": "Adder",
                            "args": [
                                {
                                    "name": "sum",
                                    "value": {
                                        "type": "BinaryExpr",
                                        "operator": "+",
                                        "left": {"type": "StepRef", "path": ["s1", "output"]},
                                        "right": {"type": "StepRef", "path": ["s2", "input"]},
                                    },
                                }
                            ],
                        },
                    },
                },
            },
        ],
    }


@pytest.fixture
def example4_workflow_ast():
    """AST for AddWorkflow (Example 4)."""
    return {
        "type": "WorkflowDecl",
        "name": "AddWorkflow",
        "params": [
            {"name": "x", "type": "Long"},
            {"name": "y", "type": "Long"},
        ],
        "returns": [{"name": "result", "type": "Long"}],
        "body": {
            "type": "AndThenBlock",
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-addition",
                    "name": "addition",
                    "call": {
                        "type": "CallExpr",
                        "target": "Adder",
                        "args": [
                            {
                                "name": "a",
                                "value": {"type": "InputRef", "path": ["x"]},
                            },
                            {
                                "name": "b",
                                "value": {"type": "InputRef", "path": ["y"]},
                            },
                        ],
                    },
                },
            ],
            "yield": {
                "type": "YieldStmt",
                "id": "yield-AW",
                "call": {
                    "type": "CallExpr",
                    "target": "AddWorkflow",
                    "args": [
                        {
                            "name": "result",
                            "value": {"type": "StepRef", "path": ["addition", "sum"]},
                        }
                    ],
                },
            },
        },
    }


# =========================================================================
# ClaudeAgentRunner with custom handlers (no API calls)
# =========================================================================


class TestClaudeAgentRunnerWithCustomHandlers:
    """Integration tests using custom handlers, no Anthropic API."""

    def test_full_workflow_with_custom_handler(self, example4_workflow_ast, example4_program_ast):
        """Example 4 workflow completes end-to-end using registered handler."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        # CountDocuments handler: just passes through (no transformation)
        registry.register("CountDocuments", lambda payload: {})

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED
        # subStep1.input = 3
        # yield SomeFacet(output = 3 + 10 = 13)
        # s1.output = 13, s2.input = 2
        # yield Adder(sum = 13 + 2 = 15)
        # yield AddWorkflow(result = 15)
        assert result.outputs["result"] == 15

    def test_no_handler_no_client_raises(self, example4_workflow_ast, example4_program_ast):
        """Missing handler + no client raises RuntimeError."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            # No registry, no anthropic_client
        )

        with pytest.raises(RuntimeError, match="No handler registered"):
            runner.run(
                example4_workflow_ast,
                inputs={"x": 1, "y": 2},
                program_ast=example4_program_ast,
            )

    def test_max_dispatches_exceeded(self, example4_workflow_ast, example4_program_ast):
        """Safety limit stops dispatching and returns current result."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        # Handler that never actually unblocks (returns empty — step still completes)
        registry.register("CountDocuments", lambda payload: {})

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
            max_dispatches=0,  # Zero dispatches allowed
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # Should be PAUSED since we didn't dispatch anything
        assert result.status == ExecutionStatus.PAUSED

    def test_default_handler_used(self, example4_workflow_ast, example4_program_ast):
        """Default handler is used when no specific handler is registered."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.set_default_handler(lambda event_type, payload: {})

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED


# =========================================================================
# Mock Anthropic client
# =========================================================================


class MockUsage:
    """Mock usage object from Anthropic API response."""

    def __init__(self, input_tokens: int = 100, output_tokens: int = 50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class MockToolUseBlock:
    """Mock tool_use content block from Anthropic API."""

    def __init__(self, name: str, input_data: dict):
        self.type = "tool_use"
        self.id = "toolu_mock"
        self.name = name
        self.input = input_data


class MockTextBlock:
    """Mock text content block from Anthropic API."""

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class MockResponse:
    """Mock Anthropic messages.create() response."""

    def __init__(self, content: list, usage: "MockUsage | None" = None):
        self.content = content
        self.model = "claude-sonnet-4-20250514"
        self.role = "assistant"
        self.stop_reason = (
            "tool_use"
            if any(getattr(b, "type", None) == "tool_use" for b in content)
            else "end_turn"
        )
        self.usage = usage if usage is not None else MockUsage()


class MockMessages:
    """Mock messages namespace on the Anthropic client."""

    def __init__(self):
        self.calls: list[dict] = []
        self._responses: list[MockResponse] = []
        self._default_response: MockResponse | None = None

    def set_response(self, response: MockResponse) -> None:
        """Set a single response for all calls."""
        self._default_response = response

    def add_response(self, response: MockResponse) -> None:
        """Queue a response for the next call."""
        self._responses.append(response)

    def create(self, **kwargs) -> MockResponse:
        self.calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        if self._default_response:
            return self._default_response
        raise ValueError("No mock response configured")


class MockAnthropicClient:
    """Mock anthropic.Anthropic() client."""

    def __init__(self):
        self.messages = MockMessages()


# =========================================================================
# ClaudeAgentRunner with mock Claude client
# =========================================================================


class TestClaudeAgentRunnerWithMockClient:
    """Tests with mock Anthropic API client."""

    def test_claude_receives_correct_tools(self, example4_workflow_ast, example4_program_ast):
        """Verify tools passed to Claude match EventFacetDecl schemas."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 42})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # Verify Claude was called
        assert len(client.messages.calls) == 1
        call = client.messages.calls[0]

        # Verify tools contain CountDocuments
        tools = call["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "CountDocuments"
        assert tools[0]["input_schema"]["properties"]["output"] == {"type": "integer"}
        assert tools[0]["input_schema"]["required"] == ["output"]

    def test_tool_use_response_mapped_to_result(self, example4_workflow_ast, example4_program_ast):
        """Claude's tool_use input becomes continue_step result."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        # Claude returns output=99 for CountDocuments
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 99})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

    def test_text_only_response(self, example4_workflow_ast, example4_program_ast):
        """Claude doesn't call tool — empty result dict used."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        # Claude returns text only, no tool use
        client.messages.set_response(MockResponse([MockTextBlock("I cannot process this.")]))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # Workflow should still complete (empty result for step)
        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

    def test_system_prompt_passed(self, example4_workflow_ast, example4_program_ast):
        """Custom system prompt is passed to Claude."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(MockResponse([MockToolUseBlock("CountDocuments", {})]))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            system_prompt="Custom system prompt here.",
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        call = client.messages.calls[0]
        assert call["system"] == "Custom system prompt here."

    def test_task_description_in_message(self, example4_workflow_ast, example4_program_ast):
        """Task description appears in the user message."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(MockResponse([MockToolUseBlock("CountDocuments", {})]))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
            task_description="Count all documents in the database",
        )

        call = client.messages.calls[0]
        user_msg = call["messages"][0]["content"]
        assert "Count all documents in the database" in user_msg


# =========================================================================
# Prompt Template Evaluation tests
# =========================================================================


class TestPromptTemplateEvaluation:
    """Tests for prompt template interpolation from PromptBlock."""

    def test_template_interpolation(self, example4_workflow_ast, example4_program_ast):
        """Prompt template placeholders are filled with param values."""
        # Add a PromptBlock to the EventFacetDecl
        for decl in example4_program_ast["declarations"]:
            if decl.get("name") == "CountDocuments":
                decl["body"] = {
                    "type": "PromptBlock",
                    "template": "Count documents for input={input}",
                }

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 42})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        call = client.messages.calls[0]
        user_msg = call["messages"][0]["content"]
        assert "Count documents for input=3" in user_msg

    def test_system_override_from_prompt_block(self, example4_workflow_ast, example4_program_ast):
        """System prompt from PromptBlock overrides default."""
        for decl in example4_program_ast["declarations"]:
            if decl.get("name") == "CountDocuments":
                decl["body"] = {
                    "type": "PromptBlock",
                    "system": "You are a document counter.",
                    "template": "Count for {input}",
                }

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 10})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        call = client.messages.calls[0]
        assert call["system"] == "You are a document counter."

    def test_model_override_from_prompt_block(self, example4_workflow_ast, example4_program_ast):
        """Model from PromptBlock overrides default."""
        for decl in example4_program_ast["declarations"]:
            if decl.get("name") == "CountDocuments":
                decl["body"] = {
                    "type": "PromptBlock",
                    "template": "Count for {input}",
                    "model": "claude-opus-4-20250514",
                }

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 10})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        call = client.messages.calls[0]
        assert call["model"] == "claude-opus-4-20250514"

    def test_multi_param_interpolation(self):
        """Multiple parameters interpolated in template."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
        )

        # Build a tool definition with a prompt block
        tool_def = ToolDefinition(
            name="TestFacet",
            description="Test",
            input_schema={"type": "object", "properties": {}, "required": []},
            param_names=["name", "age"],
            return_names=["result"],
            prompt_block={
                "type": "PromptBlock",
                "template": "Hello {name}, you are {age} years old.",
            },
        )

        # Create a mock step with params
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="TestFacet",
        )
        step.set_attribute("name", "Alice", is_return=False)
        step.set_attribute("age", 30, is_return=False)

        system, template, model = runner._evaluate_prompt_template(step, [tool_def])
        assert template == "Hello Alice, you are 30 years old."

    def test_missing_param_safe_default(self):
        """Missing params use {param} as safe default."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
        )

        tool_def = ToolDefinition(
            name="TestFacet",
            description="Test",
            input_schema={"type": "object", "properties": {}, "required": []},
            param_names=["name"],
            return_names=[],
            prompt_block={
                "type": "PromptBlock",
                "template": "Hello {name}, your id is {id}.",
            },
        )

        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="TestFacet",
        )
        step.set_attribute("name", "Bob", is_return=False)

        system, template, model = runner._evaluate_prompt_template(step, [tool_def])
        assert template == "Hello Bob, your id is {id}."

    def test_no_prompt_block_fallback(self):
        """No PromptBlock returns all None."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
        )

        tool_def = ToolDefinition(
            name="TestFacet",
            description="Test",
            input_schema={"type": "object", "properties": {}, "required": []},
            param_names=[],
            return_names=[],
        )

        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        step = StepDefinition.create(
            workflow_id="wf-1",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="TestFacet",
        )

        system, template, model = runner._evaluate_prompt_template(step, [tool_def])
        assert system is None
        assert template is None
        assert model is None


# =========================================================================
# Multi-Turn Tool Use tests
# =========================================================================


class TestMultiTurnToolUse:
    """Tests for multi-turn tool use loop."""

    def test_single_turn_unchanged(self, example4_workflow_ast, example4_program_ast):
        """Single tool_use response still works as before."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 42})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert len(client.messages.calls) == 1

    def test_intermediate_tool_then_final(self, example4_workflow_ast, example4_program_ast):
        """Intermediate tool call followed by target tool call."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()

        # First response: intermediate tool (not CountDocuments)
        intermediate = MockToolUseBlock("SearchIndex", {"query": "test"})
        intermediate.id = "toolu_search"
        resp1 = MockResponse([intermediate])
        resp1.stop_reason = "tool_use"

        # Second response: target tool
        resp2 = MockResponse([MockToolUseBlock("CountDocuments", {"output": 50})])

        client.messages.add_response(resp1)
        client.messages.add_response(resp2)

        # Register handler for intermediate tool
        registry = ToolRegistry()
        registry.register("SearchIndex", lambda p: {"results": ["doc1"]})

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            tool_registry=registry,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert len(client.messages.calls) == 2

    def test_max_turns_exceeded(self, example4_workflow_ast, example4_program_ast):
        """Multi-turn loop stops after max_turns."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()

        # Always return intermediate tool (never the target)
        def make_intermediate():
            block = MockToolUseBlock("SearchIndex", {"query": "test"})
            block.id = "toolu_search"
            resp = MockResponse([block])
            resp.stop_reason = "tool_use"
            return resp

        # Queue more responses than max_turns * (max_retries + 1)
        for _ in range(50):
            client.messages.add_response(make_intermediate())

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_turns=2,
            max_retries=0,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # Should still complete (empty result)
        assert result.success is True
        # Max 2 turns with 0 retries = 2 calls
        assert len(client.messages.calls) == 2

    def test_end_turn_without_tool_use(self, example4_workflow_ast, example4_program_ast):
        """end_turn stop_reason without tool_use triggers retry or returns empty."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        text_resp = MockResponse([MockTextBlock("I can't do that.")])
        text_resp.stop_reason = "end_turn"
        client.messages.set_response(text_resp)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_retries=0,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # Returns empty result, workflow still completes
        assert result.success is True

    def test_multiple_intermediate_tools_in_one_turn(
        self, example4_workflow_ast, example4_program_ast
    ):
        """Multiple intermediate tools in a single response are all executed."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()

        # Response with two intermediate tools
        block1 = MockToolUseBlock("ToolA", {"a": 1})
        block1.id = "toolu_a"
        block2 = MockToolUseBlock("ToolB", {"b": 2})
        block2.id = "toolu_b"
        resp1 = MockResponse([block1, block2])
        resp1.stop_reason = "tool_use"

        # Final answer
        resp2 = MockResponse([MockToolUseBlock("CountDocuments", {"output": 100})])

        client.messages.add_response(resp1)
        client.messages.add_response(resp2)

        calls = []
        registry = ToolRegistry()
        # Register specific intermediate handlers (not a default, to avoid catching CountDocuments
        # in _dispatch_single_step before reaching Claude)
        registry.register("ToolA", lambda p: (calls.append("ToolA"), p)[1])
        registry.register("ToolB", lambda p: (calls.append("ToolB"), p)[1])

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            tool_registry=registry,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert "ToolA" in calls
        assert "ToolB" in calls


# =========================================================================
# Intelligent Retry tests
# =========================================================================


class TestIntelligentRetry:
    """Tests for retry when Claude doesn't call target tool."""

    def test_retry_succeeds_on_second_attempt(self, example4_workflow_ast, example4_program_ast):
        """Retry succeeds when second attempt uses the target tool."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()

        # First attempt: text only (no tool use)
        text_resp = MockResponse([MockTextBlock("Let me think...")])
        text_resp.stop_reason = "end_turn"

        # Second attempt (after retry): correct tool use
        tool_resp = MockResponse([MockToolUseBlock("CountDocuments", {"output": 77})])

        client.messages.add_response(text_resp)
        client.messages.add_response(tool_resp)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_retries=2,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert len(client.messages.calls) == 2

    def test_retry_message_contains_facet_name(self, example4_workflow_ast, example4_program_ast):
        """Retry message includes the expected facet name."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()

        # First attempt: text only
        text_resp = MockResponse([MockTextBlock("Hmm...")])
        text_resp.stop_reason = "end_turn"

        # Second attempt: success
        tool_resp = MockResponse([MockToolUseBlock("CountDocuments", {"output": 1})])

        client.messages.add_response(text_resp)
        client.messages.add_response(tool_resp)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_retries=1,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # Second call should include retry message
        second_call = client.messages.calls[1]
        messages = second_call["messages"]
        # Find the retry message
        retry_found = False
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and "CountDocuments" in content:
                retry_found = True
                break
        assert retry_found

    def test_retries_exhausted_returns_empty(self, example4_workflow_ast, example4_program_ast):
        """All retries exhausted returns empty dict."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()

        # All attempts: text only
        text_resp = MockResponse([MockTextBlock("Can't do it.")])
        text_resp.stop_reason = "end_turn"
        client.messages.set_response(text_resp)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_retries=2,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # 1 initial + 2 retries = 3 calls
        assert len(client.messages.calls) == 3
        # Workflow still completes (empty result)
        assert result.success is True

    def test_no_retry_when_first_attempt_succeeds(
        self, example4_workflow_ast, example4_program_ast
    ):
        """No retry needed when first attempt succeeds."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 42})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_retries=2,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert len(client.messages.calls) == 1


# =========================================================================
# LLMHandler tests
# =========================================================================


class TestLLMHandler:
    """Tests for standalone LLMHandler utility class."""

    def test_basic_call(self):
        """LLMHandler dispatches to Claude and returns tool_use result."""
        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("process", {"result": "done"})])
        )

        handler = LLMHandler(
            anthropic_client=client,
            tool_definitions=[
                {
                    "name": "process",
                    "description": "Process input",
                    "input_schema": {
                        "type": "object",
                        "properties": {"result": {"type": "string"}},
                    },
                }
            ],
        )

        result = handler.handle({"input": "test"})
        assert result == {"result": "done"}

    def test_prompt_interpolation(self):
        """LLMHandler interpolates prompt template with payload."""
        client = MockAnthropicClient()
        client.messages.set_response(MockResponse([MockToolUseBlock("process", {"output": "ok"})]))

        handler = LLMHandler(
            anthropic_client=client,
            prompt_template="Process the data: {data} with mode={mode}",
            tool_definitions=[
                {
                    "name": "process",
                    "description": "Process",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        handler.handle({"data": "test_data", "mode": "fast"})

        call = client.messages.calls[0]
        user_msg = call["messages"][0]["content"]
        assert "Process the data: test_data with mode=fast" in user_msg

    def test_custom_system_prompt(self):
        """LLMHandler uses custom system prompt from config."""
        client = MockAnthropicClient()
        client.messages.set_response(MockResponse([MockToolUseBlock("process", {})]))

        config = LLMHandlerConfig(system_prompt="You are a specialized counter.")

        handler = LLMHandler(
            anthropic_client=client,
            config=config,
            tool_definitions=[
                {
                    "name": "process",
                    "description": "Process",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        handler.handle({"input": "test"})

        call = client.messages.calls[0]
        assert call["system"] == "You are a specialized counter."

    def test_tool_registry_intermediate(self):
        """LLMHandler handles intermediate tool calls via registry."""
        client = MockAnthropicClient()

        # First response: intermediate tool
        intermediate = MockToolUseBlock("lookup", {"key": "abc"})
        intermediate.id = "toolu_lookup"
        resp1 = MockResponse([intermediate])
        resp1.stop_reason = "tool_use"

        # Second response: final answer (no handler for "answer")
        resp2 = MockResponse([MockToolUseBlock("answer", {"result": "42"})])

        client.messages.add_response(resp1)
        client.messages.add_response(resp2)

        registry = ToolRegistry()
        registry.register("lookup", lambda p: {"value": "found_it"})

        handler = LLMHandler(
            anthropic_client=client,
            tool_registry=registry,
            tool_definitions=[
                {
                    "name": "lookup",
                    "description": "Lookup",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        result = handler.handle({"input": "test"})
        assert result == {"result": "42"}
        assert len(client.messages.calls) == 2

    def test_async_variant(self):
        """handle_async wraps handle()."""
        import asyncio

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("process", {"result": "async_done"})])
        )

        handler = LLMHandler(
            anthropic_client=client,
            tool_definitions=[
                {
                    "name": "process",
                    "description": "Process",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        result = asyncio.run(handler.handle_async({"input": "test"}))
        assert result == {"result": "async_done"}


# =========================================================================
# TokenUsage unit tests
# =========================================================================

from facetwork.runtime.agent import TokenUsage
from facetwork.runtime.errors import TokenBudgetExceededError


class TestTokenUsage:
    """Unit tests for the TokenUsage dataclass."""

    def test_defaults(self):
        """All fields default to zero."""
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0
        assert usage.api_calls == 0

    def test_add_single(self):
        """Single add accumulates correctly."""
        usage = TokenUsage()
        usage.add(100, 50)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150
        assert usage.api_calls == 1

    def test_add_multiple(self):
        """Multiple adds accumulate correctly."""
        usage = TokenUsage()
        usage.add(100, 50)
        usage.add(200, 80)
        assert usage.input_tokens == 300
        assert usage.output_tokens == 130
        assert usage.total_tokens == 430
        assert usage.api_calls == 2

    def test_to_dict(self):
        """to_dict returns correct serialization."""
        usage = TokenUsage()
        usage.add(100, 50)
        d = usage.to_dict()
        assert d == {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "api_calls": 1,
        }


# =========================================================================
# Token usage tracking integration tests
# =========================================================================


class TestTokenUsageTracking:
    """Tests for token usage tracking in ClaudeAgentRunner and LLMHandler."""

    def test_usage_tracked_from_single_call(self, example4_workflow_ast, example4_program_ast):
        """Token usage captured from a single API call."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse(
                [MockToolUseBlock("CountDocuments", {"output": 42})],
                usage=MockUsage(input_tokens=200, output_tokens=80),
            )
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert result.token_usage is not None
        assert result.token_usage["input_tokens"] == 200
        assert result.token_usage["output_tokens"] == 80
        assert result.token_usage["total_tokens"] == 280
        assert result.token_usage["api_calls"] == 1

    def test_usage_accumulated_multi_turn(self):
        """Token usage accumulated across multi-turn conversation in LLMHandler."""
        client = MockAnthropicClient()

        # First response: intermediate tool call
        intermediate = MockToolUseBlock("lookup", {"key": "abc"})
        intermediate.id = "toolu_lookup"
        resp1 = MockResponse(
            [intermediate],
            usage=MockUsage(input_tokens=100, output_tokens=30),
        )
        resp1.stop_reason = "tool_use"

        # Second response: final answer
        resp2 = MockResponse(
            [MockToolUseBlock("answer", {"result": "42"})],
            usage=MockUsage(input_tokens=150, output_tokens=40),
        )

        client.messages.add_response(resp1)
        client.messages.add_response(resp2)

        registry = ToolRegistry()
        registry.register("lookup", lambda p: {"value": "found"})

        handler = LLMHandler(
            anthropic_client=client,
            tool_registry=registry,
            tool_definitions=[
                {
                    "name": "lookup",
                    "description": "Lookup",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        handler.handle({"input": "test"})

        assert handler.token_usage.input_tokens == 250
        assert handler.token_usage.output_tokens == 70
        assert handler.token_usage.total_tokens == 320
        assert handler.token_usage.api_calls == 2

    def test_usage_reset_between_runs(self, example4_workflow_ast, example4_program_ast):
        """Token usage resets at the start of each run()."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse(
                [MockToolUseBlock("CountDocuments", {"output": 42})],
                usage=MockUsage(input_tokens=200, output_tokens=80),
            )
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        # First run
        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )
        assert runner.token_usage.total_tokens == 280

        # Second run resets
        result2 = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )
        assert result2.token_usage["total_tokens"] == 280  # fresh, not 560

    def test_max_tokens_passed_to_api(self, example4_workflow_ast, example4_program_ast):
        """Custom max_tokens forwarded to API call."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse([MockToolUseBlock("CountDocuments", {"output": 42})])
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            max_tokens=8192,
        )

        runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert client.messages.calls[0]["max_tokens"] == 8192

    def test_budget_enforcement(self, example4_workflow_ast, example4_program_ast):
        """Budget exceeded stops execution and returns error result."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        # Usage of 150 per call exceeds budget of 100
        client.messages.set_response(
            MockResponse(
                [MockToolUseBlock("CountDocuments", {"output": 42})],
                usage=MockUsage(input_tokens=100, output_tokens=50),
            )
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            token_budget=100,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        # First call succeeds (check is before call), but the result still has token_usage
        # The budget check happens before the NEXT call, so the first call goes through
        assert result.token_usage is not None
        assert result.token_usage["total_tokens"] == 150

    def test_no_budget_unlimited(self, example4_workflow_ast, example4_program_ast):
        """No budget means unlimited calls."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse(
                [MockToolUseBlock("CountDocuments", {"output": 42})],
                usage=MockUsage(input_tokens=5000, output_tokens=3000),
            )
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            token_budget=None,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert result.token_usage["total_tokens"] == 8000

    def test_graceful_when_usage_missing(self, example4_workflow_ast, example4_program_ast):
        """No crash when response has no usage attribute."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        resp = MockResponse([MockToolUseBlock("CountDocuments", {"output": 42})])
        del resp.usage  # simulate missing usage
        client.messages.set_response(resp)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.success is True
        assert result.token_usage["total_tokens"] == 0
        assert result.token_usage["api_calls"] == 0

    def test_token_usage_on_error_result(self, example4_workflow_ast, example4_program_ast):
        """Token usage attached even on budget-exceeded error."""
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        client = MockAnthropicClient()
        # First call: responds with text (no target tool), triggers retry
        # Each call uses 80 tokens total
        resp_text = MockResponse(
            [MockTextBlock("thinking...")],
            usage=MockUsage(input_tokens=50, output_tokens=30),
        )
        resp_text.stop_reason = "end_turn"

        # Budget of 50 — first call uses 80, exceeds budget before second call
        client.messages.add_response(resp_text)
        # Queue a second response that should be blocked by budget
        client.messages.add_response(
            MockResponse(
                [MockToolUseBlock("CountDocuments", {"output": 42})],
                usage=MockUsage(input_tokens=50, output_tokens=30),
            )
        )

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            anthropic_client=client,
            token_budget=50,
        )

        result = runner.run(
            example4_workflow_ast,
            inputs={"x": 1, "y": 2},
            program_ast=example4_program_ast,
        )

        assert result.token_usage is not None
        assert result.token_usage["api_calls"] >= 1

    def test_llm_handler_budget_enforcement(self):
        """LLMHandler raises TokenBudgetExceededError when budget exceeded."""
        client = MockAnthropicClient()

        # First response: intermediate tool call (continues loop)
        intermediate = MockToolUseBlock("lookup", {"key": "abc"})
        intermediate.id = "toolu_int"
        resp1 = MockResponse(
            [intermediate],
            usage=MockUsage(input_tokens=60, output_tokens=40),
        )
        resp1.stop_reason = "tool_use"

        client.messages.add_response(resp1)

        registry = ToolRegistry()
        registry.register("lookup", lambda p: {"value": "found"})

        config = LLMHandlerConfig(token_budget=50, max_turns=5, max_retries=0)
        handler = LLMHandler(
            anthropic_client=client,
            config=config,
            tool_registry=registry,
            tool_definitions=[
                {
                    "name": "lookup",
                    "description": "Lookup",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        with pytest.raises(TokenBudgetExceededError) as exc_info:
            handler.handle({"input": "test"})

        assert exc_info.value.budget == 50
        assert exc_info.value.used == 100

    def test_llm_handler_token_usage_property(self):
        """LLMHandler exposes token_usage property."""
        client = MockAnthropicClient()
        client.messages.set_response(
            MockResponse(
                [MockToolUseBlock("process", {"result": "done"})],
                usage=MockUsage(input_tokens=120, output_tokens=60),
            )
        )

        handler = LLMHandler(
            anthropic_client=client,
            tool_definitions=[
                {
                    "name": "process",
                    "description": "Process",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )

        handler.handle({"input": "test"})

        assert handler.token_usage.input_tokens == 120
        assert handler.token_usage.output_tokens == 60
        assert handler.token_usage.total_tokens == 180
        assert handler.token_usage.api_calls == 1
