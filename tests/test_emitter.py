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

"""Tests for AFL JSON emitter."""

import json

import pytest

from afl import parse
from afl.emitter import JSONEmitter, emit_dict, emit_json


def _decls_by_type(data, type_name):
    """Return all declarations of a given type from a program or namespace dict."""
    return [d for d in data.get("declarations", []) if d.get("type") == type_name]


def _first_decl(data, type_name):
    """Return the first declaration of a given type, or None."""
    decls = _decls_by_type(data, type_name)
    return decls[0] if decls else None


@pytest.fixture
def emitter():
    """Create an emitter instance."""
    return JSONEmitter(include_locations=False)


class TestBasicEmission:
    """Test basic JSON emission."""

    def test_empty_program(self, emitter):
        """Empty program emits minimal JSON."""
        ast = parse("")
        data = emitter.emit_dict(ast)
        assert data["type"] == "Program"

    def test_simple_facet(self, emitter):
        """Simple facet emits correct structure."""
        ast = parse("facet User(name: String)")
        data = emitter.emit_dict(ast)

        facets = _decls_by_type(data, "FacetDecl")
        assert len(facets) == 1
        facet = facets[0]
        assert facet["type"] == "FacetDecl"
        assert facet["name"] == "User"
        assert facet["params"] == [{"name": "name", "type": "String"}]

    def test_facet_with_return(self, emitter):
        """Facet with return clause."""
        ast = parse("facet Transform(input: String) => (output: String)")
        data = emitter.emit_dict(ast)

        facet = _first_decl(data, "FacetDecl")
        assert facet["returns"] == [{"name": "output", "type": "String"}]

    def test_event_facet(self, emitter):
        """Event facet emits correct type."""
        ast = parse("event facet Process(input: String) => (output: String)")
        data = emitter.emit_dict(ast)

        efs = _decls_by_type(data, "EventFacetDecl")
        assert len(efs) == 1
        ef = efs[0]
        assert ef["type"] == "EventFacetDecl"
        assert ef["name"] == "Process"

    def test_workflow(self, emitter):
        """Workflow emits correct type."""
        ast = parse("workflow Main(input: String) => (output: String)")
        data = emitter.emit_dict(ast)

        wfs = _decls_by_type(data, "WorkflowDecl")
        assert len(wfs) == 1
        wf = wfs[0]
        assert wf["type"] == "WorkflowDecl"
        assert wf["name"] == "Main"


class TestWorkflowBody:
    """Test workflow body emission."""

    def test_workflow_with_steps(self, emitter):
        """Workflow with andThen block."""
        ast = parse("""
        workflow Test(input: String) => (output: String) andThen {
            step1 = Process(value = $.input)
            step2 = Transform(data = step1.result)
            yield Test(output = step2.value)
        }
        """)
        data = emitter.emit_dict(ast)

        wf = _first_decl(data, "WorkflowDecl")
        body = wf["body"]
        assert body["type"] == "AndThenBlock"
        assert len(body["steps"]) == 2

        # Check first step
        step1 = body["steps"][0]
        assert step1["type"] == "StepStmt"
        assert step1["name"] == "step1"
        assert step1["call"]["target"] == "Process"

        # Check yield
        assert body["yield"]["type"] == "YieldStmt"
        assert body["yield"]["call"]["target"] == "Test"

    def test_foreach(self, emitter):
        """Workflow with foreach."""
        ast = parse("""
        workflow ProcessAll(items: Json) => (results: Json) andThen foreach item in $.items {
            result = Process(data = item.value)
            yield ProcessAll(results = result.output)
        }
        """)
        data = emitter.emit_dict(ast)

        body = _first_decl(data, "WorkflowDecl")["body"]
        assert body["foreach"]["type"] == "ForeachClause"
        assert body["foreach"]["variable"] == "item"
        assert body["foreach"]["iterable"] == {"type": "InputRef", "path": ["items"]}


