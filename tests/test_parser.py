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

"""Tests for AFL parser."""

import pytest

from afl import (
    AFLParser,
    ArrayType,
    BinaryExpr,
    Literal,
    ParseError,
    Program,
    Reference,
    SchemaDecl,
    TypeRef,
    UnaryExpr,
    parse,
)


@pytest.fixture
def parser():
    """Create a parser instance."""
    return AFLParser()


class TestBasicParsing:
    """Test basic parsing functionality."""

    def test_empty_program(self, parser):
        """Empty input should produce empty program."""
        ast = parser.parse("")
        assert isinstance(ast, Program)
        assert ast.namespaces == []
        assert ast.facets == []
        assert ast.workflows == []

    def test_simple_facet(self, parser):
        """Parse a simple facet declaration."""
        ast = parser.parse("facet SomeData(num: Long)")
        assert len(ast.facets) == 1
        facet = ast.facets[0]
        assert facet.sig.name == "SomeData"
        assert len(facet.sig.params) == 1
        assert facet.sig.params[0].name == "num"
        assert facet.sig.params[0].type.name == "Long"

    def test_facet_multiple_params(self, parser):
        """Parse facet with multiple parameters."""
        ast = parser.parse("facet User(name: String, email: String, age: Int)")
        facet = ast.facets[0]
        assert len(facet.sig.params) == 3
        assert facet.sig.params[0].name == "name"
        assert facet.sig.params[1].name == "email"
        assert facet.sig.params[2].name == "age"

    def test_facet_no_params(self, parser):
        """Parse facet with no parameters."""
        ast = parser.parse("facet Empty()")
        facet = ast.facets[0]
        assert facet.sig.name == "Empty"
        assert facet.sig.params == []

    def test_facet_with_return(self, parser):
        """Parse facet with return clause."""
        ast = parser.parse("facet Transform(input: String) => (output: String)")
        facet = ast.facets[0]
        assert facet.sig.returns is not None
        assert len(facet.sig.returns.params) == 1
        assert facet.sig.returns.params[0].name == "output"


class TestEventFacets:
    """Test event facet parsing."""

    def test_event_facet(self, parser):
        """Parse event facet declaration."""
        ast = parser.parse("event facet Sub(input1: Long, input2: Long) => (output: Long)")
        assert len(ast.event_facets) == 1
        ef = ast.event_facets[0]
        assert ef.sig.name == "Sub"
        assert len(ef.sig.params) == 2
        assert ef.sig.returns is not None


class TestScriptBlocks:
    """Test script block parsing for inline code execution."""

    def test_script_block_simple(self, parser):
        """Parse facet with simple script block as pre_script."""
        from afl.ast import ScriptBlock

        ast = parser.parse('facet Test() script "result = params"')
        assert len(ast.facets) == 1
        f = ast.facets[0]
        assert f.pre_script is not None
        assert isinstance(f.pre_script, ScriptBlock)
        assert f.pre_script.language == "python"
        assert f.pre_script.code == "result = params"
        assert f.body is None

    def test_script_block_explicit_python(self, parser):
        """Parse script block with explicit python directive."""
        from afl.ast import ScriptBlock

        source = '''facet Test()
script
    python "result = params"'''
        ast = parser.parse(source)
        f = ast.facets[0]
        assert isinstance(f.pre_script, ScriptBlock)
        assert f.pre_script.language == "python"
        assert f.pre_script.code == "result = params"

    def test_script_block_with_params_and_returns(self, parser):
        """Parse script block with parameters and returns."""
        from afl.ast import ScriptBlock

        source = r'facet Transform(input: String) => (output: String) script "result[\"output\"] = params[\"input\"].upper()"'
        ast = parser.parse(source)
        f = ast.facets[0]
        assert isinstance(f.pre_script, ScriptBlock)
        assert 'params["input"]' in f.pre_script.code

    def test_script_block_event_facet(self, parser):
        """Parse event facet with script block."""
        from afl.ast import ScriptBlock

        ast = parser.parse('event facet Process() script "print(42)"')
        ef = ast.event_facets[0]
        assert isinstance(ef.pre_script, ScriptBlock)
        assert ef.pre_script.code == "print(42)"

    def test_script_block_in_namespace(self, parser):
        """Parse script block inside namespace."""
        from afl.ast import ScriptBlock

        source = '''namespace utils {
    facet Helper() script "pass"
}'''
        ast = parser.parse(source)
        ns = ast.namespaces[0]
        f = ns.facets[0]
        assert isinstance(f.pre_script, ScriptBlock)

    def test_pre_script_brace_syntax(self, parser):
        """Parse facet with brace-delimited script block."""
        from afl.ast import ScriptBlock

        source = 'facet F() script {\n    x = 1\n    y = 2\n}'
        ast = parser.parse(source)
        f = ast.facets[0]
        assert isinstance(f.pre_script, ScriptBlock)
        assert "x = 1" in f.pre_script.code
        assert "y = 2" in f.pre_script.code

    def test_pre_script_with_andthen(self, parser):
        """Parse pre_script followed by andThen blocks."""
        from afl.ast import AndThenBlock, ScriptBlock

        source = 'facet F() script "pre" andThen { s = G() }'
        ast = parser.parse(source)
        f = ast.facets[0]
        assert isinstance(f.pre_script, ScriptBlock)
        assert f.pre_script.code == "pre"
        assert isinstance(f.body, AndThenBlock)
        assert f.body.block is not None

    def test_andthen_script(self, parser):
        """Parse andThen script variant."""
        from afl.ast import AndThenBlock, ScriptBlock

        source = 'facet F() andThen script "y = 2"'
        ast = parser.parse(source)
        f = ast.facets[0]
        assert f.pre_script is None
        assert isinstance(f.body, AndThenBlock)
        assert f.body.script is not None
        assert f.body.script.code == "y = 2"
        assert f.body.block is None

    def test_multiple_andthen_mixed(self, parser):
        """Parse mixed regular andThen and andThen script blocks."""
        from afl.ast import AndThenBlock

        source = 'facet F() andThen { s = G() } andThen script "y = 2"'
        ast = parser.parse(source)
        f = ast.facets[0]
        assert isinstance(f.body, list)
        assert len(f.body) == 2
        assert f.body[0].block is not None
        assert f.body[1].script is not None

    def test_pre_script_with_andthen_script(self, parser):
        """Parse pre_script + regular andThen + andThen script."""
        from afl.ast import ScriptBlock

        source = 'facet F() script "pre" andThen { s = G() } andThen script "post"'
        ast = parser.parse(source)
        f = ast.facets[0]
        assert isinstance(f.pre_script, ScriptBlock)
        assert f.pre_script.code == "pre"
        assert isinstance(f.body, list)
        assert len(f.body) == 2
        assert f.body[0].block is not None
        assert f.body[1].script is not None
        assert f.body[1].script.code == "post"

    def test_workflow_with_pre_script(self, parser):
        """Parse workflow with pre_script."""
        from afl.ast import ScriptBlock

        source = 'workflow W() script "setup" andThen { s = F() }'
        ast = parser.parse(source)
        w = ast.workflows[0]
        assert isinstance(w.pre_script, ScriptBlock)
        assert w.pre_script.code == "setup"


