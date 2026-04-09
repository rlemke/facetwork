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

"""Tests for FFL dependency resolution."""

from facetwork.runtime import DependencyGraph


class TestDependencyGraph:
    """Tests for DependencyGraph."""

    def test_from_ast_simple(self):
        """Test building graph from simple AST."""
        block_ast = {
            "steps": [
                {
                    "id": "step-1",
                    "name": "s1",
                    "call": {
                        "target": "Value",
                        "args": [
                            {"name": "input", "value": {"type": "InputRef", "path": ["input"]}}
                        ],
                    },
                },
                {
                    "id": "step-2",
                    "name": "s2",
                    "call": {
                        "target": "Value",
                        "args": [
                            {"name": "input", "value": {"type": "StepRef", "path": ["s1", "input"]}}
                        ],
                    },
                },
            ]
        }

        graph = DependencyGraph.from_ast(block_ast, {"input"})

        assert "step-1" in graph.statements
        assert "step-2" in graph.statements

        # s1 has no dependencies (only input ref)
        assert len(graph.dependencies["step-1"]) == 0

        # s2 depends on s1
        assert "step-1" in graph.dependencies["step-2"]

    def test_from_ast_with_yield(self):
        """Test building graph with yield statement."""
        block_ast = {
            "steps": [{"id": "step-1", "name": "s1", "call": {"target": "Value", "args": []}}],
            "yield": {
                "id": "yield-1",
                "call": {
                    "target": "TestOne",
                    "args": [
                        {"name": "output", "value": {"type": "StepRef", "path": ["s1", "input"]}}
                    ],
                },
            },
        }

        graph = DependencyGraph.from_ast(block_ast, set())

        assert "step-1" in graph.statements
        assert "yield-1" in graph.statements
        assert graph.statements["yield-1"].is_yield is True
        assert "step-1" in graph.dependencies["yield-1"]

    def test_can_create(self):
        """Test dependency satisfaction check."""
        block_ast = {
            "steps": [
                {"id": "s1", "name": "s1", "call": {"target": "V", "args": []}},
                {
                    "id": "s2",
                    "name": "s2",
                    "call": {
                        "target": "V",
                        "args": [
                            {"name": "x", "value": {"type": "StepRef", "path": ["s1", "out"]}}
                        ],
                    },
                },
            ]
        }

        graph = DependencyGraph.from_ast(block_ast, set())

        # s1 can be created with no completed steps
        assert graph.can_create("s1", set()) is True

        # s2 cannot be created until s1 is complete
        assert graph.can_create("s2", set()) is False
        assert graph.can_create("s2", {"s1"}) is True

    def test_get_ready_statements(self):
        """Test getting statements ready for creation."""
        block_ast = {
            "steps": [
                {"id": "a", "name": "a", "call": {"target": "V", "args": []}},
                {"id": "b", "name": "b", "call": {"target": "V", "args": []}},
                {
                    "id": "c",
                    "name": "c",
                    "call": {
                        "target": "V",
                        "args": [
                            {"name": "x", "value": {"type": "StepRef", "path": ["a", "out"]}},
                            {"name": "y", "value": {"type": "StepRef", "path": ["b", "out"]}},
                        ],
                    },
                },
            ]
        }

        graph = DependencyGraph.from_ast(block_ast, set())

        # Initially, a and b are ready
        ready = graph.get_ready_statements(set())
        ready_ids = {s.id for s in ready}
        assert "a" in ready_ids
        assert "b" in ready_ids
        assert "c" not in ready_ids

        # After a completes, b and c (but c still needs b)
        ready = graph.get_ready_statements({"a"})
        ready_ids = {s.id for s in ready}
        assert "b" in ready_ids
        assert "c" not in ready_ids

        # After both a and b complete, c is ready
        ready = graph.get_ready_statements({"a", "b"})
        ready_ids = {s.id for s in ready}
        assert "c" in ready_ids

    def test_topological_order(self):
        """Test topological ordering."""
        block_ast = {
            "steps": [
                {"id": "a", "name": "a", "call": {"target": "V", "args": []}},
                {
                    "id": "b",
                    "name": "b",
                    "call": {
                        "target": "V",
                        "args": [{"name": "x", "value": {"type": "StepRef", "path": ["a", "out"]}}],
                    },
                },
                {
                    "id": "c",
                    "name": "c",
                    "call": {
                        "target": "V",
                        "args": [{"name": "x", "value": {"type": "StepRef", "path": ["b", "out"]}}],
                    },
                },
            ]
        }

        graph = DependencyGraph.from_ast(block_ast, set())
        order = graph.topological_order()

        # a must come before b, b must come before c
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")