class TestScriptBlockEmission:
    """Test script block emission."""

    def test_script_block_simple(self, emitter):
        """Script block emitted as pre_script."""
        ast = parse('facet Test() script "x = 1"')
        data = emitter.emit_dict(ast)

        f = _first_decl(data, "FacetDecl")
        pre = f["pre_script"]
        assert pre["type"] == "ScriptBlock"
        assert pre["language"] == "python"
        assert pre["code"] == "x = 1"
        assert "body" not in f

    def test_script_block_includes_id(self, emitter):
        """Script block should have an id."""
        ast = parse('facet Test() script "pass"')
        data = emitter.emit_dict(ast)

        pre = _first_decl(data, "FacetDecl")["pre_script"]
        assert "id" in pre

    def test_script_block_event_facet(self, emitter):
        """Script block in event facet as pre_script."""
        ast = parse('event facet Test() script "result = {}"')
        data = emitter.emit_dict(ast)

        pre = _first_decl(data, "EventFacetDecl")["pre_script"]
        assert pre["type"] == "ScriptBlock"

    def test_pre_script_with_andthen_emission(self, emitter):
        """Pre-script with andThen block both emitted."""
        ast = parse('facet F() script "pre" andThen { s = G() }')
        data = emitter.emit_dict(ast)

        f = _first_decl(data, "FacetDecl")
        assert "pre_script" in f
        assert f["pre_script"]["type"] == "ScriptBlock"
        assert f["pre_script"]["code"] == "pre"
        assert "body" in f
        body = f["body"]
        assert body["type"] == "AndThenBlock"

    def test_andthen_script_emission(self, emitter):
        """andThen script block emitted with script key."""
        ast = parse('facet F() andThen script "y = 2"')
        data = emitter.emit_dict(ast)

        f = _first_decl(data, "FacetDecl")
        body = f["body"]
        assert body["type"] == "AndThenBlock"
        assert "script" in body
        assert body["script"]["type"] == "ScriptBlock"
        assert body["script"]["code"] == "y = 2"
        assert "steps" not in body

    def test_mixed_body_emission(self, emitter):
        """Mixed regular andThen + andThen script emission."""
        ast = parse('facet F() andThen { s = G() } andThen script "y = 2"')
        data = emitter.emit_dict(ast)

        f = _first_decl(data, "FacetDecl")
        body = f["body"]
        assert isinstance(body, list)
        assert len(body) == 2
        assert "steps" in body[0]
        assert "script" in body[1]


class TestPromptBlockEmission:
    """Test prompt block emission."""

    def test_prompt_block_template_only(self, emitter):
        """Prompt block with only template."""
        ast = parse('event facet Test() prompt { template "hello" }')
        data = emitter.emit_dict(ast)

        ef = _first_decl(data, "EventFacetDecl")
        body = ef["body"]
        assert body["type"] == "PromptBlock"
        assert body["template"] == "hello"
        assert "system" not in body
        assert "model" not in body

    def test_prompt_block_all_directives(self, emitter):
        """Prompt block with all directives."""
        source = '''event facet Test()
prompt {
    system "sys"
    template "tmpl"
    model "model-id"
}'''
        ast = parse(source)
        data = emitter.emit_dict(ast)

        body = _first_decl(data, "EventFacetDecl")["body"]
        assert body["type"] == "PromptBlock"
        assert body["system"] == "sys"
        assert body["template"] == "tmpl"
        assert body["model"] == "model-id"

    def test_prompt_block_includes_id(self, emitter):
        """Prompt block should have an id."""
        ast = parse('event facet Test() prompt { template "x" }')
        data = emitter.emit_dict(ast)

        body = _first_decl(data, "EventFacetDecl")["body"]
        assert "id" in body


class TestReferences:
    """Test reference emission."""

    def test_input_ref(self, emitter):
        """Input reference ($.field)."""
        ast = parse("""
        workflow Test(input: String) andThen {
            step = Process(value = $.input)
        }
        """)
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]["call"]["args"][0]
        assert arg["value"] == {"type": "InputRef", "path": ["input"]}

    def test_step_ref(self, emitter):
        """Step reference (step.field)."""
        ast = parse("""
        workflow Test(input: String) andThen {
            step1 = Process(value = $.input)
            step2 = Transform(data = step1.output)
        }
        """)
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "WorkflowDecl")["body"]["steps"][1]["call"]["args"][0]
        assert arg["value"] == {"type": "StepRef", "path": ["step1", "output"]}

    def test_nested_ref(self, emitter):
        """Nested reference path."""
        ast = parse("""
        workflow Test(data: Json) andThen {
            step = Process(value = $.data.nested.field)
        }
        """)
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]["call"]["args"][0]
        assert arg["value"] == {"type": "InputRef", "path": ["data", "nested", "field"]}