class TestPromptBlocks:
    """Test prompt block parsing for LLM-based event facets."""

    def test_prompt_block_simple(self, parser):
        """Parse event facet with simple prompt block."""
        from afl.ast import PromptBlock

        ast = parser.parse('event facet Test() prompt { template "hello" }')
        assert len(ast.event_facets) == 1
        ef = ast.event_facets[0]
        assert ef.body is not None
        assert isinstance(ef.body, PromptBlock)
        assert ef.body.template == "hello"
        assert ef.body.system is None
        assert ef.body.model is None

    def test_prompt_block_all_directives(self, parser):
        """Parse prompt block with all directives."""
        from afl.ast import PromptBlock

        source = '''event facet Summarize(doc: String) => (summary: String)
prompt {
    system "You are a summarizer."
    template "Summarize: {doc}"
    model "claude-3-opus"
}'''
        ast = parser.parse(source)
        ef = ast.event_facets[0]
        assert isinstance(ef.body, PromptBlock)
        assert ef.body.system == "You are a summarizer."
        assert ef.body.template == "Summarize: {doc}"
        assert ef.body.model == "claude-3-opus"

    def test_prompt_block_same_line(self, parser):
        """Parse prompt block on same line as signature."""
        from afl.ast import PromptBlock

        ast = parser.parse('event facet Test() prompt { system "sys" template "tmpl" }')
        ef = ast.event_facets[0]
        assert isinstance(ef.body, PromptBlock)
        assert ef.body.system == "sys"
        assert ef.body.template == "tmpl"

    def test_prompt_block_multiline_template(self, parser):
        """Parse prompt block with escaped newlines in template."""
        from afl.ast import PromptBlock

        source = r'event facet Test() prompt { template "Line1\nLine2\nLine3" }'
        ast = parser.parse(source)
        ef = ast.event_facets[0]
        assert isinstance(ef.body, PromptBlock)
        assert "Line1\nLine2\nLine3" == ef.body.template

    def test_prompt_block_in_namespace(self, parser):
        """Parse prompt block inside namespace."""
        from afl.ast import PromptBlock

        source = '''namespace llm {
    event facet Query(q: String) => (answer: String)
    prompt {
        template "{q}"
    }
}'''
        ast = parser.parse(source)
        assert len(ast.namespaces) == 1
        ns = ast.namespaces[0]
        assert len(ns.event_facets) == 1
        ef = ns.event_facets[0]
        assert isinstance(ef.body, PromptBlock)
        assert ef.body.template == "{q}"


class TestWorkflows:
    """Test workflow parsing."""

    def test_simple_workflow(self, parser):
        """Parse workflow declaration."""
        ast = parser.parse("workflow MyFlow(input: String) => (output: String)")
        assert len(ast.workflows) == 1
        wf = ast.workflows[0]
        assert wf.sig.name == "MyFlow"

    def test_workflow_with_body(self, parser):
        """Parse workflow with andThen block."""
        source = """
        workflow GetStreets(input: String) => (output: String) andThen {
            step = ConvertToGeoJson(input = $.input)
            yield GetStreets(output = step.output)
        }
        """
        ast = parser.parse(source)
        wf = ast.workflows[0]
        assert wf.body is not None
        assert wf.body.block is not None
        assert len(wf.body.block.steps) == 1
        assert wf.body.block.yield_stmt is not None


class TestNamespaces:
    """Test namespace parsing."""

    def test_simple_namespace(self, parser):
        """Parse namespace block."""
        source = """
        namespace team.a.osm {
            facet Data(value: String)
        }
        """
        ast = parser.parse(source)
        assert len(ast.namespaces) == 1
        ns = ast.namespaces[0]
        assert ns.name == "team.a.osm"
        assert len(ns.facets) == 1

    def test_namespace_with_uses(self, parser):
        """Parse namespace with uses declarations."""
        source = """
        namespace team.a.osm.conversions {
            uses team.b.osm.streets
            uses team.c.utils
            facet ConvertToGeoJson(input: String) => (output: String)
        }
        """
        ast = parser.parse(source)
        ns = ast.namespaces[0]
        assert len(ns.uses) == 2
        assert ns.uses[0].name == "team.b.osm.streets"
        assert ns.uses[1].name == "team.c.utils"

    def test_namespace_with_workflow(self, parser):
        """Parse namespace containing workflow."""
        source = """
        namespace team.a.osm.conversions {
            uses team.b.osm.streets

            facet ConvertToGeoJson(input: String) => (output: String)

            workflow GetStreets(input: String) => (output: String) andThen {
                step = ConvertToGeoJson(input = $.input)
                streets = FilterStreets(input = step.output)
                yield GetStreets(output = streets.output)
            }
        }
        """
        ast = parser.parse(source)
        ns = ast.namespaces[0]
        assert len(ns.workflows) == 1
        assert len(ns.facets) == 1


class TestMixins:
    """Test mixin parsing."""

    def test_mixin_in_signature(self, parser):
        """Parse facet with mixin in signature."""
        source = "facet Job(input: String) with Retry(maxAttempts = 3)"
        ast = parser.parse(source)
        facet = ast.facets[0]
        assert len(facet.sig.mixins) == 1
        assert facet.sig.mixins[0].name == "Retry"

    def test_mixin_call_with_alias(self, parser):
        """Parse mixin call with alias."""
        source = """
        workflow Test(input: String) andThen {
            job = RunASparkJob(input = $.input) with User(name = "test") as user
        }
        """
        ast = parser.parse(source)
        wf = ast.workflows[0]
        step = wf.body.block.steps[0]
        assert len(step.call.mixins) == 1
        assert step.call.mixins[0].alias == "user"


class TestImplicits:
    """Test implicit declaration parsing."""

    def test_implicit_decl(self, parser):
        """Parse implicit declaration."""
        source = 'implicit user = User(name = "John", email = "john@example.com")'
        ast = parser.parse(source)
        assert len(ast.implicits) == 1
        impl = ast.implicits[0]
        assert impl.name == "user"
        assert impl.call.name == "User"


