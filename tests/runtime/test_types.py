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

"""Tests for AFL runtime types."""

from afl.runtime import (
    AttributeValue,
    FacetAttributes,
    ObjectType,
    block_id,
    step_id,
    workflow_id,
)


class TestObjectType:
    """Tests for ObjectType constants."""

    def test_is_block(self):
        """Test block type detection."""
        assert ObjectType.is_block(ObjectType.AND_THEN)
        assert ObjectType.is_block(ObjectType.AND_MAP)
        assert ObjectType.is_block(ObjectType.AND_WHEN)
        assert ObjectType.is_block(ObjectType.BLOCK)
        assert not ObjectType.is_block(ObjectType.VARIABLE_ASSIGNMENT)
        assert not ObjectType.is_block(ObjectType.YIELD_ASSIGNMENT)
        assert not ObjectType.is_block(ObjectType.WORKFLOW)

    def test_is_statement(self):
        """Test statement type detection."""
        assert ObjectType.is_statement(ObjectType.VARIABLE_ASSIGNMENT)
        assert ObjectType.is_statement(ObjectType.YIELD_ASSIGNMENT)
        assert not ObjectType.is_statement(ObjectType.AND_THEN)
        assert not ObjectType.is_statement(ObjectType.WORKFLOW)


class TestAttributeValue:
    """Tests for AttributeValue."""

    def test_type_inference(self):
        """Test automatic type inference."""
        int_attr = AttributeValue("count", 42)
        assert int_attr.type_hint == "Long"

        str_attr = AttributeValue("name", "test")
        assert str_attr.type_hint == "String"

        bool_attr = AttributeValue("flag", True)
        assert bool_attr.type_hint == "Boolean"

    def test_explicit_type(self):
        """Test explicit type hint."""
        attr = AttributeValue("value", 42, "Int32")
        assert attr.type_hint == "Int32"


class TestFacetAttributes:
    """Tests for FacetAttributes."""

    def test_params_and_returns(self):
        """Test parameter and return value management."""
        attrs = FacetAttributes()

        attrs.set_param("input", 10)
        attrs.set_return("output", 20)

        assert attrs.get_param("input") == 10
        assert attrs.get_return("output") == 20

    def test_merge(self):
        """Test attribute merging."""
        attrs1 = FacetAttributes()
        attrs1.set_param("a", 1)
        attrs1.set_return("x", 10)

        attrs2 = FacetAttributes()
        attrs2.set_param("b", 2)
        attrs2.set_return("y", 20)

        attrs1.merge(attrs2)

        assert attrs1.get_param("a") == 1
        assert attrs1.get_param("b") == 2
        assert attrs1.get_return("x") == 10
        assert attrs1.get_return("y") == 20


class TestIdGeneration:
    """Tests for ID generation functions."""

    def test_unique_ids(self):
        """Test that generated IDs are unique."""
        ids = {step_id() for _ in range(100)}
        assert len(ids) == 100

    def test_id_types(self):
        """Test ID type wrappers."""
        s_id = step_id()
        b_id = block_id()
        w_id = workflow_id()

        # All should be strings
        assert isinstance(s_id, str)
        assert isinstance(b_id, str)
        assert isinstance(w_id, str)