class TestLiterals:
    """Test literal emission."""

    def test_string_literal(self, emitter):
        """String literal."""
        ast = parse('implicit msg = Message(text = "hello")')
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "ImplicitDecl")["call"]["args"][0]
        assert arg["value"] == {"type": "String", "value": "hello"}

    def test_integer_literal(self, emitter):
        """Integer literal."""
        ast = parse("implicit count = Counter(value = 42)")
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "ImplicitDecl")["call"]["args"][0]
        assert arg["value"] == {"type": "Int", "value": 42}

    def test_boolean_literal(self, emitter):
        """Boolean literals."""
        ast = parse("implicit flag = Config(enabled = true, disabled = false)")
        data = emitter.emit_dict(ast)

        args = _first_decl(data, "ImplicitDecl")["call"]["args"]
        assert args[0]["value"] == {"type": "Boolean", "value": True}
        assert args[1]["value"] == {"type": "Boolean", "value": False}

    def test_null_literal(self, emitter):
        """Null literal."""
        ast = parse("implicit opt = Optional(value = null)")
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "ImplicitDecl")["call"]["args"][0]
        assert arg["value"] == {"type": "Null"}

    def test_float_literal(self, emitter):
        """Float literal."""
        ast = parse("implicit pi = Circle(radius = 3.14)")
        data = emitter.emit_dict(ast)

        arg = _first_decl(data, "ImplicitDecl")["call"]["args"][0]
        assert arg["value"] == {"type": "Double", "value": 3.14}

    def test_float_default(self, emitter):
        """Float default value emitted correctly."""
        ast = parse("facet Search(max_distance: Double = 10.5)")
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param == {
            "name": "max_distance",
            "type": "Double",
            "default": {"type": "Double", "value": 10.5},
        }


class TestMixins:
    """Test mixin emission."""

    def test_mixin_in_signature(self, emitter):
        """Mixin in facet signature."""
        ast = parse("facet Job(input: String) with Retry(maxAttempts = 3)")
        data = emitter.emit_dict(ast)

        mixins = _first_decl(data, "FacetDecl")["mixins"]
        assert len(mixins) == 1
        assert mixins[0]["target"] == "Retry"
        assert mixins[0]["args"] == [{"name": "maxAttempts", "value": {"type": "Int", "value": 3}}]

    def test_mixin_call_with_alias(self, emitter):
        """Mixin call with alias."""
        ast = parse("""
        workflow Test(input: String) andThen {
            job = RunJob(input = $.input) with User(name = "test") as user
        }
        """)
        data = emitter.emit_dict(ast)

        mixins = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]["call"]["mixins"]
        assert len(mixins) == 1
        assert mixins[0]["target"] == "User"
        assert mixins[0]["alias"] == "user"


class TestNamespaces:
    """Test namespace emission."""

    def test_namespace(self, emitter):
        """Namespace with contents."""
        ast = parse("""
        namespace team.data.processing {
            uses team.common.utils
            uses team.other

            facet Data(value: String)
            workflow Process(input: String) => (output: String)
        }
        """)
        data = emitter.emit_dict(ast)

        ns = _first_decl(data, "Namespace")
        assert ns["type"] == "Namespace"
        assert ns["name"] == "team.data.processing"
        assert ns["uses"] == ["team.common.utils", "team.other"]
        assert len(_decls_by_type(ns, "FacetDecl")) == 1
        assert len(_decls_by_type(ns, "WorkflowDecl")) == 1


class TestImplicits:
    """Test implicit declaration emission."""

    def test_implicit(self, emitter):
        """Implicit declaration."""
        ast = parse('implicit user = User(name = "system", email = "sys@test.com")')
        data = emitter.emit_dict(ast)

        impl = _first_decl(data, "ImplicitDecl")
        assert impl["type"] == "ImplicitDecl"
        assert impl["name"] == "user"
        assert impl["call"]["target"] == "User"


class TestLocations:
    """Test source location emission."""

    def test_locations_included(self):
        """Locations included by default."""
        emitter = JSONEmitter(include_locations=True)
        ast = parse("facet Test()")
        data = emitter.emit_dict(ast)

        facet = _first_decl(data, "FacetDecl")
        assert "location" in facet
        loc = facet["location"]
        assert "line" in loc
        assert "column" in loc

    def test_locations_excluded(self):
        """Locations can be excluded."""
        emitter = JSONEmitter(include_locations=False)
        ast = parse("facet Test()")
        data = emitter.emit_dict(ast)

        assert "location" not in _first_decl(data, "FacetDecl")


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_emit_json(self):
        """Test emit_json function."""
        ast = parse("facet Test()")
        result = emit_json(ast)

        assert isinstance(result, str)
        data = json.loads(result)
        assert data["type"] == "Program"

    def test_emit_dict(self):
        """Test emit_dict function."""
        ast = parse("facet Test()")
        result = emit_dict(ast)

        assert isinstance(result, dict)
        assert result["type"] == "Program"

    def test_compact_json(self):
        """Test compact JSON output."""
        ast = parse("facet Test()")
        result = emit_json(ast, indent=None)

        assert "\n" not in result