class TestReferences:
    """Test reference parsing."""

    def test_input_reference(self, parser):
        """Parse input reference ($.field)."""
        source = """
        workflow Test(input: String) andThen {
            step = Process(value = $.input)
        }
        """
        ast = parser.parse(source)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, Reference)
        assert arg.value.is_input is True
        assert arg.value.path == ["input"]

    def test_step_reference(self, parser):
        """Parse step reference (step.field)."""
        source = """
        workflow Test(input: String) andThen {
            step1 = Process(value = $.input)
            step2 = Transform(value = step1.output)
        }
        """
        ast = parser.parse(source)
        step2 = ast.workflows[0].body.block.steps[1]
        arg = step2.call.args[0]
        assert isinstance(arg.value, Reference)
        assert arg.value.is_input is False
        assert arg.value.path == ["step1", "output"]

    def test_nested_reference(self, parser):
        """Parse nested reference (step.field.subfield)."""
        source = """
        workflow Test(data: Json) andThen {
            step = Process(value = $.data.nested.field)
        }
        """
        ast = parser.parse(source)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert arg.value.path == ["data", "nested", "field"]


class TestLiterals:
    """Test literal parsing."""

    def test_string_literal(self, parser):
        """Parse string literal."""
        source = 'implicit msg = Message(text = "hello world")'
        ast = parser.parse(source)
        arg = ast.implicits[0].call.args[0]
        assert isinstance(arg.value, Literal)
        assert arg.value.kind == "string"
        assert arg.value.value == "hello world"

    def test_integer_literal(self, parser):
        """Parse integer literal."""
        source = "implicit count = Counter(value = 42)"
        ast = parser.parse(source)
        arg = ast.implicits[0].call.args[0]
        assert isinstance(arg.value, Literal)
        assert arg.value.kind == "integer"
        assert arg.value.value == 42

    def test_boolean_literal(self, parser):
        """Parse boolean literals."""
        source = "implicit flag = Config(enabled = true, disabled = false)"
        ast = parser.parse(source)
        args = ast.implicits[0].call.args
        assert args[0].value.value is True
        assert args[1].value.value is False

    def test_null_literal(self, parser):
        """Parse null literal."""
        source = "implicit opt = Optional(value = null)"
        ast = parser.parse(source)
        arg = ast.implicits[0].call.args[0]
        assert arg.value.kind == "null"
        assert arg.value.value is None

    def test_float_literal(self, parser):
        """Parse float literal."""
        source = "implicit pi = Circle(radius = 3.14)"
        ast = parser.parse(source)
        arg = ast.implicits[0].call.args[0]
        assert isinstance(arg.value, Literal)
        assert arg.value.kind == "double"
        assert arg.value.value == 3.14

    def test_float_literal_small(self, parser):
        """Parse float literal with leading zero."""
        source = "implicit val = Config(threshold = 0.5)"
        ast = parser.parse(source)
        arg = ast.implicits[0].call.args[0]
        assert isinstance(arg.value, Literal)
        assert arg.value.kind == "double"
        assert arg.value.value == 0.5

    def test_float_literal_scientific(self, parser):
        """Parse float literal with scientific notation."""
        source = "implicit val = Config(scale = 1.0e10)"
        ast = parser.parse(source)
        arg = ast.implicits[0].call.args[0]
        assert isinstance(arg.value, Literal)
        assert arg.value.kind == "double"
        assert arg.value.value == 1.0e10

    def test_float_default_param(self, parser):
        """Parse parameter with float default value."""
        ast = parser.parse("facet Search(max_distance: Double = 10.5)")
        param = ast.facets[0].sig.params[0]
        assert isinstance(param.default, Literal)
        assert param.default.kind == "double"
        assert param.default.value == 10.5


class TestForeach:
    """Test foreach parsing."""

    def test_foreach_in_workflow(self, parser):
        """Parse workflow with foreach."""
        source = """
        workflow ProcessAllRegions(regions: Json) => (results: Json) andThen foreach r in $.regions {
            processed = ProcessRegion(region = r.name)
            yield ProcessAllRegions(results = processed.result)
        }
        """
        ast = parser.parse(source)
        wf = ast.workflows[0]
        assert wf.body.foreach is not None
        assert wf.body.foreach.variable == "r"
        assert wf.body.foreach.iterable.path == ["regions"]


class TestComments:
    """Test comment handling."""

    def test_line_comment(self, parser):
        """Line comments should be ignored."""
        source = """
        // This is a comment
        facet Data(value: String)  // inline comment
        """
        ast = parser.parse(source)
        assert len(ast.facets) == 1

    def test_block_comment(self, parser):
        """Block comments should be ignored."""
        source = """
        /* Multi-line
           comment */
        facet Data(value: String)
        """
        ast = parser.parse(source)
        assert len(ast.facets) == 1


class TestTypes:
    """Test type parsing."""

    def test_builtin_types(self, parser):
        """Parse all builtin types."""
        source = """
        facet AllTypes(
            s: String,
            l: Long,
            i: Int,
            b: Boolean,
            j: Json
        )
        """
        ast = parser.parse(source)
        params = ast.facets[0].sig.params
        assert params[0].type.name == "String"
        assert params[1].type.name == "Long"
        assert params[2].type.name == "Int"
        assert params[3].type.name == "Boolean"
        assert params[4].type.name == "Json"

    def test_qualified_type(self, parser):
        """Parse qualified type name."""
        source = "facet UseCustom(data: team.types.CustomData)"
        ast = parser.parse(source)
        param = ast.facets[0].sig.params[0]
        assert param.type.name == "team.types.CustomData"


class TestErrorReporting:
    """Test error reporting with line/column numbers."""

    def test_unexpected_token(self, parser):
        """Parse error should include line and column."""
        with pytest.raises(ParseError) as exc_info:
            parser.parse("facet ()")
        assert exc_info.value.line is not None
        assert exc_info.value.column is not None

    def test_missing_parenthesis(self, parser):
        """Missing parenthesis should report error location."""
        with pytest.raises(ParseError) as exc_info:
            parser.parse("facet Test(name: String")
        assert exc_info.value.line is not None

    def test_invalid_return_clause(self, parser):
        """Invalid return clause syntax."""
        with pytest.raises(ParseError):
            # Return clause must be => ( ... ), not => ...
            parser.parse("event facet Sub(input: Long) => output: Long")


class TestConvenienceFunction:
    """Test the parse() convenience function."""

    def test_parse_function(self):
        """Test module-level parse function."""
        ast = parse("facet Simple()")
        assert isinstance(ast, Program)
        assert len(ast.facets) == 1