class TestYieldDeferral:
    """Tests for yield deferral until all non-yield statements complete."""

    def test_yield_deferred_until_all_non_yields_complete(self):
        """Yield is NOT ready until all non-yield statements are terminal,
        even if the yield's explicit dependencies are already satisfied."""
        block_ast = {
            "steps": [
                {"id": "s1", "name": "s1", "call": {"target": "V", "args": []}},
                {"id": "s2", "name": "s2", "call": {"target": "V", "args": []}},
            ],
            "yield": {
                "id": "y1",
                "call": {
                    "target": "W",
                    "args": [{"name": "out", "value": {"type": "StepRef", "path": ["s1", "x"]}}],
                },
            },
        }
        graph = DependencyGraph.from_ast(block_ast, set())

        # Initially: s1 and s2 ready, yield not ready
        ready_ids = {s.id for s in graph.get_ready_statements(set())}
        assert "s1" in ready_ids
        assert "s2" in ready_ids
        assert "y1" not in ready_ids

        # After s1 completes (yield's explicit dep): yield still NOT ready (s2 pending)
        ready_ids = {s.id for s in graph.get_ready_statements({"s1"})}
        assert "s2" in ready_ids
        assert "y1" not in ready_ids

        # After both s1 and s2 complete: NOW yield is ready
        ready_ids = {s.id for s in graph.get_ready_statements({"s1", "s2"})}
        assert "y1" in ready_ids

    def test_yield_no_deps_still_deferred(self):
        """Yield with zero explicit dependencies still waits for all non-yields."""
        block_ast = {
            "steps": [
                {"id": "s1", "name": "s1", "call": {"target": "V", "args": []}},
            ],
            "yield": {
                "id": "y1",
                "call": {"target": "W", "args": []},
            },
        }
        graph = DependencyGraph.from_ast(block_ast, set())

        # Yield has no deps, but s1 is not terminal — yield NOT ready
        ready_ids = {s.id for s in graph.get_ready_statements(set())}
        assert "s1" in ready_ids
        assert "y1" not in ready_ids

        # After s1 completes, yield becomes ready
        ready_ids = {s.id for s in graph.get_ready_statements({"s1"})}
        assert "y1" in ready_ids


class TestCollectionDependencyExtraction:
    """Tests for dependency extraction from collection literals."""

    def test_deps_in_array_literal(self):
        """Step refs inside ArrayLiteral create dependencies."""
        block_ast = {
            "steps": [
                {"id": "a", "name": "a", "call": {"target": "V", "args": []}},
                {
                    "id": "b",
                    "name": "b",
                    "call": {
                        "target": "V",
                        "args": [
                            {
                                "name": "items",
                                "value": {
                                    "type": "ArrayLiteral",
                                    "elements": [
                                        {"type": "StepRef", "path": ["a", "out"]},
                                        {"type": "Int", "value": 1},
                                    ],
                                },
                            }
                        ],
                    },
                },
            ]
        }
        graph = DependencyGraph.from_ast(block_ast, set())
        assert "a" in graph.dependencies["b"]

    def test_deps_in_map_literal(self):
        """Step refs inside MapLiteral create dependencies."""
        block_ast = {
            "steps": [
                {"id": "a", "name": "a", "call": {"target": "V", "args": []}},
                {
                    "id": "b",
                    "name": "b",
                    "call": {
                        "target": "V",
                        "args": [
                            {
                                "name": "config",
                                "value": {
                                    "type": "MapLiteral",
                                    "entries": [
                                        {
                                            "key": "val",
                                            "value": {"type": "StepRef", "path": ["a", "out"]},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            ]
        }
        graph = DependencyGraph.from_ast(block_ast, set())
        assert "a" in graph.dependencies["b"]

    def test_deps_in_index_expr(self):
        """Step refs in IndexExpr create dependencies."""
        block_ast = {
            "steps": [
                {"id": "a", "name": "a", "call": {"target": "V", "args": []}},
                {
                    "id": "b",
                    "name": "b",
                    "call": {
                        "target": "V",
                        "args": [
                            {
                                "name": "item",
                                "value": {
                                    "type": "IndexExpr",
                                    "target": {"type": "StepRef", "path": ["a", "out"]},
                                    "index": {"type": "Int", "value": 0},
                                },
                            }
                        ],
                    },
                },
            ]
        }
        graph = DependencyGraph.from_ast(block_ast, set())
        assert "a" in graph.dependencies["b"]

    def test_deps_in_index_expr_index(self):
        """Step refs in IndexExpr index position create dependencies."""
        block_ast = {
            "steps": [
                {"id": "a", "name": "a", "call": {"target": "V", "args": []}},
                {"id": "c", "name": "c", "call": {"target": "V", "args": []}},
                {
                    "id": "b",
                    "name": "b",
                    "call": {
                        "target": "V",
                        "args": [
                            {
                                "name": "item",
                                "value": {
                                    "type": "IndexExpr",
                                    "target": {"type": "StepRef", "path": ["a", "out"]},
                                    "index": {"type": "StepRef", "path": ["c", "idx"]},
                                },
                            }
                        ],
                    },
                },
            ]
        }
        graph = DependencyGraph.from_ast(block_ast, set())
        assert "a" in graph.dependencies["b"]
        assert "c" in graph.dependencies["b"]

    def test_no_deps_from_input_refs_in_collections(self):
        """InputRef inside collections does not create dependencies."""
        block_ast = {
            "steps": [
                {
                    "id": "a",
                    "name": "a",
                    "call": {
                        "target": "V",
                        "args": [
                            {
                                "name": "items",
                                "value": {
                                    "type": "ArrayLiteral",
                                    "elements": [
                                        {"type": "InputRef", "path": ["x"]},
                                    ],
                                },
                            }
                        ],
                    },
                }
            ]
        }
        graph = DependencyGraph.from_ast(block_ast, {"x"})
        assert graph.dependencies["a"] == set()