class TestComplexExample:
    """Test complex real-world example."""

    def test_full_workflow(self, emitter):
        """Full workflow with all features."""
        ast = parse("""
        namespace team.email {
            uses team.common.types

            facet EmailConfig(smtpHost: String, smtpPort: Int)

            event facet SendEmail(to: String, subject: String, body: String) => (messageId: String)

            implicit config = EmailConfig(smtpHost = "smtp.example.com", smtpPort = 587)

            workflow BulkSend(recipients: Json, template: String) => (results: Json) with Retry(maxAttempts = 3) andThen foreach recipient in $.recipients {
                email = SendEmail(
                    to = recipient.email,
                    subject = "Hello",
                    body = $.template
                ) with Config() as cfg
                yield BulkSend(results = email.messageId)
            }
        }
        """)
        data = emitter.emit_dict(ast)

        # Verify structure
        ns = _first_decl(data, "Namespace")
        assert ns["name"] == "team.email"
        assert ns["uses"] == ["team.common.types"]
        assert len(_decls_by_type(ns, "FacetDecl")) == 1
        assert len(_decls_by_type(ns, "EventFacetDecl")) == 1
        assert len(_decls_by_type(ns, "ImplicitDecl")) == 1
        assert len(_decls_by_type(ns, "WorkflowDecl")) == 1

        # Verify workflow
        wf = _first_decl(ns, "WorkflowDecl")
        assert wf["name"] == "BulkSend"
        assert wf["mixins"][0]["target"] == "Retry"
        assert wf["body"]["foreach"]["variable"] == "recipient"


class TestDefaultParameterValues:
    """Test default parameter value emission."""

    def test_string_default(self, emitter):
        """String default value emitted correctly."""
        ast = parse('facet Greeting(message: String = "hello")')
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param == {
            "name": "message",
            "type": "String",
            "default": {"type": "String", "value": "hello"},
        }

    def test_integer_default(self, emitter):
        """Integer default value emitted correctly."""
        ast = parse("facet Config(retries: Int = 3)")
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param == {"name": "retries", "type": "Int", "default": {"type": "Int", "value": 3}}

    def test_boolean_default(self, emitter):
        """Boolean default value emitted correctly."""
        ast = parse("facet Config(verbose: Boolean = true)")
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param == {
            "name": "verbose",
            "type": "Boolean",
            "default": {"type": "Boolean", "value": True},
        }

    def test_null_default(self, emitter):
        """Null default value emitted correctly."""
        ast = parse("facet Config(extra: Json = null)")
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param == {"name": "extra", "type": "Json", "default": {"type": "Null"}}

    def test_no_default_omits_key(self, emitter):
        """Parameters without defaults omit the default key."""
        ast = parse("facet Data(value: String)")
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert "default" not in param

    def test_mixed_defaults(self, emitter):
        """Mix of params with and without defaults."""
        ast = parse("facet Mixed(required: String, optional: Int = 42)")
        data = emitter.emit_dict(ast)
        params = _first_decl(data, "FacetDecl")["params"]
        assert "default" not in params[0]
        assert params[1]["default"] == {"type": "Int", "value": 42}

    def test_workflow_defaults_roundtrip(self, emitter):
        """Workflow params and returns with defaults round-trip correctly."""
        ast = parse('workflow MyFlow(input: String = "hello") => (output: String = "world")')
        data = emitter.emit_dict(ast)
        wf = _first_decl(data, "WorkflowDecl")
        assert wf["params"][0]["default"] == {"type": "String", "value": "hello"}
        assert wf["returns"][0]["default"] == {"type": "String", "value": "world"}

    def test_default_in_json_output(self):
        """Default values survive JSON serialization."""
        ast = parse("facet Config(retries: Int = 3)")
        json_str = emit_json(ast, include_locations=False)
        data = json.loads(json_str)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param["default"] == {"type": "Int", "value": 3}


class TestJSONValidity:
    """Test that output is valid JSON."""

    def test_valid_json_output(self):
        """Output should be valid JSON."""
        ast = parse("""
        facet Test(value: String)
        workflow Main(input: String) => (output: String) andThen {
            step = Test(value = $.input)
            yield Main(output = step.value)
        }
        """)
        result = emit_json(ast)

        # Should not raise
        parsed = json.loads(result)
        assert parsed is not None

    def test_roundtrip_consistency(self):
        """Multiple emissions should produce same result."""
        ast = parse("facet Test(value: String)")

        result1 = emit_json(ast, include_locations=False)
        result2 = emit_json(ast, include_locations=False)

        assert result1 == result2