class TestSourceLocations:
    """Test source location tracking."""

    def test_facet_has_location(self, parser):
        """Parsed nodes should have source locations."""
        ast = parser.parse("facet Test(value: String)")
        facet = ast.facets[0]
        assert facet.location is not None
        assert facet.location.line == 1


class TestMultipleDeclarations:
    """Test parsing multiple declarations."""

    def test_multiple_facets(self, parser):
        """Parse multiple facet declarations."""
        source = """
        facet A(x: Int)
        facet B(y: String)
        facet C(z: Boolean)
        """
        ast = parser.parse(source)
        assert len(ast.facets) == 3

    def test_mixed_declarations(self, parser):
        """Parse mixed declaration types."""
        source = """
        facet Data(value: String)
        event facet Process(input: String) => (output: String)
        workflow Main(start: String) => (end: String)
        implicit config = Config(debug = true)
        """
        ast = parser.parse(source)
        assert len(ast.facets) == 1
        assert len(ast.event_facets) == 1
        assert len(ast.workflows) == 1
        assert len(ast.implicits) == 1


class TestSemicolonSeparators:
    """Test semicolon as statement separator."""

    def test_semicolon_separator(self, parser):
        """Semicolons can separate statements."""
        source = "facet A(); facet B(); facet C()"
        ast = parser.parse(source)
        assert len(ast.facets) == 3

    def test_mixed_separators(self, parser):
        """Mix of newlines and semicolons."""
        source = """facet A(); facet B()
        facet C()"""
        ast = parser.parse(source)
        assert len(ast.facets) == 3


class TestConcatExpression:
    """Test concatenation expression (++) parsing."""

    def test_simple_concat(self, parser):
        """Parse simple concat expression."""
        source = """
        facet Data() => (value: Json)
        workflow Test() => (result: Json) andThen {
            a = Data()
            b = Data()
            yield Test(result = a.value ++ b.value)
        }
        """
        ast = parser.parse(source)
        yield_stmt = ast.workflows[0].body.block.yield_stmts[0]
        arg = yield_stmt.call.args[0]
        from afl.ast import ConcatExpr

        assert isinstance(arg.value, ConcatExpr)
        assert len(arg.value.operands) == 2

    def test_multi_concat(self, parser):
        """Parse multiple concat operands."""
        source = """
        facet Data() => (value: Json)
        workflow Test() => (result: Json) andThen {
            a = Data()
            b = Data()
            c = Data()
            yield Test(result = a.value ++ b.value ++ c.value)
        }
        """
        ast = parser.parse(source)
        yield_stmt = ast.workflows[0].body.block.yield_stmts[0]
        arg = yield_stmt.call.args[0]
        from afl.ast import ConcatExpr

        assert isinstance(arg.value, ConcatExpr)
        assert len(arg.value.operands) == 3

    def test_concat_with_newlines(self, parser):
        """Parse concat expression with newlines after ++."""
        source = """
        facet Data() => (value: Json)
        workflow Test() => (result: Json) andThen {
            a = Data()
            b = Data()
            c = Data()
            yield Test(result =
                a.value ++
                b.value ++
                c.value)
        }
        """
        ast = parser.parse(source)
        yield_stmt = ast.workflows[0].body.block.yield_stmts[0]
        arg = yield_stmt.call.args[0]
        from afl.ast import ConcatExpr

        assert isinstance(arg.value, ConcatExpr)
        assert len(arg.value.operands) == 3


class TestUseDeclaration:
    """Test 'use' as alternative to 'uses'."""

    def test_use_singular(self, parser):
        """Parse 'use' declaration (singular form)."""
        source = """
        namespace test {
            use other.module
            facet Test()
        }
        """
        ast = parser.parse(source)
        ns = ast.namespaces[0]
        assert len(ns.uses) == 1
        assert ns.uses[0].name == "other.module"

    def test_multiple_use_declarations(self, parser):
        """Parse multiple 'use' declarations."""
        source = """
        namespace test {
            use module.a
            use module.b
            uses module.c
            facet Test()
        }
        """
        ast = parser.parse(source)
        ns = ast.namespaces[0]
        assert len(ns.uses) == 3


class TestDefaultParameterValues:
    """Test parsing default parameter values."""

    def test_string_default(self, parser):
        """Parse parameter with string default."""
        ast = parser.parse('facet Greeting(message: String = "hello")')
        param = ast.facets[0].sig.params[0]
        assert param.name == "message"
        assert param.type.name == "String"
        assert isinstance(param.default, Literal)
        assert param.default.kind == "string"
        assert param.default.value == "hello"

    def test_integer_default(self, parser):
        """Parse parameter with integer default."""
        ast = parser.parse("facet Config(retries: Int = 3)")
        param = ast.facets[0].sig.params[0]
        assert isinstance(param.default, Literal)
        assert param.default.kind == "integer"
        assert param.default.value == 3

    def test_boolean_default(self, parser):
        """Parse parameter with boolean default."""
        ast = parser.parse("facet Config(verbose: Boolean = true)")
        param = ast.facets[0].sig.params[0]
        assert isinstance(param.default, Literal)
        assert param.default.value is True

    def test_null_default(self, parser):
        """Parse parameter with null default."""
        ast = parser.parse("facet Config(extra: Json = null)")
        param = ast.facets[0].sig.params[0]
        assert isinstance(param.default, Literal)
        assert param.default.kind == "null"
        assert param.default.value is None

    def test_no_default(self, parser):
        """Parameters without defaults have default=None."""
        ast = parser.parse("facet Data(value: String)")
        param = ast.facets[0].sig.params[0]
        assert param.default is None

    def test_mixed_defaults(self, parser):
        """Parse params where some have defaults and some don't."""
        ast = parser.parse("facet Mixed(required: String, optional: Int = 42)")
        params = ast.facets[0].sig.params
        assert params[0].default is None
        assert params[1].default is not None
        assert params[1].default.value == 42

    def test_workflow_with_defaults(self, parser):
        """Parse workflow with default parameter values."""
        ast = parser.parse('workflow MyFlow(input: String = "hello") => (output: String = "world")')
        wf = ast.workflows[0]
        assert wf.sig.params[0].default.value == "hello"
        assert wf.sig.returns.params[0].default.value == "world"

    def test_event_facet_with_defaults(self, parser):
        """Parse event facet with default parameter values."""
        ast = parser.parse("event facet Process(count: Long = 10) => (result: Long)")
        ef = ast.event_facets[0]
        assert ef.sig.params[0].default.value == 10

    def test_multiple_defaults(self, parser):
        """Parse multiple parameters with defaults."""
        ast = parser.parse(
            'facet Config(host: String = "localhost", port: Int = 8080, debug: Boolean = false)'
        )
        params = ast.facets[0].sig.params
        assert params[0].default.value == "localhost"
        assert params[1].default.value == 8080
        assert params[2].default.value is False

    def test_reference_default(self, parser):
        """Parse parameter with reference default."""
        ast = parser.parse(
            "workflow Test(x: Long = 1) => (output: Long) andThen {\n"
            "    step = Process(value = $.x)\n"
            "}"
        )
        param = ast.workflows[0].sig.params[0]
        assert isinstance(param.default, Literal)
        assert param.default.value == 1


