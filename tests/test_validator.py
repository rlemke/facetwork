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

"""Tests for FFL semantic validator."""

import pytest

from facetwork import parse
from facetwork.validator import FFLValidator, ValidationError, ValidationResult, validate


@pytest.fixture
def validator():
    """Create a validator instance."""
    return FFLValidator()


def _ns(source: str) -> str:
    """Wrap FFL source in a namespace if it contains a top-level workflow."""
    import textwrap

    stripped = textwrap.dedent(source).strip()
    # Track brace depth to distinguish top-level from nested workflows
    lines = stripped.split("\n")
    depth = 0
    needs_wrap = False
    for line in lines:
        s = line.strip()
        if s.startswith("workflow ") and depth == 0:
            needs_wrap = True
            break
        depth += s.count("{") - s.count("}")
    if not needs_wrap:
        return source
    return f"\nnamespace _test {{\n{stripped}\n}}\n"


class TestNameUniqueness:
    """Test name uniqueness validation."""

    def test_duplicate_facet_names(self, validator):
        """Duplicate facet names should error."""
        ast = parse("""
        facet User(name: String)
        facet User(email: String)
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate facet name 'User'" in str(e) for e in result.errors)

    def test_duplicate_workflow_names(self, validator):
        """Duplicate workflow names should error."""
        ast = parse(_ns("""
        workflow Process(input: String)
        workflow Process(data: String)
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate workflow name 'Process'" in str(e) for e in result.errors)

    def test_duplicate_event_facet_names(self, validator):
        """Duplicate event facet names should error."""
        ast = parse("""
        event facet Handler(input: String)
        event facet Handler(data: String)
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate event facet name 'Handler'" in str(e) for e in result.errors)

    def test_facet_workflow_same_name(self, validator):
        """Facet and workflow with same name should error."""
        ast = parse(_ns("""
        facet Process(input: String)
        workflow Process(input: String)
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate" in str(e) and "Process" in str(e) for e in result.errors)

    def test_unique_names_valid(self, validator):
        """Unique names should pass."""
        ast = parse(_ns("""
        facet User(name: String)
        facet Account(id: String)
        workflow Process(input: String)
        """))
        result = validator.validate(ast)
        assert result.is_valid

    def test_duplicate_names_in_namespace(self, validator):
        """Duplicate names within a namespace should error."""
        ast = parse("""
        namespace team.data {
            facet User(name: String)
            facet User(email: String)
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate facet name 'User'" in str(e) for e in result.errors)

    def test_same_name_different_namespaces(self, validator):
        """Same name in different namespaces should be valid."""
        ast = parse("""
        namespace team.a {
            facet User(name: String)
        }
        namespace team.b {
            facet User(email: String)
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_duplicate_step_names(self, validator):
        """Duplicate step names within a block should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            step1 = Data(value = $.input)
            yield Test(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate step name 'step1'" in str(e) for e in result.errors)

    def test_unique_step_names_valid(self, validator):
        """Unique step names should pass."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            step2 = Data(value = $.input)
            yield Test(output = step2.result)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid


class TestStepReferences:
    """Test step reference validation."""

    def test_valid_input_reference(self, validator):
        """Valid $.param reference should pass."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            yield Test(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid

    def test_invalid_input_reference(self, validator):
        """Invalid $.param reference should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.nonexistent)
            yield Test(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Invalid input reference '$.nonexistent'" in str(e) for e in result.errors)

    def test_valid_step_reference(self, validator):
        """Valid step.attr reference should pass."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            step2 = Data(value = step1.result)
            yield Test(output = step2.result)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid

    def test_invalid_step_attribute(self, validator):
        """Invalid step attribute should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            step2 = Data(value = step1.nonexistent)
            yield Test(output = step2.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any(
            "Invalid attribute 'nonexistent' for step 'step1'" in str(e) for e in result.errors
        )

    def test_reference_undefined_step(self, validator):
        """Reference to undefined step should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = undefined.result)
            yield Test(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Reference to undefined step 'undefined'" in str(e) for e in result.errors)

    def test_reference_step_defined_after(self, validator):
        """Reference to step defined after should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = step2.result)
            step2 = Data(value = $.input)
            yield Test(output = step2.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        # Step2 is not defined when step1 tries to reference it
        assert any("undefined step 'step2'" in str(e) for e in result.errors)

    def test_foreach_variable_valid(self, validator):
        """Foreach variable reference should be valid."""
        ast = parse(_ns("""
        facet Process(item: String) => (result: String)
        workflow Test(items: Json) => (results: Json) andThen foreach item in $.items {
            step1 = Process(item = item.value)
            yield Test(results = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid


class TestYieldValidation:
    """Test yield statement validation."""

    def test_valid_yield_containing_facet(self, validator):
        """Yield to containing facet should pass."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            yield Test(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid

    def test_invalid_yield_target(self, validator):
        """Yield to wrong facet should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            yield WrongFacet(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Invalid yield target 'WrongFacet'" in str(e) for e in result.errors)

    def test_yield_to_mixin_valid(self, validator):
        """Yield to mixin should pass."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        facet Extra(data: String) => (extra: String)
        workflow Test(input: String) => (output: String) with Extra(data = "x") andThen {
            step1 = Data(value = $.input)
            yield Test(output = step1.result)
            yield Extra(extra = step1.result)
        }
        """))
        result = validator.validate(ast)
        # This should pass - yields to both containing facet and mixin
        assert result.is_valid

    def test_yield_references_validated(self, validator):
        """References in yield should be validated."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            yield Test(output = undefined.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Reference to undefined step 'undefined'" in str(e) for e in result.errors)

    def test_duplicate_yield_targets(self, validator):
        """Duplicate yield targets should error."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            yield Test(output = step1.result)
            yield Test(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate yield target 'Test'" in str(e) for e in result.errors)


class TestConvenienceFunction:
    """Test module-level validate function."""

    def test_validate_function(self):
        """Test validate() convenience function."""
        ast = parse("facet Test()")
        result = validate(ast)
        assert isinstance(result, ValidationResult)
        assert result.is_valid

    def test_validate_with_errors(self):
        """Test validate() returns errors."""
        ast = parse("""
        facet Test()
        facet Test()
        """)
        result = validate(ast)
        assert not result.is_valid
        assert len(result.errors) > 0


class TestValidationResult:
    """Test ValidationResult class."""

    def test_empty_result_is_valid(self):
        """Empty result should be valid."""
        result = ValidationResult()
        assert result.is_valid
        assert len(result.errors) == 0

    def test_result_with_errors_invalid(self):
        """Result with errors should be invalid."""
        result = ValidationResult()
        result.add_error("Test error")
        assert not result.is_valid
        assert len(result.errors) == 1

    def test_error_string_format(self):
        """Error should format with location."""
        error = ValidationError("Test error", line=10, column=5)
        assert "Test error at line 10, column 5" == str(error)

    def test_error_string_no_location(self):
        """Error without location should format correctly."""
        error = ValidationError("Test error")
        assert "Test error" == str(error)


class TestComplexScenarios:
    """Test complex validation scenarios."""

    def test_nested_block_references(self, validator):
        """References in nested contexts should work."""
        ast = parse(_ns("""
        facet Transform(input: String) => (output: String)
        facet Process(data: String) => (result: String)

        workflow Pipeline(input: String) => (final: String) andThen {
            t1 = Transform(input = $.input)
            p1 = Process(data = t1.output)
            yield Pipeline(final = p1.result)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid

    def test_multiple_errors_reported(self, validator):
        """Multiple errors should all be reported."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        facet Data(other: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.nonexistent)
            step1 = Data(value = $.input)
            yield WrongTarget(output = step1.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        # Should have multiple errors: duplicate name, invalid input ref, duplicate step, invalid yield
        assert len(result.errors) >= 3

    def test_full_namespace_example(self, validator):
        """Full namespace example should validate correctly."""
        ast = parse("""
        namespace team.email {
            facet EmailConfig(host: String, port: Int)
            facet SendResult(messageId: String) => (status: String)

            event facet SendEmail(to: String, subject: String) => (messageId: String)

            workflow BulkSend(recipients: Json, template: String) => (results: Json) andThen foreach r in $.recipients {
                email = SendEmail(to = r.email, subject = $.template)
                yield BulkSend(results = email.messageId)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid


class TestUseStatementValidation:
    """Test use statement validation."""

    def test_valid_use_statement(self, validator):
        """Use statement referencing existing namespace should pass."""
        ast = parse("""
        namespace common.utils {
            facet Helper(value: String)
        }
        namespace app.main {
            use common.utils
            facet App(input: String)
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_invalid_use_statement(self, validator):
        """Use statement referencing non-existent namespace should error."""
        ast = parse("""
        namespace app.main {
            use nonexistent.namespace
            facet App(input: String)
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any(
            "namespace 'nonexistent.namespace' does not exist" in str(e) for e in result.errors
        )

    def test_multiple_valid_use_statements(self, validator):
        """Multiple use statements referencing existing namespaces should pass."""
        ast = parse("""
        namespace lib.a {
            facet FacetA(value: String)
        }
        namespace lib.b {
            facet FacetB(value: String)
        }
        namespace app {
            use lib.a
            use lib.b
            facet App(input: String)
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_mixed_valid_invalid_use_statements(self, validator):
        """Mix of valid and invalid use statements should report errors."""
        ast = parse("""
        namespace lib.a {
            facet FacetA(value: String)
        }
        namespace app {
            use lib.a
            use lib.nonexistent
            facet App(input: String)
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("namespace 'lib.nonexistent' does not exist" in str(e) for e in result.errors)


class TestFacetNameResolution:
    """Test facet name resolution and ambiguity detection."""

    def test_unambiguous_facet_reference(self, validator):
        """Unambiguous facet reference should pass."""
        ast = parse("""
        namespace lib {
            facet Helper(value: String) => (result: String)
        }
        namespace app {
            use lib
            facet App(input: String) => (output: String) andThen {
                h = Helper(value = $.input)
                yield App(output = h.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_ambiguous_facet_reference(self, validator):
        """Ambiguous facet reference should error."""
        ast = parse("""
        namespace a.b {
            facet SomeFacet(input: String) => (result: String)
        }
        namespace c.d {
            facet SomeFacet(input: String) => (result: String)
        }
        namespace app {
            use a.b
            use c.d
            facet App(input: String) => (output: String) andThen {
                s = SomeFacet(input = $.input)
                yield App(output = s.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Ambiguous facet reference 'SomeFacet'" in str(e) for e in result.errors)

    def test_qualified_name_resolves_ambiguity(self, validator):
        """Using fully qualified name should resolve ambiguity."""
        ast = parse("""
        namespace a.b {
            facet SomeFacet(input: String) => (result: String)
        }
        namespace c.d {
            facet SomeFacet(input: String) => (result: String)
        }
        namespace app {
            use a.b
            use c.d
            facet App(input: String) => (output: String) andThen {
                s = a.b.SomeFacet(input = $.input)
                yield App(output = s.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_local_facet_takes_precedence(self, validator):
        """Facet in current namespace takes precedence over imports."""
        ast = parse("""
        namespace lib {
            facet Helper(value: String) => (result: String)
        }
        namespace app {
            use lib
            facet Helper(value: String) => (result: String)
            facet App(input: String) => (output: String) andThen {
                h = Helper(value = $.input)
                yield App(output = h.result)
            }
        }
        """)
        result = validator.validate(ast)
        # Local Helper should be used without ambiguity
        assert result.is_valid

    def test_mixin_with_qualified_name(self, validator):
        """Mixin with qualified name should work."""
        ast = parse("""
        namespace a.b {
            facet SomeFacet(input: String) => (data: String)
        }
        namespace c.d {
            facet SomeFacet(input: String) => (data: String)
            facet OtherFacet(value: String) with a.b.SomeFacet(input = "test")
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_unknown_qualified_facet(self, validator):
        """Unknown fully qualified facet should error."""
        ast = parse("""
        namespace app {
            facet App(input: String) => (output: String) andThen {
                s = nonexistent.namespace.Facet(input = $.input)
                yield App(output = s.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Unknown facet 'nonexistent.namespace.Facet'" in str(e) for e in result.errors)

    def test_ambiguous_import_vs_non_imported_namespace(self, validator):
        """Facet in imported ns + non-imported ns should error on unqualified call."""
        ast = parse("""
        namespace a.b {
            facet Shared(input: String) => (result: String)
        }
        namespace c.d {
            facet Shared(input: String) => (result: String)
        }
        namespace app {
            use a.b
            facet App(input: String) => (output: String) andThen {
                s = Shared(input = $.input)
                yield App(output = s.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Ambiguous facet reference 'Shared'" in str(e) for e in result.errors)
        assert any("a.b.Shared" in str(e) and "c.d.Shared" in str(e) for e in result.errors)

    def test_qualified_resolves_global_ambiguity(self, validator):
        """Qualified call should resolve global ambiguity even with only one import."""
        ast = parse("""
        namespace a.b {
            facet Shared(input: String) => (result: String)
        }
        namespace c.d {
            facet Shared(input: String) => (result: String)
        }
        namespace app {
            use a.b
            facet App(input: String) => (output: String) andThen {
                s = a.b.Shared(input = $.input)
                yield App(output = s.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_local_definition_overrides_global_ambiguity(self, validator):
        """Local facet in current namespace should override global duplicates."""
        ast = parse("""
        namespace a.b {
            facet Shared(input: String) => (result: String)
        }
        namespace c.d {
            facet Shared(input: String) => (result: String)
        }
        namespace app {
            use a.b
            facet Shared(input: String) => (result: String)
            facet App(input: String) => (output: String) andThen {
                s = Shared(input = $.input)
                yield App(output = s.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid


class TestSchemaValidation:
    """Test schema declaration validation."""

    def test_duplicate_schema_names(self, validator):
        """Duplicate schema names in namespace should error."""
        ast = parse("""
        namespace app {
            schema User {
                name: String
            }
            schema User {
                email: String
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate schema name 'User'" in str(e) for e in result.errors)

    def test_schema_facet_same_name(self, validator):
        """Schema and facet with same name should error."""
        ast = parse("""
        namespace app {
            schema User {
                name: String
            }
            facet User(name: String)
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate" in str(e) and "User" in str(e) for e in result.errors)

    def test_duplicate_field_names(self, validator):
        """Duplicate field names within a schema should error."""
        ast = parse("""
        namespace app {
            schema User {
                name: String,
                name: Int
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate schema field name 'name'" in str(e) for e in result.errors)

    def test_valid_schema(self, validator):
        """Valid schema should pass validation."""
        ast = parse("""
        namespace app {
            schema User {
                name: String,
                age: Int
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_duplicate_schema_in_namespace(self, validator):
        """Duplicate schema names in namespace should error."""
        ast = parse("""
        namespace app {
            schema Config {
                key: String
            }
            schema Config {
                value: String
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate schema name 'Config'" in str(e) for e in result.errors)

    def test_schema_and_facet_same_name_in_namespace(self, validator):
        """Schema and facet with same name in namespace should error."""
        ast = parse("""
        namespace app {
            schema Data {
                value: String
            }
            facet Data(value: String)
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Duplicate" in str(e) and "Data" in str(e) for e in result.errors)


class TestAmbiguousReferences:
    """Test ambiguous facet references across namespaces and imports."""

    def test_ambiguous_across_imports_and_toplevel(self, validator):
        """Facet defined both at top-level and in imported namespace is ambiguous."""
        ast = parse("""
        facet Helper(value: String) => (result: String)

        namespace lib {
            facet Helper(value: String) => (result: String)
        }

        namespace app {
            use lib
            workflow Run(input: String) => (output: String) andThen {
                h = Helper(value = $.input)
                yield Run(output = h.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Ambiguous" in str(e) for e in result.errors)

    def test_ambiguous_multiple_global_namespaces(self, validator):
        """Facet defined in two non-imported namespaces should be ambiguous when referenced from third."""
        ast = parse("""
        namespace x {
            facet Shared(a: String) => (r: String)
        }
        namespace y {
            facet Shared(a: String) => (r: String)
        }
        namespace app {
            use x
            use y
            workflow Run(input: String) => (output: String) andThen {
                s = Shared(a = $.input)
                yield Run(output = s.r)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Ambiguous facet reference 'Shared'" in str(e) for e in result.errors)


class TestForwardStepReferences:
    """Test that forward references to later steps are rejected."""

    def test_step2_references_step3_forward(self, validator):
        """A step cannot reference a step defined after it."""
        ast = parse(_ns("""
        facet Data(value: String) => (result: String)
        workflow Test(input: String) => (output: String) andThen {
            step1 = Data(value = $.input)
            step2 = Data(value = step3.result)
            step3 = Data(value = $.input)
            yield Test(output = step3.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("undefined step 'step3'" in str(e) for e in result.errors)


class TestEventFacetValidation:
    """Test event facet with body and mixin references."""

    def test_event_facet_with_body(self, validator):
        """Event facet with andThen body should validate correctly."""
        ast = parse("""
        facet Compute(x: Int) => (result: Int)
        event facet Process(input: Int) => (output: Int) andThen {
            c = Compute(x = $.input)
            yield Process(output = c.result)
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_event_facet_mixin_references(self, validator):
        """Event facet with mixin references should validate."""
        ast = parse("""
        facet Retry(maxAttempts: Int)
        event facet Process(input: String) => (result: String) with Retry(maxAttempts = 3)
        """)
        result = validator.validate(ast)
        assert result.is_valid

    def test_event_facet_invalid_mixin(self, validator):
        """Event facet with unknown mixin should produce an error."""
        ast = parse("""
        event facet Process(input: String) with nonexistent.ns.Mixin(x = "y")
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Unknown facet" in str(e) for e in result.errors)


class TestScriptBlockValidation:
    """Test script block validation."""

    def test_script_block_valid(self, validator):
        """Valid script block passes validation."""
        ast = parse('facet Test() script "x = 1"')
        result = validator.validate(ast)
        assert result.is_valid

    def test_script_block_empty_code(self, validator):
        """Script block with empty code should fail."""
        ast = parse('facet Test() script ""')
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("must contain code" in str(e) for e in result.errors)

    def test_script_block_whitespace_only(self, validator):
        """Script block with only whitespace should fail."""
        ast = parse('facet Test() script "   "')
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("must contain code" in str(e) for e in result.errors)

    def test_script_block_event_facet(self, validator):
        """Event facet with script block validates."""
        ast = parse('event facet Process() script "result = {}"')
        result = validator.validate(ast)
        assert result.is_valid

    def test_script_block_with_params(self, validator):
        """Script block with params validates."""
        ast = parse(
            r'facet Transform(x: String) => (y: String) script "result[\"y\"] = params[\"x\"]"'
        )
        result = validator.validate(ast)
        assert result.is_valid

    def test_pre_script_with_andthen_validates(self, validator):
        """Pre-script combined with andThen blocks validates."""
        ast = parse('facet F() script "x = 1" andThen { s = G() }')
        result = validator.validate(ast)
        assert result.is_valid

    def test_andthen_script_validates(self, validator):
        """andThen script variant validates."""
        ast = parse('facet F() andThen script "y = 2"')
        result = validator.validate(ast)
        assert result.is_valid

    def test_andthen_script_empty_code_fails(self, validator):
        """andThen script with empty code should fail."""
        ast = parse('facet F() andThen script ""')
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("must contain code" in str(e) for e in result.errors)

    def test_mixed_andthen_validates(self, validator):
        """Mixed regular andThen + andThen script validates."""
        ast = parse('facet F() andThen { s = G() } andThen script "y = 2"')
        result = validator.validate(ast)
        assert result.is_valid


class TestPromptBlockValidation:
    """Test prompt block validation."""

    def test_prompt_block_valid(self, validator):
        """Valid prompt block passes validation."""
        ast = parse("""event facet Test(x: String) => (y: String)
prompt {
    system "System prompt"
    template "Process {x}"
    model "claude"
}""")
        result = validator.validate(ast)
        assert result.is_valid

    def test_prompt_block_missing_template(self, validator):
        """Prompt block without template should fail."""
        ast = parse("""event facet Test(x: String)
prompt {
    system "Only system"
}""")
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("template" in str(e) for e in result.errors)

    def test_prompt_block_invalid_placeholder(self, validator):
        """Prompt block with invalid placeholder should fail."""
        ast = parse("""event facet Test(x: String)
prompt {
    template "Value is {invalid_param}"
}""")
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("invalid_param" in str(e) for e in result.errors)

    def test_prompt_block_valid_placeholder(self, validator):
        """Prompt block with valid placeholder should pass."""
        ast = parse("""event facet Test(myParam: String)
prompt {
    template "Value is {myParam}"
}""")
        result = validator.validate(ast)
        assert result.is_valid

    def test_prompt_block_multiple_placeholders(self, validator):
        """Prompt block can use multiple placeholders."""
        ast = parse("""event facet Test(a: String, b: String, c: Int)
prompt {
    template "{a} and {b} make {c}"
}""")
        result = validator.validate(ast)
        assert result.is_valid

    def test_prompt_block_placeholder_in_system(self, validator):
        """Placeholders in system prompt are also validated."""
        ast = parse("""event facet Test(role: String)
prompt {
    system "You are a {role}."
    template "Do something"
}""")
        result = validator.validate(ast)
        assert result.is_valid

    def test_prompt_block_invalid_placeholder_in_system(self, validator):
        """Invalid placeholder in system prompt should fail."""
        ast = parse("""event facet Test(x: String)
prompt {
    system "You are a {undefined_role}."
    template "{x}"
}""")
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("undefined_role" in str(e) for e in result.errors)

    def test_prompt_block_in_namespace(self, validator):
        """Prompt block inside namespace is validated."""
        ast = parse("""namespace ns {
    event facet Query(q: String) => (answer: String)
    prompt {
        template "Question: {q}"
    }
}""")
        result = validator.validate(ast)
        assert result.is_valid


class TestErrorLocation:
    """Test that validation errors include location information."""

    def test_error_line_only(self):
        """Error with line but no column."""
        from facetwork.validator import ValidationError

        error = ValidationError("test error", line=5)
        assert "at line 5" in str(error)
        assert "column" not in str(error)

    def test_add_error_with_location(self, validator):
        """add_error with SourceLocation should capture line/column."""
        from facetwork.ast import SourceLocation
        from facetwork.validator import ValidationResult

        result = ValidationResult()
        loc = SourceLocation(line=10, column=3)
        result.add_error("test", loc)
        assert result.errors[0].line == 10
        assert result.errors[0].column == 3

    def test_add_error_without_location(self, validator):
        """add_error without location should have None line/column."""
        from facetwork.validator import ValidationResult

        result = ValidationResult()
        result.add_error("test")
        assert result.errors[0].line is None
        assert result.errors[0].column is None


class TestMixinCallValidation:
    """Test that mixin call references in step calls are validated."""

    def test_mixin_args_reference_validated(self, validator):
        """References in mixin call arguments should be validated."""
        ast = parse(_ns("""
        facet Config(setting: String)
        facet Process(input: String) => (result: String)
        workflow Test(x: String) => (output: String) andThen {
            p = Process(input = $.x) with Config(setting = $.nonexistent)
            yield Test(output = p.result)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Invalid input reference '$.nonexistent'" in str(e) for e in result.errors)


class TestSchemaInstantiation:
    """Test schema instantiation validation in step statements."""

    def test_valid_schema_instantiation(self, validator):
        """Valid schema instantiation should pass."""
        ast = parse(_ns("""
        namespace app {
            schema Config {
                timeout: Long,
                retries: Long
            }
            event facet DoSomething(config: Config) => (result: String)
            workflow Example() => (output: String) andThen {
                cfg = Config(timeout = 30, retries = 3)
                result = DoSomething(config = cfg.timeout)
                yield Example(output = result.result)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_field_reference(self, validator):
        """Step referencing schema fields should pass."""
        ast = parse(_ns("""
        namespace app {
            schema Data {
                value: String,
                count: Long
            }
            facet Process(input: String) => (output: String)
            workflow Test(x: String) => (result: String) andThen {
                d = Data(value = $.x, count = 5)
                p = Process(input = d.value)
                yield Test(result = p.output)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_invalid_schema_field_reference(self, validator):
        """Reference to nonexistent schema field should error."""
        ast = parse(_ns("""
        namespace app {
            schema Data {
                value: String
            }
            facet Process(input: String) => (output: String)
            workflow Test(x: String) => (result: String) andThen {
                d = Data(value = $.x)
                p = Process(input = d.nonexistent)
                yield Test(result = p.output)
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Invalid attribute 'nonexistent' for step 'd'" in str(e) for e in result.errors)

    def test_unknown_schema_field(self, validator):
        """Unknown field in schema instantiation should error."""
        ast = parse(_ns("""
        namespace app {
            schema Config {
                timeout: Long
            }
            workflow Test() andThen {
                cfg = Config(timeout = 30, unknown = "bad")
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Unknown field 'unknown' for schema 'Config'" in str(e) for e in result.errors)

    def test_schema_with_mixins_error(self, validator):
        """Schema instantiation with mixins should error."""
        ast = parse(_ns("""
        namespace app {
            schema Config {
                timeout: Long
            }
            facet SomeMixin()
            workflow Test() andThen {
                cfg = Config(timeout = 30) with SomeMixin()
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("cannot have mixins" in str(e) for e in result.errors)

    def test_namespaced_schema_instantiation(self, validator):
        """Namespaced schema instantiation should work."""
        ast = parse(_ns("""
        namespace app {
            schema Settings {
                name: String,
                value: Long
            }
            facet Process(s: Settings) => (result: String)
            workflow Test(input: String) => (output: String) andThen {
                s = Settings(name = $.input, value = 42)
                r = Process(s = s.name)
                yield Test(output = r.result)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_qualified_namespaced_schema_instantiation(self, validator):
        """Qualified schema reference should work across namespaces."""
        ast = parse("""
        namespace lib {
            schema Config {
                key: String
            }
        }
        namespace app {
            use lib
            workflow Run(input: String) => (output: String) andThen {
                c = Config(key = $.input)
                yield Run(output = c.key)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_with_concat_expression(self, validator):
        """Schema instantiation with concatenation expression should validate."""
        ast = parse(_ns("""
        namespace app {
            schema Data {
                combined: String
            }
            workflow Test(a: String, b: String) => (result: String) andThen {
                d = Data(combined = $.a ++ $.b)
                yield Test(result = d.combined)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_fields_accessible_after_step(self, validator):
        """Schema fields should be accessible in subsequent steps."""
        ast = parse(_ns("""
        namespace app {
            schema Request {
                url: String,
                method: String
            }
            facet Fetch(url: String, method: String) => (data: String)
            workflow Test(input: String) => (result: String) andThen {
                req = Request(url = $.input, method = "GET")
                resp = Fetch(url = req.url, method = req.method)
                yield Test(result = resp.data)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestBinaryExprValidation:
    """Test validation of BinaryExpr in references."""

    def test_valid_refs_in_binary(self, validator):
        """Valid references in binary expressions pass."""
        ast = parse(_ns("""
        facet Value(input: Long) => (output: Long)
        workflow Test(a: Long) => (output: Long) andThen {
            s = Value(input = $.a + 1)
            yield Test(output = s.output)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_invalid_input_ref_in_binary(self, validator):
        """Invalid input reference in binary expr should error."""
        ast = parse(_ns("""
        facet Value(input: Long) => (output: Long)
        workflow Test(a: Long) => (output: Long) andThen {
            s = Value(input = $.nonexistent + 1)
            yield Test(output = s.output)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("nonexistent" in str(e) for e in result.errors)

    def test_invalid_step_ref_in_binary(self, validator):
        """Invalid step reference in binary expr should error."""
        ast = parse(_ns("""
        facet Value(input: Long) => (output: Long)
        workflow Test(a: Long) => (output: Long) andThen {
            s = Value(input = unknown.output + 1)
            yield Test(output = s.output)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("unknown" in str(e) for e in result.errors)

    def test_nested_binary_in_concat(self, validator):
        """References in binary inside concat should be validated."""
        ast = parse(_ns("""
        facet Value(input: Long) => (output: Long)
        workflow Test(a: Long, b: Long) => (output: String) andThen {
            s = Value(input = $.a + $.b)
            yield Test(output = s.output ++ s.output)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_ref_binary_chain(self, validator):
        """Chain of arithmetic with step refs should validate."""
        ast = parse(_ns("""
        facet Value(input: Long) => (output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s1 = Value(input = $.input + 1)
            s2 = Value(input = s1.output + 1)
            yield Test(output = s2.output + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestMultipleBlockValidation:
    """Test validation of multiple andThen blocks."""

    def test_valid_multi_block(self, validator):
        """Multiple andThen blocks with valid refs should pass."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s1 = V(input = $.input)
                yield Test(a = s1.output)
            } andThen {
                s2 = V(input = $.input)
                yield Test(b = s2.output)
            }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_same_step_names_across_blocks(self, validator):
        """Same step names in different blocks should pass (independent scopes)."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s = V(input = $.input)
                yield Test(a = s.output)
            } andThen {
                s = V(input = $.input)
                yield Test(b = s.output)
            }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_invalid_ref_in_second_block(self, validator):
        """Invalid reference in second block should error."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s1 = V(input = $.input)
                yield Test(a = s1.output)
            } andThen {
                s2 = V(input = $.nonexistent)
                yield Test(b = s2.output)
            }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("nonexistent" in str(e) for e in result.errors)

    def test_cross_block_step_reference_error(self, validator):
        """Reference to step from a sibling andThen block should error."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s1 = V(input = $.input)
                yield Test(a = s1.output)
            } andThen {
                s2 = V(input = s1.output)
                yield Test(b = s2.output)
            }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Cross-block step reference" in str(e) for e in result.errors)
        assert any("s1" in str(e) for e in result.errors)

    def test_cross_block_yield_reference_error(self, validator):
        """Yield referencing step from a sibling block should error."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s1 = V(input = $.input)
                yield Test(a = s1.output)
            } andThen {
                s2 = V(input = $.input)
                yield Test(b = s1.output)
            }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Cross-block step reference" in str(e) for e in result.errors)

    def test_step_body_within_block_ok(self, validator):
        """Step body (step = Call() andThen { ... }) is NOT cross-block."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (result: Long) andThen {
            s1 = V(input = $.input) andThen {
                s2 = V(input = s1.output)
                yield Test(result = s2.output)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_same_step_name_different_blocks_ok(self, validator):
        """Same step name in different blocks is allowed (no cross-ref)."""
        ast = parse(_ns("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s = V(input = $.input)
                yield Test(a = s.output)
            } andThen {
                s = V(input = $.input)
                yield Test(b = s.output)
            }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestCollectionLiteralValidation:
    """Test validation of references inside collection literals."""

    def test_valid_refs_in_array(self, validator):
        """Valid references inside array should pass."""
        ast = parse(_ns("""
        facet V(items: String) => (output: Long)
        workflow Test(x: Long) andThen {
            s = V(items = "test")
            s2 = V(items = [$.x, s.output])
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_invalid_input_ref_in_array(self, validator):
        """Invalid input reference inside array should error."""
        ast = parse(_ns("""
        facet V(items: String)
        workflow Test(x: Long) andThen {
            s = V(items = [$.nonexistent])
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("nonexistent" in str(e) for e in result.errors)

    def test_invalid_step_ref_in_array(self, validator):
        """Invalid step reference inside array should error."""
        ast = parse(_ns("""
        facet V(items: String)
        workflow Test(x: Long) andThen {
            s = V(items = [missing.output])
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("missing" in str(e) for e in result.errors)

    def test_valid_refs_in_map(self, validator):
        """Valid references inside map should pass."""
        ast = parse(_ns("""
        facet V(config: String) => (output: Long)
        workflow Test(x: Long) andThen {
            s = V(config = "test")
            s2 = V(config = #{"a": $.x, "b": s.output})
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_invalid_ref_in_map_value(self, validator):
        """Invalid reference in map value should error."""
        ast = parse(_ns("""
        facet V(config: String)
        workflow Test(x: Long) andThen {
            s = V(config = #{"a": $.missing})
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("missing" in str(e) for e in result.errors)

    def test_valid_refs_in_index_expr(self, validator):
        """Valid references in index expression should pass."""
        ast = parse(_ns("""
        facet V(items: String) => (output: String)
        workflow Test(idx: Long) andThen {
            s = V(items = "test")
            s2 = V(items = s.output[$.idx])
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_nested_collections_validation(self, validator):
        """Nested collections with valid references should pass."""
        ast = parse(_ns("""
        facet V(items: String) => (output: Long)
        workflow Test(x: Long) andThen {
            s = V(items = "test")
            s2 = V(items = [[$.x], [s.output]])
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestExpressionTypeChecking:
    """Test expression type checking in the validator."""

    def test_string_plus_int_error(self, validator):
        """String + Int should produce type error."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = "hello" + 1)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_int_plus_string_error(self, validator):
        """Int + String should produce type error."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = 1 + "hello")
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_int_plus_int_valid(self, validator):
        """Int + Int should be valid."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = 1 + 2)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_int_multiply_int_valid(self, validator):
        """Int * Int should be valid."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = 3 * 4)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_boolean_arithmetic_error(self, validator):
        """Boolean in arithmetic should produce type error."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = true - 1)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_ref_plus_int_passes(self, validator):
        """Reference + Int should pass (Unknown type, no error)."""
        ast = parse(_ns("""
        facet V(x: Long) => (output: Long)
        workflow Test(a: Long) andThen {
            s = V(x = $.a + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_ref_plus_int_passes(self, validator):
        """Step reference + Int should pass."""
        ast = parse(_ns("""
        facet V(x: Long) => (output: Long)
        workflow Test(a: Long) andThen {
            s1 = V(x = $.a)
            s2 = V(x = s1.output + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_string_concat_valid(self, validator):
        """String ++ String is always valid (concat, not arithmetic)."""
        ast = parse(_ns("""
        facet V(x: String)
        workflow Test() andThen {
            s = V(x = "hello" ++ " " ++ "world")
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_nested_binary_type_error(self, validator):
        """Nested binary with type error should be caught."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = 1 + 2 * "bad")
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) for e in result.errors)

    def test_string_multiply_error(self, validator):
        """String * Int should produce type error."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = "abc" * 3)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) for e in result.errors)


class TestUnaryExprValidation:
    """Test unary expression type checking in the validator."""

    def test_negate_int_valid(self, validator):
        """Negating Int should be valid."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = -5)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_negate_float_valid(self, validator):
        """Negating Double should be valid."""
        ast = parse(_ns("""
        facet V(x: Double)
        workflow Test() andThen {
            s = V(x = -3.14)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_negate_ref_valid(self, validator):
        """Negating a reference should be valid (Unknown type passes)."""
        ast = parse(_ns("""
        facet V(x: Long) => (output: Long)
        workflow Test(a: Long) andThen {
            s = V(x = -$.a)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_negate_string_error(self, validator):
        """Negating a String should produce type error."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = -"hello")
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("negate" in str(e) and "String" in str(e) for e in result.errors)

    def test_negate_boolean_error(self, validator):
        """Negating a Boolean should produce type error."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = -true)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("negate" in str(e) and "Boolean" in str(e) for e in result.errors)

    def test_double_negation_valid(self, validator):
        """Double negation of Int should be valid."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = --5)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_negate_in_binary_valid(self, validator):
        """Negation inside binary expression should be valid."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test() andThen {
            s = V(x = 10 + -5)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestStepBodyValidation:
    """Test yield target validation inside inline step bodies."""

    def test_valid_inner_yield_targets_step_call(self, validator):
        """Inner yield targeting the step's call facet should pass."""
        ast = parse("""
        namespace test {
            event facet Inner(x: String) => (y: String)
            event facet Outer(a: String) => (b: String)

            workflow Main(x: String) => (result: String) andThen {
                s = Outer(a = $.x) andThen {
                    i = Inner(x = $.x)
                    yield Outer(b = i.y)
                }
                yield Main(result = s.b)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_invalid_inner_yield_target(self, validator):
        """Inner yield targeting a random name should fail."""
        ast = parse("""
        namespace test {
            event facet Inner(x: String) => (y: String)
            event facet Outer(a: String) => (b: String)

            workflow Main(x: String) => (result: String) andThen {
                s = Outer(a = $.x) andThen {
                    i = Inner(x = $.x)
                    yield BadName(b = i.y)
                }
                yield Main(result = s.b)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("BadName" in str(e) for e in result.errors)


class TestWorkflowAsStep:
    """Test that workflows can be called as steps inside andThen blocks."""

    def test_workflow_calls_workflow(self, validator):
        """A workflow referencing another workflow as a step should validate."""
        ast = parse(_ns("""
        namespace test {
            event facet DoWork(input: String) => (output: String)

            workflow Inner(x: String) => (y: String) andThen {
                w = DoWork(input = $.x)
                yield Inner(y = w.output)
            }

            workflow Outer(x: String) => (result: String) andThen {
                inner = Inner(x = $.x)
                yield Outer(result = inner.y)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_workflow_calls_workflow_cross_namespace(self, validator):
        """A workflow calling a workflow in another namespace should validate."""
        ast = parse("""
        namespace inner {
            event facet DoWork(input: String) => (output: String)

            workflow Process(x: String) => (y: String) andThen {
                w = DoWork(input = $.x)
                yield Process(y = w.output)
            }
        }

        namespace outer {
            use inner

            workflow Main(x: String) => (result: String) andThen {
                p = Process(x = $.x)
                yield Main(result = p.y)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestImplicitValidation:
    """Test validation of implicit declarations."""

    def test_valid_implicit(self, validator):
        """Valid implicit referencing a known facet with correct params."""
        ast = parse("""
        namespace ns {
            facet Retry(maxAttempts: Int)
            implicit retryDefaults = Retry(maxAttempts = 5)
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_implicit_unknown_param(self, validator):
        """Implicit passes an arg not in the target facet's params."""
        ast = parse("""
        namespace ns {
            facet Retry(maxAttempts: Int)
            implicit retryDefaults = Retry(unknownParam = 5)
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("unknownParam" in str(e) for e in result.errors)

    def test_implicit_valid_with_multiple_params(self, validator):
        """Implicit providing multiple valid params."""
        ast = parse("""
        namespace ns {
            facet Config(timeout: Int, retries: Int)
            implicit defaults = Config(timeout = 30, retries = 3)
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestComparisonBooleanValidation:
    """Test validation of comparison and boolean operators."""

    def test_boolean_and_with_non_boolean_error(self, validator):
        """&& with non-Boolean operand should produce type error."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test() andThen {
            s = V(x = 1 && true)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("'&&' requires Boolean" in str(e) for e in result.errors)

    def test_boolean_or_with_non_boolean_error(self, validator):
        """|| with non-Boolean operand should produce type error."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test() andThen {
            s = V(x = true || "yes")
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("'||' requires Boolean" in str(e) for e in result.errors)

    def test_ordered_comparison_with_boolean_error(self, validator):
        """Ordered comparison (> < >= <=) with Boolean should produce error."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test() andThen {
            s = V(x = true > false)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("ordered comparison" in str(e) and "Boolean" in str(e) for e in result.errors)

    def test_not_with_non_boolean_error(self, validator):
        """! with non-Boolean operand should produce type error."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test() andThen {
            s = V(x = !5)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("'!' requires Boolean" in str(e) for e in result.errors)

    def test_equality_with_any_types_valid(self, validator):
        """== can compare any types (Int == Int)."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test() andThen {
            s = V(x = 1 == 2)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_comparison_with_refs_valid(self, validator):
        """Comparison with references should pass (Unknown type)."""
        ast = parse(_ns("""
        facet V(x: Boolean) => (output: Int)
        workflow Test(a: Int) andThen {
            s1 = V(x = $.a > 0)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_not_with_ref_valid(self, validator):
        """! with reference should pass (Unknown type)."""
        ast = parse(_ns("""
        facet V(x: Boolean) => (output: Boolean)
        workflow Test(a: Boolean) andThen {
            s = V(x = !$.a)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_boolean_ops_valid(self, validator):
        """&& and || with Boolean operands should be valid."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test() andThen {
            s = V(x = true && false || true)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestWhenBlockValidation:
    """Test validation of andThen when blocks."""

    def test_valid_when_block(self, validator):
        """Valid when block with boolean conditions should pass."""
        ast = parse(_ns("""
        facet DoA(x: Int) => (value: String)
        facet DoFallback() => (value: String)
        workflow Test(count: Int) => (output: String) andThen when {
            case $.count > 10 => {
                a = DoA(x = $.count)
                yield Test(output = a.value)
            }
            case _ => {
                f = DoFallback()
                yield Test(output = f.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_multiple_defaults_error(self, validator):
        """When block with multiple default cases should error."""
        ast = parse(_ns("""
        facet DoA() => (value: String)
        facet DoB() => (value: String)
        workflow Test() => (output: String) andThen when {
            case _ => {
                a = DoA()
                yield Test(output = a.value)
            }
            case _ => {
                b = DoB()
                yield Test(output = b.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("at most one default" in str(e) for e in result.errors)

    def test_default_not_last_error(self, validator):
        """Default case not last should error."""
        ast = parse(_ns("""
        facet DoA() => (value: String)
        facet DoB(x: Int) => (value: String)
        workflow Test(count: Int) => (output: String) andThen when {
            case _ => {
                a = DoA()
                yield Test(output = a.value)
            }
            case $.count > 10 => {
                b = DoB(x = $.count)
                yield Test(output = b.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("last case" in str(e) for e in result.errors)

    def test_non_boolean_condition_error(self, validator):
        """Non-boolean condition should produce error."""
        ast = parse(_ns("""
        facet DoA() => (value: String)
        workflow Test() => (output: String) andThen when {
            case 42 => {
                a = DoA()
                yield Test(output = a.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean" in str(e) for e in result.errors)

    def test_missing_default_error(self, validator):
        """When block without a default case should error."""
        ast = parse(_ns("""
        facet DoA(x: Int) => (value: String)
        workflow Test(count: Int) => (output: String) andThen when {
            case $.count > 10 => {
                a = DoA(x = $.count)
                yield Test(output = a.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("must have a default case" in str(e) for e in result.errors)

    def test_valid_references_in_condition(self, validator):
        """Valid input references in when conditions should pass."""
        ast = parse(_ns("""
        facet DoA(x: Int) => (value: String)
        facet DoFallback() => (value: String)
        workflow Test(count: Int, active: Boolean) => (output: String) andThen when {
            case $.active && $.count > 0 => {
                a = DoA(x = $.count)
                yield Test(output = a.value)
            }
            case _ => {
                f = DoFallback()
                yield Test(output = f.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestCatchBlockValidation:
    """Test catch block validation."""

    def test_valid_catch_block(self, validator):
        """Valid catch block should pass validation."""
        ast = parse(_ns("""
        facet Transform(data: String) => (output: String)
        facet SafeDefault(reason: String) => (output: String)
        workflow Test(input: String) => (output: String) andThen {
            s = Transform(data = $.input) catch { fallback = SafeDefault(reason = $.input) }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_catch_when_missing_default(self, validator):
        """Catch when without default case should fail."""
        ast = parse(_ns("""
        facet F(x: String) => (out: String)
        facet G(x: String) => (out: String)
        workflow Test(input: String) => (output: String) andThen {
            s = F(x = $.input) catch when {
                case $.input == "a" => { r = G(x = $.input) }
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("default" in str(e).lower() for e in result.errors)

    def test_catch_when_multiple_defaults(self, validator):
        """Catch when with multiple defaults should fail."""
        ast = parse(_ns("""
        facet F(x: String) => (out: String)
        facet G(x: String) => (out: String)
        workflow Test(input: String) => (output: String) andThen {
            s = F(x = $.input) catch when {
                case _ => { r = G(x = $.input) }
                case _ => { r = G(x = $.input) }
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("at most one" in str(e).lower() for e in result.errors)

    def test_catch_when_non_boolean_condition(self, validator):
        """Catch when with non-boolean condition should fail."""
        ast = parse(_ns("""
        facet F(x: String) => (out: String)
        facet G(x: String) => (out: String)
        workflow Test(input: Int) => (output: String) andThen {
            s = F(x = $.input) catch when {
                case 42 => { r = G(x = $.input) }
                case _ => { r = G(x = $.input) }
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("boolean" in str(e).lower() for e in result.errors)

    def test_valid_workflow_catch(self, validator):
        """Workflow-level catch should pass validation."""
        ast = parse(_ns("""
        facet Build(service: String) => (image: String)
        facet Notify(service: String) => (status: String)
        workflow Deploy(service: String) => (status: String) andThen {
            build = Build(service = $.service)
        } catch { fallback = Notify(service = $.service) }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestParameterTypeInference:
    """Test that parameter types from signatures are used in type checking."""

    def test_string_param_arithmetic_error(self, validator):
        """$.string_param + 1 should error (String in arithmetic)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(text: String) andThen {
            s = V(x = $.text + 1)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_bool_param_arithmetic_error(self, validator):
        """$.bool_param + 1 should error (Boolean in arithmetic)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(flag: Boolean) andThen {
            s = V(x = $.flag + 1)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_int_param_arithmetic_valid(self, validator):
        """$.int_param + 1 should pass (Int + Int)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(count: Int) andThen {
            s = V(x = $.count + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_int_param_boolean_op_error(self, validator):
        """$.int_param && true should error (Int in boolean op)."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test(count: Int) andThen {
            s = V(x = $.count && true)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean operands" in str(e) and "Int" in str(e) for e in result.errors)

    def test_json_param_passes(self, validator):
        """$.json_param + 1 should pass (Json -> Unknown, no error)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(data: Json) andThen {
            s = V(x = $.data + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_ref_type_resolved(self, validator):
        """step.field type is resolved from facet returns (Long + Int = valid)."""
        ast = parse(_ns("""
        facet V(x: Long) => (output: Long)
        workflow Test(a: Long) andThen {
            s1 = V(x = $.a)
            s2 = V(x = s1.output + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_negate_string_param_error(self, validator):
        """-$.string_param should error (negate String)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(name: String) andThen {
            s = V(x = -$.name)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) for e in result.errors)

    def test_bool_param_ordered_comparison_error(self, validator):
        """$.bool_param > 0 should error (ordered comparison with Boolean)."""
        ast = parse(_ns("""
        facet V(x: Boolean)
        workflow Test(flag: Boolean) andThen {
            s = V(x = $.flag > 0)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("ordered comparison" in str(e) and "Boolean" in str(e) for e in result.errors)

    def test_double_param_arithmetic_valid(self, validator):
        """$.double_param * 100 should pass (Double * Int = Double)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(rate: Double) andThen {
            s = V(x = $.rate * 100)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_string_param_concat_valid(self, validator):
        """$.string_param ++ "suffix" should pass (concat always valid)."""
        ast = parse(_ns("""
        facet V(x: String)
        workflow Test(name: String) andThen {
            s = V(x = $.name ++ "_suffix")
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_typed_param_unknown(self, validator):
        """Schema-typed param should be Unknown (passes arithmetic)."""
        ast = parse("""
        namespace ns {
            schema Config { value: Int }
            facet V(x: Long)
            workflow Test(cfg: Config) andThen {
                s = V(x = $.cfg + 1)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_array_typed_param(self, validator):
        """Array-typed param should be Array (passes arithmetic)."""
        ast = parse(_ns("""
        facet V(x: Long)
        workflow Test(items: [Int]) andThen {
            s = V(x = $.items + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_when_string_condition_error(self, validator):
        """when { case $.string_param => ... } should error (non-Boolean condition)."""
        ast = parse(_ns("""
        facet DoA() => (value: String)
        facet DoFallback() => (value: String)
        workflow Test(name: String) => (output: String) andThen when {
            case $.name => {
                a = DoA()
                yield Test(output = a.value)
            }
            case _ => {
                f = DoFallback()
                yield Test(output = f.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean" in str(e) for e in result.errors)

    def test_when_bool_condition_valid(self, validator):
        """when { case $.bool_param => ... } should pass."""
        ast = parse(_ns("""
        facet DoA() => (value: String)
        facet DoFallback() => (value: String)
        workflow Test(active: Boolean) => (output: String) andThen when {
            case $.active => {
                a = DoA()
                yield Test(output = a.value)
            }
            case _ => {
                f = DoFallback()
                yield Test(output = f.value)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestStepReturnTypeInference:
    """Test that step return types from facet/schema declarations are used in type checking."""

    def test_step_string_plus_int_error(self, validator):
        """s1.name + 1 where name: String should error."""
        ast = parse(_ns("""
        facet GetName(id: Int) => (name: String)
        facet Process(x: Long)
        workflow Test(id: Int) andThen {
            s1 = GetName(id = $.id)
            s2 = Process(x = s1.name + 1)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_step_long_and_true_error(self, validator):
        """s1.count && true where count: Long should error."""
        ast = parse(_ns("""
        facet Count() => (count: Long)
        facet Check(x: Boolean)
        workflow Test() andThen {
            s1 = Count()
            s2 = Check(x = s1.count && true)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean operands" in str(e) and "Long" in str(e) for e in result.errors)

    def test_step_not_long_error(self, validator):
        """!s1.count where count: Long should error."""
        ast = parse(_ns("""
        facet Count() => (count: Long)
        facet Check(x: Boolean)
        workflow Test() andThen {
            s1 = Count()
            s2 = Check(x = !s1.count)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("'!'" in str(e) and "Long" in str(e) for e in result.errors)

    def test_step_bool_ordered_comparison_error(self, validator):
        """s1.flag > 0 where flag: Boolean should error."""
        ast = parse(_ns("""
        facet GetFlag() => (flag: Boolean)
        facet Process(x: Boolean)
        workflow Test() andThen {
            s1 = GetFlag()
            s2 = Process(x = s1.flag > 0)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("ordered comparison" in str(e) and "Boolean" in str(e) for e in result.errors)

    def test_step_negate_string_error(self, validator):
        """-s1.name where name: String should error."""
        ast = parse(_ns("""
        facet GetName() => (name: String)
        facet Process(x: Long)
        workflow Test() andThen {
            s1 = GetName()
            s2 = Process(x = -s1.name)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) for e in result.errors)

    def test_step_long_plus_int_valid(self, validator):
        """s1.count + 1 where count: Long should pass (Long + Int = Long)."""
        ast = parse(_ns("""
        facet Count() => (count: Long)
        facet Process(x: Long)
        workflow Test() andThen {
            s1 = Count()
            s2 = Process(x = s1.count + 1)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_double_plus_long_valid(self, validator):
        """s1.rate + s2.count where rate: Double, count: Long should pass (promotion)."""
        ast = parse(_ns("""
        facet GetRate() => (rate: Double)
        facet GetCount() => (count: Long)
        facet Process(x: Double)
        workflow Test() andThen {
            s1 = GetRate()
            s2 = GetCount()
            s3 = Process(x = s1.rate + s2.count)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_not_boolean_valid(self, validator):
        """!s1.enabled where enabled: Boolean should pass."""
        ast = parse(_ns("""
        facet GetEnabled() => (enabled: Boolean)
        facet Check(x: Boolean)
        workflow Test() andThen {
            s1 = GetEnabled()
            s2 = Check(x = !s1.enabled)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_string_equality_valid(self, validator):
        """s1.x == s2.y where both String should pass (comparison always valid)."""
        ast = parse(_ns("""
        facet GetA() => (x: String)
        facet GetB() => (y: String)
        facet Check(result: Boolean)
        workflow Test() andThen {
            s1 = GetA()
            s2 = GetB()
            s3 = Check(result = s1.x == s2.y)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_int_arithmetic_chain_valid(self, validator):
        """Chained step ref arithmetic should pass: s1.x + s2.y * 2."""
        ast = parse(_ns("""
        facet GetX() => (x: Int)
        facet GetY() => (y: Int)
        facet Process(result: Int)
        workflow Test() andThen {
            s1 = GetX()
            s2 = GetY()
            s3 = Process(result = s1.x + s2.y * 2)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_string_bool_op_error(self, validator):
        """s1.name || true where name: String should error."""
        ast = parse(_ns("""
        facet GetName() => (name: String)
        facet Check(x: Boolean)
        workflow Test() andThen {
            s1 = GetName()
            s2 = Check(x = s1.name || true)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean operands" in str(e) and "String" in str(e) for e in result.errors)

    def test_step_negate_boolean_error(self, validator):
        """-s1.flag where flag: Boolean should error (arithmetic negation)."""
        ast = parse(_ns("""
        facet GetFlag() => (flag: Boolean)
        facet Process(x: Long)
        workflow Test() andThen {
            s1 = GetFlag()
            s2 = Process(x = -s1.flag)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean" in str(e) for e in result.errors)

    def test_schema_field_type_resolved(self, validator):
        """Schema field types should be resolved for step refs."""
        ast = parse("""
        namespace ns {
            schema Config { name: String, count: Long }
            facet Process(x: Long)
            workflow Test() andThen {
                cfg = Config(name = "test", count = 42)
                s = Process(x = cfg.name + 1)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_schema_field_long_valid(self, validator):
        """Schema Long field in arithmetic should pass."""
        ast = parse("""
        namespace ns {
            schema Config { count: Long }
            facet Process(x: Long)
            workflow Test() andThen {
                cfg = Config(count = 42)
                s = Process(x = cfg.count + 1)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_unknown_facet_step_ref_passes(self, validator):
        """Step ref to unknown facet returns Unknown — no type error."""
        ast = parse(_ns("""
        facet Process(x: Long)
        workflow Test() andThen {
            s1 = ExternalFacet(id = 1)
            s2 = Process(x = s1.output + 1)
        }
        """))
        result = validator.validate(ast)
        # Should have no arithmetic type errors (Unknown + Int = Unknown, which passes)
        assert not any("arithmetic" in str(e) for e in result.errors)

    def test_yield_step_ref_type_checked(self, validator):
        """Step ref type checking also works in yield arguments."""
        ast = parse(_ns("""
        facet GetName() => (name: String)
        workflow Test() => (output: Long) andThen {
            s1 = GetName()
            yield Test(output = s1.name + 1)
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_step_ref_concat_valid(self, validator):
        """s1.name ++ s2.name where both String should pass (concat always valid)."""
        ast = parse(_ns("""
        facet GetFirst() => (name: String)
        facet GetLast() => (name: String)
        facet Display(label: String)
        workflow Test() andThen {
            s1 = GetFirst()
            s2 = GetLast()
            s3 = Display(label = s1.name ++ " " ++ s2.name)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_step_double_multiply_valid(self, validator):
        """s1.rate * 100 where rate: Double should pass."""
        ast = parse(_ns("""
        facet GetRate() => (rate: Double)
        facet Process(x: Double)
        workflow Test() andThen {
            s1 = GetRate()
            s2 = Process(x = s1.rate * 100)
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]


class TestWhenBlockStepReturnTypes:
    """Test step return type inference in when block conditions (Phase 2 gaps)."""

    def test_when_condition_step_string_plus_int_error(self, validator):
        """Gap 1: case s1.name + 1 where name: String should catch type error."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (risk_level: String, score: Int)
        facet HandleHigh(x: Int)
        workflow Test(id: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.risk_level + 1 => {
                h = HandleHigh(x = 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_when_condition_step_int_equals_valid(self, validator):
        """Gap 1: case s1.score == 10 where score: Int should pass (Boolean)."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (risk_level: String, score: Int)
        facet HandleHigh(x: Int)
        workflow Test(id: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.score == 10 => {
                h = HandleHigh(x = s1.score)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_when_condition_step_string_eq_valid(self, validator):
        """Gap 1: case s1.risk_level == "critical" should pass."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (risk_level: String, score: Int)
        facet HandleCritical(x: Int)
        workflow Test(id: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.risk_level == "critical" => {
                h = HandleCritical(x = 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_when_condition_step_bool_arithmetic_error(self, validator):
        """Gap 1: case s1.flag + 1 where flag: Boolean should error."""
        ast = parse(_ns("""
        event facet GetFlag() => (flag: Boolean)
        facet Handle(x: Int)
        workflow Test() andThen {
            s1 = GetFlag()
        } andThen when {
            case s1.flag + 1 => {
                h = Handle(x = 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean" in str(e) for e in result.errors)

    def test_cross_block_step_visible_in_when(self, validator):
        """Gap 2: when block can see steps from prior regular andThen blocks."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (risk_level: String, score: Int)
        facet HandleCritical(level: String)
        facet HandleNormal(level: String)
        workflow Test(id: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.risk_level == "critical" => {
                h = HandleCritical(level = s1.risk_level)
            }
            case _ => {
                n = HandleNormal(level = s1.risk_level)
            }
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_cross_block_step_type_checked_in_when_body(self, validator):
        """Gap 2: step type info flows into when body for type checking."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (risk_level: String, score: Int)
        facet Process(x: Int)
        workflow Test(id: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.score > 5 => {
                p = Process(x = s1.risk_level + 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_when_condition_undefined_step_error(self, validator):
        """Gap 3: referencing undefined step in when condition should error."""
        ast = parse(_ns("""
        facet Handle(x: Int)
        workflow Test(id: Int) andThen when {
            case s1.score == 10 => {
                h = Handle(x = 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("undefined step" in str(e) and "s1" in str(e) for e in result.errors)

    def test_when_condition_invalid_step_attr_error(self, validator):
        """Gap 3: referencing invalid attribute on step in when condition should error."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (risk_level: String, score: Int)
        facet Handle(x: Int)
        workflow Test(id: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.nonexistent == "x" => {
                h = Handle(x = 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Invalid attribute 'nonexistent'" in str(e) for e in result.errors)

    def test_when_condition_step_and_input_mixed(self, validator):
        """Conditions can mix step refs and input refs."""
        ast = parse(_ns("""
        event facet GetRisk(id: Int) => (score: Int)
        facet Handle(x: Int)
        workflow Test(id: Int, threshold: Int) andThen {
            s1 = GetRisk(id = $.id)
        } andThen when {
            case s1.score > $.threshold => {
                h = Handle(x = s1.score)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_when_no_prior_blocks_step_ref_still_unknown(self, validator):
        """Without prior blocks, step refs in when conditions reference undefined steps."""
        ast = parse(_ns("""
        facet Handle(x: Int)
        workflow Test(id: Int) andThen when {
            case $.id == 1 => {
                h = Handle(x = 1)
            }
            case _ => {}
        }
        """))
        result = validator.validate(ast)
        # Input ref $.id is valid, no step refs — should pass
        assert result.is_valid, [str(e) for e in result.errors]

    def test_when_condition_schema_field_type_resolved(self, validator):
        """Gap 1+2: schema field types resolve through cross-block when conditions."""
        ast = parse("""
        namespace ns {
            schema Config { name: String, count: Long }
            facet Handle(x: Int)
            workflow Test() andThen {
                cfg = Config(name = "test", count = 42)
            } andThen when {
                case cfg.name + 1 => {
                    h = Handle(x = 1)
                }
                case _ => {}
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("String" in str(e) and "arithmetic" in str(e) for e in result.errors)


class TestSchemaReturnTypeInference:
    """Test Phase 3: schema-typed return fields resolve to schema names."""

    def test_schema_return_resolves_to_schema_name(self, validator):
        """Schema-typed return should resolve to schema name, not Unknown."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int, label: String }
            event facet Analyze(input: String) => (result: Result)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                b = Analyze(input = a.result ++ "ok")
            }
        }
        """)
        result = validator.validate(ast)
        # String concatenation with schema type should be fine (++ accepts any type)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_return_plus_int_errors(self, validator):
        """Schema-typed step ref + Int should produce a type error."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int }
            event facet Analyze(input: String) => (result: Result)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                b = Analyze(input = a.result + 1)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("schema type" in str(e) and "arithmetic" in str(e) for e in result.errors)

    def test_schema_return_ordered_comparison_errors(self, validator):
        """Schema-typed step ref in ordered comparison should error."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int }
            event facet Analyze(input: String) => (result: Result)
            facet Report(flag: Boolean)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                r = Report(flag = a.result > 5)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any(
            "schema type" in str(e) and "ordered comparison" in str(e) for e in result.errors
        )

    def test_schema_return_equality_allowed(self, validator):
        """Schema-typed step ref in equality comparison (== / !=) should be allowed."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int }
            event facet Analyze(input: String) => (result: Result)
            facet Report(flag: Boolean)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                r = Report(flag = a.result == a.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_return_concat_allowed(self, validator):
        """Schema-typed step ref in string concatenation (++) should be allowed."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int }
            event facet Analyze(input: String) => (result: Result)
            event facet Log(message: String)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                l = Log(message = "result: " ++ a.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_return_boolean_op_errors(self, validator):
        """Schema-typed step ref in boolean op (&&) should error."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int }
            event facet Analyze(input: String) => (result: Result)
            facet Report(flag: Boolean)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                r = Report(flag = a.result && true)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("Boolean operands" in str(e) and "Result" in str(e) for e in result.errors)

    def test_qualified_schema_return_resolves(self, validator):
        """Fully-qualified schema type in return should resolve correctly."""
        ast = parse("""
        namespace data {
            schema Report { summary: String }
        }
        namespace ops {
            use data
            event facet Generate(input: String) => (report: data.Report)
            event facet Publish(content: String)
            workflow Run(x: String) andThen {
                g = Generate(input = $.x)
                p = Publish(content = g.report ++ " done")
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_schema_return_negation_errors(self, validator):
        """Negating a schema-typed step ref should error."""
        ast = parse("""
        namespace ns {
            schema Result { score: Int }
            event facet Analyze(input: String) => (result: Result)
            event facet Log(value: Int)
            workflow Test(x: String) andThen {
                a = Analyze(input = $.x)
                l = Log(value = -a.result)
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        assert any("schema type" in str(e) and "negate" in str(e) for e in result.errors)


class TestNestedSchemaFieldAccess:
    """Test nested field access through schema-typed step references."""

    def test_nested_field_resolves_type(self, validator):
        """step.result.count where result is schema with count: Int."""
        ast = parse("""
        namespace ns {
            schema Result { count: Int, label: String }
            event facet Analyze(input: String) => (result: Result)
            event facet Report(value: Int)
            workflow Test(x: String = "a") andThen {
                a = Analyze(input = $.x)
                r = Report(value = a.result.count)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_nested_field_type_error(self, validator):
        """step.result.count + 'hello' where count is Int → type error."""
        ast = parse("""
        namespace ns {
            schema Result { count: Int, label: String }
            event facet Analyze(input: String) => (result: Result)
            event facet Report(value: String)
            workflow Test(x: String = "a") andThen {
                a = Analyze(input = $.x)
                r = Report(value = a.result.count + "hello")
            }
        }
        """)
        result = validator.validate(ast)
        assert not result.is_valid
        errors_str = " ".join(str(e) for e in result.errors)
        assert "Int" in errors_str or "String" in errors_str

    def test_nested_field_nonexistent_warns(self, validator):
        """step.result.nonexistent in arithmetic triggers warning."""
        ast = parse("""
        namespace ns {
            schema Result { count: Int }
            event facet Analyze(input: String) => (result: Result)
            event facet Report(value: Int)
            workflow Test(x: String = "a") andThen {
                a = Analyze(input = $.x)
                r = Report(value = a.result.nonexistent + 1)
            }
        }
        """)
        result = validator.validate(ast)
        assert any("nonexistent" in str(w) and "not found" in str(w) for w in result.warnings)

    def test_deeply_nested_field_resolves(self, validator):
        """step.result.nested.value where nested is also a schema."""
        ast = parse("""
        namespace ns {
            schema Inner { value: Int }
            schema Outer { nested: Inner }
            event facet Analyze(input: String) => (result: Outer)
            event facet Report(value: Int)
            workflow Test(x: String = "a") andThen {
                a = Analyze(input = $.x)
                r = Report(value = a.result.nested.value)
            }
        }
        """)
        result = validator.validate(ast)
        assert result.is_valid, [str(e) for e in result.errors]

    def test_unknown_intermediate_returns_unknown(self, validator):
        """step.unknown_field.whatever triggers invalid attribute error."""
        ast = parse("""
        namespace ns {
            event facet Analyze(input: String) => (result: String)
            event facet Report(value: String)
            workflow Test(x: String = "a") andThen {
                a = Analyze(input = $.x)
                r = Report(value = a.nonexistent.whatever ++ "ok")
            }
        }
        """)
        result = validator.validate(ast)
        # Reference validation catches invalid first-level attribute
        assert any("nonexistent" in str(e) and "Invalid attribute" in str(e) for e in result.errors)