class TestSchemaEmission:
    """Test schema declaration JSON emission."""

    def test_basic_schema(self, emitter):
        """Schema emits correct JSON structure."""
        ast = parse("""
        namespace app {
            schema UserRequest {
                name: String,
                age: Int
            }
        }
        """)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        schemas = _decls_by_type(ns, "SchemaDecl")
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "SchemaDecl"
        assert schema["name"] == "UserRequest"
        assert len(schema["fields"]) == 2
        assert schema["fields"][0] == {"name": "name", "type": "String"}
        assert schema["fields"][1] == {"name": "age", "type": "Int"}

    def test_array_type_in_schema(self, emitter):
        """Array types emit correctly in schema fields."""
        ast = parse("""
        namespace app {
            schema Tagged {
                tags: [String]
            }
        }
        """)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        field = _first_decl(ns, "SchemaDecl")["fields"][0]
        assert field["name"] == "tags"
        assert field["type"] == {"type": "ArrayType", "elementType": "String"}

    def test_array_type_in_parameter(self, emitter):
        """Array types emit correctly in regular parameters."""
        ast = parse("facet Process(items: [String])")
        data = emitter.emit_dict(ast)
        param = _first_decl(data, "FacetDecl")["params"][0]
        assert param["name"] == "items"
        assert param["type"] == {"type": "ArrayType", "elementType": "String"}

    def test_nested_array_type(self, emitter):
        """Nested array types emit correctly."""
        ast = parse("""
        namespace app {
            schema Matrix {
                rows: [[Int]]
            }
        }
        """)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        field = _first_decl(ns, "SchemaDecl")["fields"][0]
        assert field["type"] == {
            "type": "ArrayType",
            "elementType": {"type": "ArrayType", "elementType": "Int"},
        }

    def test_schema_in_namespace(self, emitter):
        """Schema in namespace emits correctly."""
        ast = parse("""
        namespace app {
            schema Config {
                key: String
            }
        }
        """)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        schemas = _decls_by_type(ns, "SchemaDecl")
        assert len(schemas) == 1
        assert schemas[0]["name"] == "Config"

    def test_schema_reference_as_field_type(self, emitter):
        """Schema name as field type emits as string."""
        ast = parse("""
        namespace app {
            schema Address {
                city: String
            }
            schema Person {
                home: Address
            }
        }
        """)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        schemas = _decls_by_type(ns, "SchemaDecl")
        person_field = schemas[1]["fields"][0]
        assert person_field == {"name": "home", "type": "Address"}


class TestUsesDecl:
    """Test uses declaration emission."""

    def test_uses_decl_standalone(self):
        """UsesDecl emits with type and name in namespace context."""
        emitter = JSONEmitter(include_locations=True)
        ast = parse("""
        namespace app {
            use lib.utils
        }
        """)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        # Uses declarations are emitted as a list of strings in namespace
        assert "uses" in ns or "uses" not in ns  # uses are emitted as name strings
        # The namespace should contain the use reference
        assert ns["uses"] == ["lib.utils"]


class TestConcatExpr:
    """Test ConcatExpr emission via direct AST construction."""

    def test_concat_expr(self):
        from afl.ast import ConcatExpr, Literal

        emitter = JSONEmitter(include_locations=False)
        node = ConcatExpr(
            operands=[
                Literal(kind="string", value="hello"),
                Literal(kind="string", value=" world"),
            ]
        )
        data = emitter._convert(node)
        assert data["type"] == "ConcatExpr"
        assert len(data["operands"]) == 2
        assert data["operands"][0] == {"type": "String", "value": "hello"}
        assert data["operands"][1] == {"type": "String", "value": " world"}


class TestProvenance:
    """Test source provenance emission."""

    def test_file_provenance(self, tmp_path):
        from afl.parser import AFLParser
        from afl.source import CompilerInput

        afl_file = tmp_path / "test.afl"
        afl_file.write_text("facet Test()")

        from afl.loader import SourceLoader

        entry = SourceLoader.load_file(str(afl_file), is_library=False)
        ci = CompilerInput()
        ci.primary_sources.append(entry)

        parser = AFLParser()
        ast, registry = parser.parse_sources(ci)

        emitter = JSONEmitter(
            include_locations=True,
            include_provenance=True,
            source_registry=registry,
        )
        data = emitter.emit_dict(ast)
        loc = _first_decl(data, "FacetDecl")["location"]
        assert "provenance" in loc
        assert loc["provenance"]["type"] == "file"

    def test_mongodb_provenance(self):
        from afl.source import MongoDBOrigin

        emitter = JSONEmitter()
        origin = MongoDBOrigin(collection_id="col-1", display_name="MySource")
        result = emitter._provenance_to_dict(origin)
        assert result == {
            "type": "mongodb",
            "collectionId": "col-1",
            "displayName": "MySource",
        }

    def test_maven_provenance_without_classifier(self):
        from afl.source import MavenOrigin

        emitter = JSONEmitter()
        origin = MavenOrigin(group_id="com.example", artifact_id="lib", version="1.0")
        result = emitter._provenance_to_dict(origin)
        assert result == {
            "type": "maven",
            "groupId": "com.example",
            "artifactId": "lib",
            "version": "1.0",
        }
        assert "classifier" not in result

    def test_maven_provenance_with_classifier(self):
        from afl.source import MavenOrigin

        emitter = JSONEmitter()
        origin = MavenOrigin(
            group_id="com.example",
            artifact_id="lib",
            version="1.0",
            classifier="tests",
        )
        result = emitter._provenance_to_dict(origin)
        assert result["classifier"] == "tests"

    def test_unknown_provenance(self):
        emitter = JSONEmitter()
        # Pass an object that isn't any known origin type
        result = emitter._provenance_to_dict("not-a-real-origin")
        assert result == {"type": "unknown"}