class TestSchemaDeclarations:
    """Test schema declaration parsing."""

    def test_basic_schema(self, parser):
        """Parse a basic schema with scalar fields."""
        ast = parser.parse("""
        namespace app {
            schema UserRequest {
                name: String,
                age: Int
            }
        }
        """)
        assert len(ast.namespaces) == 1
        ns = ast.namespaces[0]
        assert len(ns.schemas) == 1
        schema = ns.schemas[0]
        assert isinstance(schema, SchemaDecl)
        assert schema.name == "UserRequest"
        assert len(schema.fields) == 2
        assert schema.fields[0].name == "name"
        assert isinstance(schema.fields[0].type, TypeRef)
        assert schema.fields[0].type.name == "String"
        assert schema.fields[1].name == "age"
        assert schema.fields[1].type.name == "Int"

    def test_schema_with_array_type(self, parser):
        """Parse a schema with array type fields."""
        ast = parser.parse("""
        namespace app {
            schema TaggedItem {
                tags: [String],
                ids: [Long]
            }
        }
        """)
        schema = ast.namespaces[0].schemas[0]
        assert len(schema.fields) == 2
        tags_field = schema.fields[0]
        assert tags_field.name == "tags"
        assert isinstance(tags_field.type, ArrayType)
        assert isinstance(tags_field.type.element_type, TypeRef)
        assert tags_field.type.element_type.name == "String"

    def test_schema_referencing_schema(self, parser):
        """Parse a schema that references another schema as a field type."""
        ast = parser.parse("""
        namespace app {
            schema Address {
                street: String,
                city: String
            }
            schema Person {
                name: String,
                home: Address
            }
        }
        """)
        ns = ast.namespaces[0]
        assert len(ns.schemas) == 2
        person = ns.schemas[1]
        assert person.fields[1].name == "home"
        assert isinstance(person.fields[1].type, TypeRef)
        assert person.fields[1].type.name == "Address"

    def test_schema_in_namespace(self, parser):
        """Parse a schema inside a namespace."""
        ast = parser.parse("""
        namespace app {
            schema Config {
                key: String,
                value: String
            }
        }
        """)
        assert len(ast.namespaces) == 1
        ns = ast.namespaces[0]
        assert len(ns.schemas) == 1
        assert ns.schemas[0].name == "Config"
        assert len(ns.schemas[0].fields) == 2

    def test_schema_as_parameter_type(self, parser):
        """Schema name used as a parameter type in facet signature."""
        ast = parser.parse("""
        namespace app {
            schema UserRequest {
                name: String
            }
            event facet CreateUser(request: UserRequest) => (id: String)
        }
        """)
        ns = ast.namespaces[0]
        assert len(ns.schemas) == 1
        assert len(ns.event_facets) == 1
        param = ns.event_facets[0].sig.params[0]
        assert param.name == "request"
        assert isinstance(param.type, TypeRef)
        assert param.type.name == "UserRequest"

    def test_array_type_in_parameter(self, parser):
        """Array type used in regular facet parameter."""
        ast = parser.parse("facet Process(items: [String])")
        param = ast.facets[0].sig.params[0]
        assert param.name == "items"
        assert isinstance(param.type, ArrayType)
        assert isinstance(param.type.element_type, TypeRef)
        assert param.type.element_type.name == "String"

    def test_nested_array_type(self, parser):
        """Nested array type [[String]]."""
        ast = parser.parse("""
        namespace app {
            schema Matrix {
                rows: [[Int]]
            }
        }
        """)
        field = ast.namespaces[0].schemas[0].fields[0]
        assert isinstance(field.type, ArrayType)
        assert isinstance(field.type.element_type, ArrayType)
        assert isinstance(field.type.element_type.element_type, TypeRef)
        assert field.type.element_type.element_type.name == "Int"

    def test_empty_schema(self, parser):
        """Parse an empty schema."""
        ast = parser.parse("namespace app { schema Empty {} }")
        assert len(ast.namespaces) == 1
        ns = ast.namespaces[0]
        assert len(ns.schemas) == 1
        assert ns.schemas[0].name == "Empty"
        assert ns.schemas[0].fields == []

    def test_schema_with_qualified_type(self, parser):
        """Schema field with qualified type name."""
        ast = parser.parse("""
        namespace app {
            schema Response {
                data: other.DataModel
            }
        }
        """)
        field = ast.namespaces[0].schemas[0].fields[0]
        assert isinstance(field.type, TypeRef)
        assert field.type.name == "other.DataModel"