class TestCompactJSON:
    """Test compact JSON output (indent=None)."""

    def test_compact_no_newlines(self):
        ast = parse("facet Test(value: String)")
        emitter = JSONEmitter(include_locations=False, indent=None)
        result = emitter.emit(ast)
        assert "\n" not in result
        # Should still be valid JSON
        data = json.loads(result)
        assert data["type"] == "Program"


class TestUnknownNodeType:
    """Test unknown node type raises ValueError."""

    def test_unknown_node_raises(self):
        emitter = JSONEmitter()
        with pytest.raises(ValueError, match="Unknown node type"):
            emitter._convert(object())


class TestBinaryExprEmission:
    """Test BinaryExpr JSON emission."""

    def test_simple_addition(self):
        """BinaryExpr emits correct JSON structure."""
        ast = parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input + 1)
            yield Test(output = s.input)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "BinaryExpr"
        assert arg_value["operator"] == "+"
        assert arg_value["left"] == {"type": "InputRef", "path": ["input"]}
        assert arg_value["right"] == {"type": "Int", "value": 1}

    def test_nested_binary(self):
        """Nested BinaryExpr emits correct tree."""
        ast = parse("""
        facet Value(input: Long, output: Long)
        workflow Test(a: Long, b: Long) => (output: Long) andThen {
            s = Value(input = $.a + $.b * 2)
            yield Test(output = s.input)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "BinaryExpr"
        assert arg_value["operator"] == "+"
        assert arg_value["right"]["type"] == "BinaryExpr"
        assert arg_value["right"]["operator"] == "*"

    def test_binary_with_step_ref(self):
        """BinaryExpr with step reference emits correctly."""
        ast = parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s1 = Value(input = $.input)
            s2 = Value(input = s1.input + 1)
            yield Test(output = s2.input)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step2 = _first_decl(data, "WorkflowDecl")["body"]["steps"][1]
        arg_value = step2["call"]["args"][0]["value"]
        assert arg_value["type"] == "BinaryExpr"
        assert arg_value["left"]["type"] == "StepRef"
        assert arg_value["left"]["path"] == ["s1", "input"]


class TestStepBodyEmission:
    """Test step body emission."""

    def test_step_with_body(self):
        """Step with inline body emits body key."""
        ast = parse("""
        facet Outer(input: Long) => (output: Long)
        facet Inner(value: Long) => (result: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Outer(input = $.input) andThen {
                inner = Inner(value = $.input)
                yield Outer(output = inner.result)
            }
            yield Test(output = s.output)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        assert "body" in step
        assert step["body"]["type"] == "AndThenBlock"
        assert len(step["body"]["steps"]) == 1
        assert step["body"]["steps"][0]["name"] == "inner"

    def test_step_without_body(self):
        """Step without body has no body key."""
        ast = parse("""
        facet Value(input: Long)
        workflow Test(input: Long) andThen {
            s = Value(input = $.input)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        assert "body" not in step


class TestMultipleBlockEmission:
    """Test multiple andThen block emission."""

    def test_multi_block_emission(self):
        """Multiple andThen blocks emit as array."""
        ast = parse("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                s1 = V(input = $.input)
                yield Test(a = s1.output)
            } andThen {
                s2 = V(input = $.input)
                yield Test(b = s2.output)
            }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        body = _first_decl(data, "WorkflowDecl")["body"]
        assert isinstance(body, list)
        assert len(body) == 2
        assert body[0]["type"] == "AndThenBlock"
        assert body[1]["type"] == "AndThenBlock"

    def test_single_block_backward_compat(self):
        """Single andThen block emits as object (not array)."""
        ast = parse("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = V(input = $.input)
            yield Test(output = s.output)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        body = _first_decl(data, "WorkflowDecl")["body"]
        assert isinstance(body, dict)
        assert body["type"] == "AndThenBlock"


class TestCollectionLiteralEmission:
    """Test JSON emission for collection literals."""

    def test_array_literal_emission(self):
        """Array literal emits correct JSON."""
        ast = parse("""
        facet V(items: String)
        workflow Test(a: Long, b: Long) andThen {
            s = V(items = [1, 2, 3])
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "ArrayLiteral"
        assert len(arg_value["elements"]) == 3
        assert arg_value["elements"][0] == {"type": "Int", "value": 1}
        assert arg_value["elements"][1] == {"type": "Int", "value": 2}

    def test_empty_array_emission(self):
        """Empty array literal emits correct JSON."""
        ast = parse("""
        facet V(items: String)
        workflow Test() andThen {
            s = V(items = [])
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "ArrayLiteral"
        assert arg_value["elements"] == []

    def test_map_literal_emission(self):
        """Map literal emits correct JSON."""
        ast = parse("""
        facet V(config: String)
        workflow Test() andThen {
            s = V(config = #{"key": "value", "num": 42})
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "MapLiteral"
        assert len(arg_value["entries"]) == 2
        assert arg_value["entries"][0]["key"] == "key"
        assert arg_value["entries"][0]["value"] == {"type": "String", "value": "value"}
        assert arg_value["entries"][1]["key"] == "num"
        assert arg_value["entries"][1]["value"] == {"type": "Int", "value": 42}

    def test_empty_map_emission(self):
        """Empty map literal emits correct JSON."""
        ast = parse("""
        facet V(config: String)
        workflow Test() andThen {
            s = V(config = #{})
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "MapLiteral"
        assert arg_value["entries"] == []

    def test_index_expr_emission(self):
        """Index expression emits correct JSON."""
        ast = parse("""
        facet V(items: String) => (output: String)
        workflow Test() andThen {
            s = V(items = "test")
            s2 = V(items = s.output[0])
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][1]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "IndexExpr"
        assert arg_value["target"]["type"] == "StepRef"
        assert arg_value["index"] == {"type": "Int", "value": 0}

    def test_nested_collections_emission(self):
        """Nested arrays and maps emit correct JSON."""
        ast = parse("""
        facet V(items: String)
        workflow Test() andThen {
            s = V(items = [[1, 2], [3, 4]])
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "ArrayLiteral"
        assert len(arg_value["elements"]) == 2
        assert arg_value["elements"][0]["type"] == "ArrayLiteral"
        assert arg_value["elements"][1]["type"] == "ArrayLiteral"

    def test_array_with_refs_emission(self):
        """Array with references emits correct JSON."""
        ast = parse("""
        facet V(items: String) => (output: Long)
        workflow Test(x: Long) andThen {
            s = V(items = "test")
            s2 = V(items = [$.x, s.output])
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][1]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "ArrayLiteral"
        assert arg_value["elements"][0]["type"] == "InputRef"
        assert arg_value["elements"][1]["type"] == "StepRef"


class TestUnaryExprEmission:
    """Test UnaryExpr JSON emission."""

    def test_simple_negation(self):
        """UnaryExpr emits correct JSON structure."""
        ast = parse("""
        facet V(input: Long)
        workflow Test() andThen {
            s = V(input = -5)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "UnaryExpr"
        assert arg_value["operator"] == "-"
        assert arg_value["operand"] == {"type": "Int", "value": 5}

    def test_negation_of_float(self):
        """UnaryExpr with float emits correctly."""
        ast = parse("""
        facet V(input: Double)
        workflow Test() andThen {
            s = V(input = -3.14)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "UnaryExpr"
        assert arg_value["operator"] == "-"
        assert arg_value["operand"] == {"type": "Double", "value": 3.14}

    def test_negation_of_ref(self):
        """UnaryExpr with input reference emits correctly."""
        ast = parse("""
        facet V(input: Long)
        workflow Test(x: Long) andThen {
            s = V(input = -$.x)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "UnaryExpr"
        assert arg_value["operator"] == "-"
        assert arg_value["operand"] == {"type": "InputRef", "path": ["x"]}

    def test_double_negation(self):
        """Double negation emits nested UnaryExpr."""
        ast = parse("""
        facet V(input: Long)
        workflow Test() andThen {
            s = V(input = --5)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "UnaryExpr"
        assert arg_value["operand"]["type"] == "UnaryExpr"
        assert arg_value["operand"]["operand"] == {"type": "Int", "value": 5}

    def test_negation_in_binary(self):
        """UnaryExpr nested in BinaryExpr emits correctly."""
        ast = parse("""
        facet V(input: Long)
        workflow Test(a: Long) andThen {
            s = V(input = $.a + -1)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        step = _first_decl(data, "WorkflowDecl")["body"]["steps"][0]
        arg_value = step["call"]["args"][0]["value"]
        assert arg_value["type"] == "BinaryExpr"
        assert arg_value["operator"] == "+"
        assert arg_value["right"]["type"] == "UnaryExpr"
        assert arg_value["right"]["operand"] == {"type": "Int", "value": 1}


class TestWorkflowInDeclarations:
    """Test that workflows appear in the unified declarations list."""

    def test_top_level_workflow_in_declarations(self):
        """Top-level workflow appears in program declarations."""
        ast = parse("""
        facet Value(input: Long)
        workflow Main(x: Long) => (result: Long) andThen {
            s = Value(input = $.x)
            yield Main(result = s.input)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        decl_types = [d["type"] for d in data.get("declarations", [])]
        assert "WorkflowDecl" in decl_types

    def test_namespaced_workflow_in_declarations(self):
        """Workflow inside namespace appears in namespace declarations."""
        ast = parse("""
        namespace test {
            facet Value(input: Long)
            workflow Main(x: Long) => (result: Long) andThen {
                s = Value(input = $.x)
                yield Main(result = s.input)
            }
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        decl_types = [d["type"] for d in ns.get("declarations", [])]
        assert "WorkflowDecl" in decl_types

    def test_workflow_alongside_facets_in_declarations(self):
        """Workflows coexist with facets and event facets in declarations."""
        ast = parse("""
        namespace test {
            event facet DoWork(input: String) => (output: String)
            facet Helper(x: String)
            workflow Main(x: String) => (result: String) andThen {
                w = DoWork(input = $.x)
                yield Main(result = w.output)
            }
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        decl_types = {d["type"] for d in ns.get("declarations", [])}
        assert "FacetDecl" in decl_types
        assert "EventFacetDecl" in decl_types
        assert "WorkflowDecl" in decl_types


class TestDocCommentEmission:
    """Tests for doc comment emission in JSON output."""

    def test_doc_on_namespace(self):
        """Namespace doc comment appears as structured dict in JSON."""
        ast = parse("""
        /** NS doc. */
        namespace ns {
            facet F(x: String)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        assert _first_decl(data, "Namespace")["doc"] == {
            "description": "NS doc.",
            "params": [],
            "returns": [],
        }

    def test_doc_on_facet(self):
        """Facet doc comment appears as structured dict in JSON."""
        ast = parse("""
        namespace ns {
            /** Facet doc. */
            facet F(x: String)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        facet = _first_decl(ns, "FacetDecl")
        assert facet["doc"] == {
            "description": "Facet doc.",
            "params": [],
            "returns": [],
        }

    def test_doc_on_event_facet_and_workflow(self):
        """Event facet and workflow doc comments in JSON."""
        ast = parse("""
        namespace ns {
            /** EF doc. */
            event facet EF(x: String) => (y: String)
            /** WF doc. */
            workflow WF(input: String) andThen {
                s = EF(x = $.input)
            }
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        decls = {d["name"]: d for d in ns["declarations"]}
        assert decls["EF"]["doc"]["description"] == "EF doc."
        assert decls["WF"]["doc"]["description"] == "WF doc."

    def test_doc_on_schema(self):
        """Schema doc comment appears as structured dict in JSON."""
        ast = parse("""
        namespace ns {
            /** Schema doc. */
            schema S { f1: String }
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        schema = _first_decl(ns, "SchemaDecl")
        assert schema["doc"] == {
            "description": "Schema doc.",
            "params": [],
            "returns": [],
        }

    def test_no_doc_key_when_absent(self):
        """No doc key emitted when declaration has no doc comment."""
        ast = parse("""
        namespace ns {
            facet F(x: String)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        assert "doc" not in ns
        assert "doc" not in _first_decl(ns, "FacetDecl")

    def test_doc_with_tags_emits_structured(self):
        """Doc comment with @param/@return tags emits full structured output."""
        ast = parse("""
        namespace ns {
            /**
             * Adds one to the input.
             * @param value The input value.
             * @return result The incremented value.
             */
            event facet AddOne(value: Long) => (result: Long)
        }
        """)
        emitter = JSONEmitter(include_locations=False)
        data = emitter.emit_dict(ast)
        ns = _first_decl(data, "Namespace")
        doc = _first_decl(ns, "EventFacetDecl")["doc"]
        assert doc["description"] == "Adds one to the input."
        assert doc["params"] == [{"name": "value", "description": "The input value."}]
        assert doc["returns"] == [{"name": "result", "description": "The incremented value."}]