class TestArithmeticExpressions:
    """Test arithmetic expression parsing."""

    def test_addition(self, parser):
        """Parse addition expression."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input + 1)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, BinaryExpr)
        assert arg.value.operator == "+"
        assert isinstance(arg.value.left, Reference)
        assert isinstance(arg.value.right, Literal)
        assert arg.value.right.value == 1

    def test_subtraction(self, parser):
        """Parse subtraction expression."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input - 1)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, BinaryExpr)
        assert arg.value.operator == "-"

    def test_multiplication(self, parser):
        """Parse multiplication expression."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input * 2)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, BinaryExpr)
        assert arg.value.operator == "*"

    def test_division(self, parser):
        """Parse division expression."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input / 2)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, BinaryExpr)
        assert arg.value.operator == "/"

    def test_modulo(self, parser):
        """Parse modulo expression."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input % 3)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, BinaryExpr)
        assert arg.value.operator == "%"

    def test_precedence_mul_over_add(self, parser):
        """Multiplication binds tighter than addition."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(a: Long, b: Long, c: Long) => (output: Long) andThen {
            s = Value(input = $.a + $.b * $.c)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        expr = step.call.args[0].value
        # Should be: a + (b * c)
        assert isinstance(expr, BinaryExpr)
        assert expr.operator == "+"
        assert isinstance(expr.left, Reference)
        assert isinstance(expr.right, BinaryExpr)
        assert expr.right.operator == "*"

    def test_left_associative_addition(self, parser):
        """Addition is left-associative."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(a: Long, b: Long, c: Long) => (output: Long) andThen {
            s = Value(input = $.a + $.b + $.c)
            yield Test(output = s.input)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        expr = step.call.args[0].value
        # Should be: (a + b) + c
        assert isinstance(expr, BinaryExpr)
        assert expr.operator == "+"
        assert isinstance(expr.left, BinaryExpr)
        assert expr.left.operator == "+"

    def test_concat_with_arithmetic(self, parser):
        """Concat has lower precedence than arithmetic."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(a: Long, b: Long) => (output: String) andThen {
            s = Value(input = $.a + $.b)
            yield Test(output = s.input ++ "px")
        }
        """)
        yield_stmt = ast.workflows[0].body.block.yield_stmts[0]
        arg = yield_stmt.call.args[0]
        from afl.ast import ConcatExpr

        assert isinstance(arg.value, ConcatExpr)
        assert len(arg.value.operands) == 2

    def test_step_ref_in_arithmetic(self, parser):
        """Step references work in arithmetic expressions."""
        ast = parser.parse("""
        facet Value(input: Long, output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s1 = Value(input = $.input)
            s2 = Value(input = s1.input + 1)
            yield Test(output = s2.input)
        }
        """)
        step2 = ast.workflows[0].body.block.steps[1]
        arg = step2.call.args[0]
        assert isinstance(arg.value, BinaryExpr)
        assert arg.value.operator == "+"
        assert isinstance(arg.value.left, Reference)
        assert arg.value.left.path == ["s1", "input"]

    def test_param_default_arithmetic(self, parser):
        """Arithmetic expressions are NOT valid as defaults (only literals/refs)."""
        # Arithmetic in defaults is valid syntactically since expr covers it
        ast = parser.parse("facet Config(timeout: Long = 30 * 1000)")
        param = ast.facets[0].sig.params[0]
        assert isinstance(param.default, BinaryExpr)
        assert param.default.operator == "*"


class TestStepBody:
    """Test statement-level andThen body parsing."""

    def test_inline_body(self, parser):
        """Parse step with inline andThen body."""
        ast = parser.parse("""
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
        step = ast.workflows[0].body.block.steps[0]
        assert step.name == "s"
        assert step.body is not None
        from afl.ast import AndThenBlock
        assert isinstance(step.body, AndThenBlock)
        assert len(step.body.block.steps) == 1
        assert step.body.block.steps[0].name == "inner"
        assert len(step.body.block.yield_stmts) == 1

    def test_no_body(self, parser):
        """Step without body has body=None."""
        ast = parser.parse("""
        facet Value(input: Long) => (output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = Value(input = $.input)
            yield Test(output = s.output)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        assert step.body is None

    def test_body_with_foreach(self, parser):
        """Parse step body with foreach clause."""
        ast = parser.parse("""
        facet Source(input: Long) => (items: Json)
        facet Process(item: Long) => (result: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            src = Source(input = $.input) andThen foreach item in $.items {
                p = Process(item = $.item)
                yield Source(items = p.result)
            }
            yield Test(output = src.items)
        }
        """)
        step = ast.workflows[0].body.block.steps[0]
        assert step.body is not None
        assert step.body.foreach is not None
        assert step.body.foreach.variable == "item"


class TestMultipleAndThenBlocks:
    """Test multiple andThen blocks on facets/workflows."""

    def test_two_andthen_blocks(self, parser):
        """Parse facet with two andThen blocks."""
        ast = parser.parse("""
        facet Process(input: Long) => (a: Long, b: Long)
        facet Inner(value: Long) => (result: Long)
        workflow Test(input: Long) => (x: Long, y: Long) andThen {
                s1 = Process(input = $.input)
                yield Test(x = s1.a)
            }
            andThen {
                s2 = Process(input = $.input)
                yield Test(y = s2.b)
            }
        """)
        body = ast.workflows[0].body
        assert isinstance(body, list)
        assert len(body) == 2
        from afl.ast import AndThenBlock
        assert all(isinstance(b, AndThenBlock) for b in body)
        assert body[0].block.steps[0].name == "s1"
        assert body[1].block.steps[0].name == "s2"

    def test_three_andthen_blocks(self, parser):
        """Parse workflow with three andThen blocks."""
        ast = parser.parse("""
        facet V(input: Long) => (output: Long)
        workflow Multi(input: Long) => (a: Long, b: Long, c: Long) andThen {
            s1 = V(input = $.input)
            yield Multi(a = s1.output)
        } andThen {
            s2 = V(input = $.input)
            yield Multi(b = s2.output)
        } andThen {
            s3 = V(input = $.input)
            yield Multi(c = s3.output)
        }
        """)
        body = ast.workflows[0].body
        assert isinstance(body, list)
        assert len(body) == 3

    def test_single_still_works(self, parser):
        """Single andThen block still produces AndThenBlock (not list)."""
        ast = parser.parse("""
        facet V(input: Long) => (output: Long)
        workflow Test(input: Long) => (output: Long) andThen {
            s = V(input = $.input)
            yield Test(output = s.output)
        }
        """)
        from afl.ast import AndThenBlock
        assert isinstance(ast.workflows[0].body, AndThenBlock)

    def test_multi_block_with_foreach(self, parser):
        """Multiple blocks, one with foreach."""
        ast = parser.parse("""
        facet Source() => (items: Json)
        facet Process(item: Long) => (result: Long)
        workflow Test(input: Long) => (a: Long, b: Long) andThen {
                src = Source()
                yield Test(a = src.items)
            }
            andThen foreach item in $.input {
                p = Process(item = $.item)
                yield Test(b = p.result)
            }
        """)
        body = ast.workflows[0].body
        assert isinstance(body, list)
        assert len(body) == 2
        assert body[0].foreach is None
        assert body[1].foreach is not None
        assert body[1].foreach.variable == "item"


class TestCollectionLiterals:
    """Test array, map, and index expression parsing."""

    def test_empty_array(self, parser):
        """Parse empty array literal."""
        ast = parser.parse("""
        facet V(items: Json)
        workflow Test() andThen {
            s = V(items = [])
        }
        """)
        from afl.ast import ArrayLiteral
        step = ast.workflows[0].body.block.steps[0]
        arg = step.call.args[0]
        assert isinstance(arg.value, ArrayLiteral)
        assert arg.value.elements == []

    def test_array_with_literals(self, parser):
        """Parse array with literal elements."""
        ast = parser.parse("""
        facet V(items: Json)
        workflow Test() andThen {
            s = V(items = [1, 2, 3])
        }
        """)
        from afl.ast import ArrayLiteral
        step = ast.workflows[0].body.block.steps[0]
        arr = step.call.args[0].value
        assert isinstance(arr, ArrayLiteral)
        assert len(arr.elements) == 3
        assert all(isinstance(e, Literal) for e in arr.elements)

    def test_nested_array(self, parser):
        """Parse nested array literal."""
        ast = parser.parse("""
        facet V(items: Json)
        workflow Test() andThen {
            s = V(items = [[1, 2], [3, 4]])
        }
        """)
        from afl.ast import ArrayLiteral
        arr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(arr, ArrayLiteral)
        assert len(arr.elements) == 2
        assert all(isinstance(e, ArrayLiteral) for e in arr.elements)

    def test_array_with_refs(self, parser):
        """Parse array with reference elements."""
        ast = parser.parse("""
        facet V(items: Json) => (output: Json)
        workflow Test(a: Long, b: Long) andThen {
            s = V(items = [$.a, $.b])
        }
        """)
        from afl.ast import ArrayLiteral
        arr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(arr, ArrayLiteral)
        assert len(arr.elements) == 2
        assert all(isinstance(e, Reference) for e in arr.elements)

    def test_map_literal(self, parser):
        """Parse map literal."""
        ast = parser.parse("""
        facet V(config: Json)
        workflow Test() andThen {
            s = V(config = #{"host": "localhost", "port": 8080})
        }
        """)
        from afl.ast import MapLiteral
        m = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(m, MapLiteral)
        assert len(m.entries) == 2
        assert m.entries[0].key == "host"
        assert m.entries[1].key == "port"

    def test_empty_map(self, parser):
        """Parse empty map literal."""
        ast = parser.parse("""
        facet V(config: Json)
        workflow Test() andThen {
            s = V(config = #{})
        }
        """)
        from afl.ast import MapLiteral
        m = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(m, MapLiteral)
        assert m.entries == []

    def test_map_with_refs(self, parser):
        """Parse map with reference values."""
        ast = parser.parse("""
        facet V(config: Json) => (output: Json)
        workflow Test(host: String, port: Long) andThen {
            s = V(config = #{"host": $.host, "port": $.port})
        }
        """)
        from afl.ast import MapLiteral
        m = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(m, MapLiteral)
        assert len(m.entries) == 2
        assert isinstance(m.entries[0].value, Reference)

    def test_index_expression(self, parser):
        """Parse index expression."""
        ast = parser.parse("""
        facet V(items: Json) => (output: Json)
        workflow Test(input: Json) andThen {
            s = V(items = $.input[0])
        }
        """)
        from afl.ast import IndexExpr
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, IndexExpr)
        assert isinstance(expr.target, Reference)
        assert isinstance(expr.index, Literal)
        assert expr.index.value == 0

    def test_index_on_array_literal(self, parser):
        """Parse index on array literal."""
        ast = parser.parse("""
        facet V(item: Json)
        workflow Test() andThen {
            s = V(item = [1, 2, 3][1])
        }
        """)
        from afl.ast import IndexExpr, ArrayLiteral
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, IndexExpr)
        assert isinstance(expr.target, ArrayLiteral)

    def test_grouped_expression(self, parser):
        """Parse grouped expression with parentheses."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test(a: Long, b: Long) andThen {
            s = V(input = ($.a + $.b) * 2)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, BinaryExpr)
        assert expr.operator == "*"
        assert isinstance(expr.left, BinaryExpr)
        assert expr.left.operator == "+"


class TestUnaryNegation:
    """Test unary negation expression parsing."""

    def test_negate_integer(self, parser):
        """Parse unary negation of integer literal."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test() andThen {
            s = V(input = -5)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, UnaryExpr)
        assert expr.operator == "-"
        assert isinstance(expr.operand, Literal)
        assert expr.operand.value == 5

    def test_negate_float(self, parser):
        """Parse unary negation of float literal."""
        ast = parser.parse("""
        facet V(input: Double)
        workflow Test() andThen {
            s = V(input = -3.14)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, UnaryExpr)
        assert expr.operator == "-"
        assert isinstance(expr.operand, Literal)
        assert expr.operand.value == 3.14

    def test_negate_input_ref(self, parser):
        """Parse unary negation of input reference."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test(x: Long) andThen {
            s = V(input = -$.x)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, UnaryExpr)
        assert expr.operator == "-"
        assert isinstance(expr.operand, Reference)

    def test_negate_grouped_expr(self, parser):
        """Parse unary negation of grouped expression."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test(a: Long, b: Long) andThen {
            s = V(input = -($.a + $.b))
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, UnaryExpr)
        assert expr.operator == "-"
        assert isinstance(expr.operand, BinaryExpr)
        assert expr.operand.operator == "+"

    def test_double_negation(self, parser):
        """Parse double negation (--5)."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test() andThen {
            s = V(input = --5)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, UnaryExpr)
        assert expr.operator == "-"
        assert isinstance(expr.operand, UnaryExpr)
        assert expr.operand.operator == "-"
        assert isinstance(expr.operand.operand, Literal)
        assert expr.operand.operand.value == 5

    def test_negation_in_arithmetic(self, parser):
        """Parse negation within arithmetic expression."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test(a: Long) andThen {
            s = V(input = $.a + -1)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        assert isinstance(expr, BinaryExpr)
        assert expr.operator == "+"
        assert isinstance(expr.right, UnaryExpr)
        assert expr.right.operator == "-"

    def test_negation_precedence_over_multiply(self, parser):
        """Unary negation binds tighter than multiplication."""
        ast = parser.parse("""
        facet V(input: Long)
        workflow Test() andThen {
            s = V(input = -2 * 3)
        }
        """)
        expr = ast.workflows[0].body.block.steps[0].call.args[0].value
        # Should be: (-2) * 3
        assert isinstance(expr, BinaryExpr)
        assert expr.operator == "*"
        assert isinstance(expr.left, UnaryExpr)
        assert isinstance(expr.right, Literal)
        assert expr.right.value == 3

    def test_negate_default_param(self, parser):
        """Parse negation as default parameter value."""
        ast = parser.parse("""
        facet V(input: Double = -90.0)
        """)
        param = ast.facets[0].sig.params[0]
        assert isinstance(param.default, UnaryExpr)
        assert param.default.operator == "-"
        assert isinstance(param.default.operand, Literal)
        assert param.default.operand.value == 90.0


class TestDocComments:
    """Tests for /** ... */ doc comments."""

    @pytest.fixture
    def parser(self):
        return AFLParser()

    def test_doc_comment_on_facet(self, parser):
        """Doc comment attached to a facet declaration."""
        ast = parser.parse("""
        namespace ns {
            /** Facet documentation. */
            facet F(x: String)
        }
        """)
        doc = ast.namespaces[0].facets[0].doc
        assert doc.description == "Facet documentation."
        assert doc.params == []
        assert doc.returns == []

    def test_doc_comment_on_event_facet(self, parser):
        """Doc comment attached to an event facet declaration."""
        ast = parser.parse("""
        namespace ns {
            /** Event facet documentation. */
            event facet EF(x: String) => (y: String)
        }
        """)
        doc = ast.namespaces[0].event_facets[0].doc
        assert doc.description == "Event facet documentation."
        assert doc.params == []
        assert doc.returns == []

    def test_doc_comment_on_workflow(self, parser):
        """Doc comment attached to a workflow declaration."""
        ast = parser.parse("""
        namespace ns {
            /** Workflow documentation. */
            event facet DoWork(x: String)
            workflow WF(input: String) andThen {
                s = DoWork(x = $.input)
            }
        }
        """)
        # The workflow doesn't have a doc comment
        assert ast.namespaces[0].workflows[0].doc is None
        # The event facet has one
        doc = ast.namespaces[0].event_facets[0].doc
        assert doc.description == "Workflow documentation."

    def test_doc_comment_on_workflow_direct(self, parser):
        """Doc comment directly on a workflow."""
        ast = parser.parse("""
        namespace ns {
            event facet DoWork(x: String)
            /** Run the workflow. */
            workflow WF(input: String) andThen {
                s = DoWork(x = $.input)
            }
        }
        """)
        assert ast.namespaces[0].workflows[0].doc.description == "Run the workflow."

    def test_doc_comment_on_namespace(self, parser):
        """Doc comment attached to a namespace."""
        ast = parser.parse("""
        /** Namespace documentation. */
        namespace ns {
            facet F(x: String)
        }
        """)
        assert ast.namespaces[0].doc.description == "Namespace documentation."

    def test_doc_comment_on_schema(self, parser):
        """Doc comment attached to a schema declaration."""
        ast = parser.parse("""
        namespace ns {
            /** Schema documentation. */
            schema MySchema {
                field1: String,
                field2: Long
            }
        }
        """)
        assert ast.namespaces[0].schemas[0].doc.description == "Schema documentation."

    def test_multiline_doc_comment(self, parser):
        """Multi-line doc comment with * prefix cleaning."""
        ast = parser.parse("""
        namespace ns {
            /**
             * Multi-line documentation.
             * Second line.
             */
            facet F(x: String)
        }
        """)
        assert ast.namespaces[0].facets[0].doc.description == "Multi-line documentation.\nSecond line."

    def test_doc_comment_with_tags(self, parser):
        """Doc comment with @param and @return tags parsed into structured form."""
        ast = parser.parse("""
        namespace ns {
            /**
             * Adds one to the input.
             * @param value The input value.
             * @return result The incremented value.
             */
            event facet AddOne(value: Long) => (result: Long)
        }
        """)
        doc = ast.namespaces[0].event_facets[0].doc
        assert doc.description == "Adds one to the input."
        assert len(doc.params) == 1
        assert doc.params[0].name == "value"
        assert doc.params[0].description == "The input value."
        assert len(doc.returns) == 1
        assert doc.returns[0].name == "result"
        assert doc.returns[0].description == "The incremented value."

    def test_no_doc_comment_is_none(self, parser):
        """Declarations without doc comments have doc=None."""
        ast = parser.parse("""
        namespace ns {
            facet F(x: String)
        }
        """)
        assert ast.namespaces[0].facets[0].doc is None
        assert ast.namespaces[0].doc is None

    def test_regular_block_comment_not_doc(self, parser):
        """Regular /* */ comments are still ignored, not treated as doc."""
        ast = parser.parse("""
        namespace ns {
            /* This is a regular comment. */
            facet F(x: String)
        }
        """)
        assert ast.namespaces[0].facets[0].doc is None

    def test_multiple_documented_declarations(self, parser):
        """Multiple declarations each with their own doc comment."""
        ast = parser.parse("""
        /** NS doc. */
        namespace ns {
            /** Facet A doc. */
            facet A(x: String)
            /** Facet B doc. */
            facet B(y: Long)
        }
        """)
        assert ast.namespaces[0].doc.description == "NS doc."
        assert ast.namespaces[0].facets[0].doc.description == "Facet A doc."
        assert ast.namespaces[0].facets[1].doc.description == "Facet B doc."

    def test_top_level_doc_comment(self, parser):
        """Doc comment on a top-level (non-namespace) facet."""
        ast = parser.parse("""
        /** Top-level facet doc. */
        facet TopF(x: String)
        """)
        assert ast.facets[0].doc.description == "Top-level facet doc."

    def test_doc_comment_multiple_params(self, parser):
        """Doc comment with multiple @param tags parsed correctly."""
        ast = parser.parse("""
        namespace ns {
            /**
             * Compute something.
             * @param graph_path Path to the edge graph
             * @param anchors_path Path to anchor node set
             * @param zoom_level Target zoom level
             */
            event facet Compute(graph_path: String, anchors_path: String, zoom_level: Long)
        }
        """)
        doc = ast.namespaces[0].event_facets[0].doc
        assert doc.description == "Compute something."
        assert len(doc.params) == 3
        assert doc.params[0].name == "graph_path"
        assert doc.params[1].name == "anchors_path"
        assert doc.params[2].name == "zoom_level"
        assert doc.params[2].description == "Target zoom level"

    def test_doc_comment_description_with_markdown(self, parser):
        """Markdown formatting preserved in description text."""
        ast = parser.parse("""
        namespace ns {
            /**
             * Computes **betweenness** sampling.
             *
             * Uses `shortest-path` routing between pairs.
             */
            facet F(x: String)
        }
        """)
        doc = ast.namespaces[0].facets[0].doc
        assert "**betweenness**" in doc.description
        assert "`shortest-path`" in doc.description

    def test_doc_comment_params_no_description(self, parser):
        """Doc comment with only @param tags and no description."""
        ast = parser.parse("""
        namespace ns {
            /**
             * @param x The x value
             * @param y The y value
             */
            facet F(x: String, y: Long)
        }
        """)
        doc = ast.namespaces[0].facets[0].doc
        assert doc.description == ""
        assert len(doc.params) == 2
        assert doc.params[0].name == "x"
        assert doc.params[1].name == "y"
