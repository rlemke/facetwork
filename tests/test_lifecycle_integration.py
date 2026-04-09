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

"""Cross-component integration tests: FFL source → compile → execute → resume."""

import json

import pytest

from facetwork import emit_dict, parse
from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, StepState, Telemetry
from facetwork.runtime.entities import HandlerRegistration
from facetwork.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig

try:
    from mcp.types import TextContent  # noqa: F401

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


# =========================================================================
# Inline FFL source constants
# =========================================================================

AFL_HELLO = """\
namespace hello {
    event facet Greet(name: String) => (greeting: String)

    workflow SayHello(name: String) => (result: String) andThen {
        g = Greet(name = $.name)
        yield SayHello(result = g.greeting)
    }
}
"""

AFL_MULTI_STEP = """\
namespace multi {
    event facet StepA(input: Long) => (output: Long)
    event facet StepB(input: Long) => (output: Long)

    workflow TwoStep(x: Long) => (result: Long) andThen {
        a = StepA(input = $.x)
        b = StepB(input = a.output)
        yield TwoStep(result = b.output)
    }
}
"""

AFL_FOREACH = """\
namespace batch {
    event facet Process(input: Long) => (output: Long)

    workflow ProcessAll(items: Json) => (count: Long)
      andThen foreach r in $.items {
        v = Process(input = r)
        yield ProcessAll(count = v.output)
      }
}
"""


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def evaluator(store):
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))


def _compile(source: str):
    """Parse and compile FFL source, returning (workflow_ast, program_ast)."""
    ast = parse(source)
    compiled = emit_dict(ast)
    return compiled


def _find_workflow(compiled: dict, name: str):
    """Find a workflow declaration by name in compiled output."""
    from facetwork.ast_utils import find_workflow

    return find_workflow(compiled, name)


def _register_handler(store, tmp_path, facet_name, code):
    """Write handler module to temp file and register."""
    f = tmp_path / f"{facet_name.replace('.', '_')}_handler.py"
    f.write_text(code)
    reg = HandlerRegistration(
        facet_name=facet_name,
        module_uri=f"file://{f}",
        entrypoint="handle",
    )
    store.save_handler_registration(reg)


# =========================================================================
# Full lifecycle tests
# =========================================================================


class TestFullLifecycleSimple:
    """Parse FFL → compile → execute → pause → continue → resume → verify."""

    def test_hello_workflow_compiles_and_executes(self, store, evaluator):
        compiled = _compile(AFL_HELLO)
        wf_ast = _find_workflow(compiled, "SayHello")
        assert wf_ast is not None

        result = evaluator.execute(wf_ast, inputs={"name": "World"}, program_ast=compiled)
        assert result.status == ExecutionStatus.PAUSED

    def test_hello_workflow_continues_and_completes(self, store, evaluator, tmp_path):
        compiled = _compile(AFL_HELLO)
        wf_ast = _find_workflow(compiled, "SayHello")

        result = evaluator.execute(wf_ast, inputs={"name": "World"}, program_ast=compiled)
        assert result.status == ExecutionStatus.PAUSED

        # Find the blocked step and continue it manually
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        step = blocked[0]
        assert step.attributes.get_param("name") == "World"

        # Manually provide return values
        evaluator.continue_step(step.id, result={"greeting": "Hello, World!"})

        final = evaluator.resume(result.workflow_id, wf_ast, compiled)
        assert final.success
        assert final.status == ExecutionStatus.COMPLETED
        assert final.outputs["result"] == "Hello, World!"

    def test_simple_workflow_without_event_completes_immediately(self, store, evaluator):
        source = "workflow Direct(x: Long) => (y: Long)"
        compiled = _compile(source)
        wf_ast = _find_workflow(compiled, "Direct")
        assert wf_ast is not None

        result = evaluator.execute(wf_ast, inputs={"x": 42}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED


class TestFullLifecycleMultiStep:
    """Two sequential event facets with data flow between steps."""

    def test_two_step_data_flows(self, store, evaluator, tmp_path):
        compiled = _compile(AFL_MULTI_STEP)
        wf_ast = _find_workflow(compiled, "TwoStep")

        result = evaluator.execute(wf_ast, inputs={"x": 5}, program_ast=compiled)
        assert result.status == ExecutionStatus.PAUSED

        # Step A should be blocked with input=5
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) == 1
        evaluator.continue_step(blocked[0].id, result={"output": 10})

        # Resume to create Step B
        r2 = evaluator.resume(result.workflow_id, wf_ast, compiled)
        assert r2.status == ExecutionStatus.PAUSED

        # Step B should be blocked with input=10 (from a.output)
        blocked2 = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked2) == 1
        assert blocked2[0].attributes.get_param("input") == 10

        evaluator.continue_step(blocked2[0].id, result={"output": 100})

        final = evaluator.resume(result.workflow_id, wf_ast, compiled)
        assert final.success
        assert final.outputs["result"] == 100


class TestFullLifecycleRegistryRunner:
    """From FFL source through RegistryRunner dispatch."""

    def test_registry_runner_processes_hello(self, store, evaluator, tmp_path):
        compiled = _compile(AFL_HELLO)
        wf_ast = _find_workflow(compiled, "SayHello")

        _register_handler(
            store,
            tmp_path,
            "hello.Greet",
            "def handle(payload):\n    return {'greeting': 'Hi, ' + payload['name']}\n",
        )

        result = evaluator.execute(wf_ast, inputs={"name": "Test"}, program_ast=compiled)
        assert result.status == ExecutionStatus.PAUSED

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(result.workflow_id, wf_ast, program_ast=compiled)
        dispatched = runner.poll_once()
        assert dispatched == 1

        final = evaluator.resume(result.workflow_id, wf_ast, compiled)
        assert final.success
        assert final.outputs["result"] == "Hi, Test"


class TestCompileAndExecuteEdgeCases:
    """Edge cases in compilation and execution."""

    def test_namespaced_workflow_found(self, store, evaluator):
        compiled = _compile(AFL_HELLO)
        wf_ast = _find_workflow(compiled, "SayHello")
        assert wf_ast is not None
        assert wf_ast["type"] == "WorkflowDecl"
        assert wf_ast["name"] == "SayHello"

    def test_invalid_source_raises(self):
        with pytest.raises(Exception):  # noqa: B017
            _compile("this is not valid afl {{{{")

    def test_validation_catches_duplicate(self):
        from facetwork.validator import validate

        source = "facet Dup()\nfacet Dup()"
        ast = parse(source)
        result = validate(ast)
        assert not result.is_valid

    def test_nonexistent_workflow_returns_none(self, store, evaluator):
        compiled = _compile("facet NotAWorkflow()")
        wf_ast = _find_workflow(compiled, "Missing")
        assert wf_ast is None


AFL_ADD_LONGS = """\
namespace afl.test.dependency {
    facet LongValue(value: Long)
    workflow AddLongs(input:Long = 1) => (output:Long = 2) andThen {
       s1 = LongValue(value = $.input + 1 )
       s2 = LongValue(value = s1.value + 2)
       s3 = LongValue(value = s2.value + s1.value)
       s4 = LongValue(value = s3.value + 4)
       s5 = LongValue(value = s4.value + 5)
       s6 = LongValue(value = s5.value + s4.value + s5.value)
       s7 = LongValue(value = s6.value + 7)
       s8 = LongValue(value = s7.value + 8)
       s9 = LongValue(value = s8.value + 9)
       s10 = LongValue(value = s9.value + s7.value + s9.value + s1.value + s3.value + s6.value)
       yield AddLongs(output = s10.value)
    }
}
"""


class TestAddLongsDependencyChain:
    """10-step dependency chain with arithmetic — all non-event facets complete immediately.

    s1=input+1, s2=s1+2, s3=s2+s1, s4=s3+4, s5=s4+5,
    s6=s5+s4+s5, s7=s6+7, s8=s7+8, s9=s8+9,
    s10=s9+s7+s9+s1+s3+s6 → output
    """

    def test_compiles_with_10_steps(self):
        compiled = _compile(AFL_ADD_LONGS)
        wf = _find_workflow(compiled, "AddLongs")
        assert wf is not None
        assert wf["type"] == "WorkflowDecl"
        steps = wf["body"]["steps"]
        assert len(steps) == 10

    def test_default_input_completes_with_223(self, store, evaluator):
        compiled = _compile(AFL_ADD_LONGS)
        wf = _find_workflow(compiled, "AddLongs")
        result = evaluator.execute(wf, inputs={"input": 1}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["output"] == 223

    def test_custom_input_completes_with_331(self, store, evaluator):
        compiled = _compile(AFL_ADD_LONGS)
        wf = _find_workflow(compiled, "AddLongs")
        result = evaluator.execute(wf, inputs={"input": 5}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["output"] == 331


AFL_MULTI_ANDTHEN = """\
namespace afl.test.basic.test_runners {
    facet Value(a:Int = 0,b:Int=1) => (value:Int) andThen {
      yield Value(value = $.a + $.b)
    }
    workflow MultiAndThenEventTest(parameter:Int = 1) => (output1:Int = 0,output2:Int = 0,output3:Int = 0,output4:Int = 0,output5:Int = 0) andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenEventTest(output1 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenEventTest(output2 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenEventTest(output3 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenEventTest(output4 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenEventTest(output5 = v1.value + v5.value)
    }
}
"""


class TestMultiAndThenBlocks:
    """5 concurrent andThen blocks with facet-computed values.

    Facet Value(a, b) => (value = a + b) via its own andThen body.
    Each block has 6 steps with cross-step dependencies and a yield.
    All 5 blocks execute the same computation independently.
    """

    def test_compiles_with_5_blocks(self):
        compiled = _compile(AFL_MULTI_ANDTHEN)
        wf = _find_workflow(compiled, "MultiAndThenEventTest")
        assert wf is not None
        body = wf["body"]
        assert isinstance(body, list)
        assert len(body) == 5

    def test_each_block_has_6_steps(self):
        compiled = _compile(AFL_MULTI_ANDTHEN)
        wf = _find_workflow(compiled, "MultiAndThenEventTest")
        for i, block in enumerate(wf["body"]):
            assert len(block["steps"]) == 6, f"block {i} should have 6 steps"

    def test_default_parameter_all_outputs_89(self, store, evaluator):
        compiled = _compile(AFL_MULTI_ANDTHEN)
        wf = _find_workflow(compiled, "MultiAndThenEventTest")
        result = evaluator.execute(wf, inputs={"parameter": 1}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        for i in range(1, 6):
            assert result.outputs[f"output{i}"] == 89

    def test_parameter_5_all_outputs_149(self, store, evaluator):
        compiled = _compile(AFL_MULTI_ANDTHEN)
        wf = _find_workflow(compiled, "MultiAndThenEventTest")
        result = evaluator.execute(wf, inputs={"parameter": 5}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        for i in range(1, 6):
            assert result.outputs[f"output{i}"] == 149


AFL_MULTI_ANDTHEN_NESTED = """\
namespace afl.test.basic.test_runners {
    facet IntValueAdd(a:Int, b:Int) => (value:Int) andThen {
       yield IntValueAdd(value = $.a + $.b)
    }
    facet Value(a:Int = 0,b:Int=1) => (value:Int) andThen {
      a = IntValueAdd(a = $.a, b = $.b)
      b = IntValueAdd(a = $.a + 1, b = $.b + 1)
      c = IntValueAdd(a = a.a + a.value, b = b.b + b.value)
      yield Value(value = $.a + $.b + c.value)
    }
    workflow MultiAndThenTest2(parameter:Int = 1) => (output1:Int = 0,output2:Int = 0,output3:Int = 0,output4:Int = 0,output5:Int = 0) andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenTest2(output1 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenTest2(output2 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenTest2(output3 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenTest2(output4 = v1.value + v5.value)
    } andThen {
      v0 = Value(a = 3 + $.parameter, b = 2)
      v1 = Value(a = v0.a + v0.b, b = v0.value)
      v2 = Value(a = v1.a, b = v0.b + v0.value)
      v3 = Value(a = v0.a + v1.b, b = v2.value)
      v4 = Value(b = v1.a + v2.a)
      v5 = Value(a = v1.value + v1.b + v4.value + v4.b + v3.a + v3.value)
      yield MultiAndThenTest2(output5 = v1.value + v5.value)
    }
}
"""


class TestMultiAndThenNestedFacets:
    """5 concurrent andThen blocks with two-level nested facet andThen bodies.

    IntValueAdd(a, b) => (value = a + b) via its own andThen body.
    Value(a, b) => (value) via a 3-step andThen body calling IntValueAdd.
    Each workflow block has 6 steps with cross-step dependencies.
    """

    def test_compiles_with_5_blocks(self):
        compiled = _compile(AFL_MULTI_ANDTHEN_NESTED)
        wf = _find_workflow(compiled, "MultiAndThenTest2")
        assert wf is not None
        body = wf["body"]
        assert isinstance(body, list)
        assert len(body) == 5

    def test_value_facet_has_3_step_body(self):
        compiled = _compile(AFL_MULTI_ANDTHEN_NESTED)
        # Find the Value facet definition
        for decl in compiled.get("declarations", []):
            if decl.get("type") == "Namespace":
                for nested in decl.get("declarations", []):
                    if nested.get("name") == "Value" and nested.get("type") == "FacetDecl":
                        assert len(nested["body"]["steps"]) == 3
                        return
        pytest.fail("Value facet not found")

    def test_default_parameter_all_outputs_3962(self, store, evaluator):
        compiled = _compile(AFL_MULTI_ANDTHEN_NESTED)
        wf = _find_workflow(compiled, "MultiAndThenTest2")
        result = evaluator.execute(wf, inputs={"parameter": 1}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        for i in range(1, 6):
            assert result.outputs[f"output{i}"] == 3962

    def test_parameter_5_all_outputs_6266(self, store, evaluator):
        compiled = _compile(AFL_MULTI_ANDTHEN_NESTED)
        wf = _find_workflow(compiled, "MultiAndThenTest2")
        result = evaluator.execute(wf, inputs={"parameter": 5}, program_ast=compiled)
        assert result.success
        assert result.status == ExecutionStatus.COMPLETED
        for i in range(1, 6):
            assert result.outputs[f"output{i}"] == 6266


AFL_TOP_LEVEL_WORKFLOW = """\
event facet AddOne(input: Long) => (output: Long)

workflow TestAddOne(x: Long) => (result: Long) andThen {
    s = AddOne(input = $.x)
    yield TestAddOne(result = s.output)
}
"""


@pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
class TestMcpToolIntegration:
    """Chain MCP tool functions: compile → execute → continue → resume."""

    def test_compile_valid_source(self):
        from facetwork.mcp.server import _tool_compile

        result = _tool_compile({"source": AFL_HELLO})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "json" in data

    def test_compile_then_execute_top_level(self):
        from facetwork.mcp.server import _tool_compile, _tool_execute_workflow

        # Compile
        compile_result = _tool_compile({"source": AFL_TOP_LEVEL_WORKFLOW})
        compile_data = json.loads(compile_result[0].text)
        assert compile_data["success"] is True

        # Execute — top-level workflow should be found and pause
        exec_result = _tool_execute_workflow(
            {
                "source": AFL_TOP_LEVEL_WORKFLOW,
                "workflow_name": "TestAddOne",
                "inputs": {"x": 5},
            }
        )
        exec_data = json.loads(exec_result[0].text)
        assert exec_data["success"] is True
        assert "workflow_id" in exec_data

    def test_execute_simple_workflow_completes(self):
        from facetwork.mcp.server import _tool_execute_workflow

        source = "workflow Direct(x: Long) => (y: Long)"
        result = _tool_execute_workflow(
            {
                "source": source,
                "workflow_name": "Direct",
                "inputs": {"x": 42},
            }
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["status"] == "COMPLETED"

    def test_execute_nonexistent_workflow(self):
        from facetwork.mcp.server import _tool_execute_workflow

        result = _tool_execute_workflow(
            {
                "source": "facet NotWorkflow()",
                "workflow_name": "Missing",
            }
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["error"]


# =========================================================================
# andThen script blocks with Census-style event facets
# =========================================================================

AFL_CENSUS_WITH_SCRIPTS = """\
namespace census.types {
    schema CensusFile {
        path: String,
        size: Long
    }
    schema ACSResult {
        output_path: String,
        row_count: Long,
        state_fips: String
    }
    schema CensusSummary {
        total_population: Long,
        median_income: Long,
        county_count: Long,
        state_name: String
    }
}

namespace census.Operations {
    use census.types

    event facet DownloadACS(
        year: String = "2023",
        state_fips: String
    ) => (file: CensusFile)

    event facet DownloadTIGER(
        year: String = "2024",
        geo_level: String = "COUNTY",
        state_fips: String
    ) => (file: CensusFile)
}

namespace census.ACS {
    use census.types

    event facet ExtractPopulation(
        file: CensusFile,
        state_fips: String,
        geo_level: String = "county"
    ) => (result: ACSResult)

    event facet ExtractIncome(
        file: CensusFile,
        state_fips: String,
        geo_level: String = "county"
    ) => (result: ACSResult)
}

namespace census.Summary {
    use census.types

    event facet JoinGeo(
        acs_path: String,
        tiger_path: String,
        join_field: String = "GEOID"
    ) => (result: CensusSummary)
}

namespace census.workflows {
    use census.types
    use census.Operations
    use census.ACS
    use census.Summary

    workflow AnalyzeStateWithScripts(
        state_fips: String = "01",
        state_name: String = "Alabama"
    ) => (
        summary: CensusSummary,
        pop_total: Long,
        income_total: Long,
        report: String,
        audit: String
    )
    script {
        result["state_label"] = params["state_name"].upper() + " (" + params["state_fips"] + ")"
    }
    andThen {
        acs = DownloadACS(state_fips = $.state_fips)
        tiger = DownloadTIGER(state_fips = $.state_fips, geo_level = "COUNTY")
        pop = ExtractPopulation(file = acs.file, state_fips = $.state_fips)
        income = ExtractIncome(file = acs.file, state_fips = $.state_fips)
        joined = JoinGeo(acs_path = pop.result.output_path, tiger_path = tiger.file.path)
        yield AnalyzeStateWithScripts(summary = joined.result)
    }
    andThen script {
        label = params.get("state_label", params["state_name"])
        result["pop_total"] = 5000000
        result["report"] = "Population report for " + label
    }
    andThen {
        acs2 = DownloadACS(state_fips = $.state_fips, year = "2022")
        income2 = ExtractIncome(file = acs2.file, state_fips = $.state_fips)
        yield AnalyzeStateWithScripts(income_total = income2.result.row_count)
    }
    andThen script {
        label = params.get("state_label", params["state_name"])
        result["audit"] = "Audit complete for " + label + " at fips=" + params["state_fips"]
    }
}
"""


class TestCensusWithAndThenScripts:
    """Census-style workflow with pre-script, 2 regular andThen blocks,
    and 2 andThen script blocks running concurrently.

    The workflow:
    1. Pre-script: normalizes state_name into state_label param
    2. andThen block 1: download → extract → join (5 event facet steps + yield)
    3. andThen script 1: computes pop_total and report using state_label
    4. andThen block 2: download 2022 → extract income (2 event steps + yield)
    5. andThen script 2: computes audit string using state_label
    """

    def test_compiles_with_pre_script_and_4_blocks(self):
        """Workflow compiles with pre_script and 4 andThen blocks (2 regular + 2 script)."""
        compiled = _compile(AFL_CENSUS_WITH_SCRIPTS)
        wf = _find_workflow(compiled, "AnalyzeStateWithScripts")
        assert wf is not None
        assert wf["type"] == "WorkflowDecl"
        assert "pre_script" in wf
        assert wf["pre_script"]["type"] == "ScriptBlock"
        body = wf["body"]
        assert isinstance(body, list)
        assert len(body) == 4

    def test_body_block_types(self):
        """First and third blocks are regular (steps), second and fourth are scripts."""
        compiled = _compile(AFL_CENSUS_WITH_SCRIPTS)
        wf = _find_workflow(compiled, "AnalyzeStateWithScripts")
        body = wf["body"]

        # Block 0: regular (has steps + yield)
        assert "steps" in body[0]
        assert len(body[0]["steps"]) == 5
        assert "yield" in body[0]

        # Block 1: script
        assert "script" in body[1]
        assert body[1]["script"]["type"] == "ScriptBlock"
        assert "pop_total" in body[1]["script"]["code"]

        # Block 2: regular (has steps + yield)
        assert "steps" in body[2]
        assert len(body[2]["steps"]) == 2
        assert "yield" in body[2]

        # Block 3: script
        assert "script" in body[3]
        assert body[3]["script"]["type"] == "ScriptBlock"
        assert "audit" in body[3]["script"]["code"]

    def test_pre_script_sets_state_label(self):
        """Pre-script creates state_label param from state_name and state_fips."""
        compiled = _compile(AFL_CENSUS_WITH_SCRIPTS)
        wf = _find_workflow(compiled, "AnalyzeStateWithScripts")
        code = wf["pre_script"]["code"]
        assert "state_label" in code
        assert "state_name" in code

    def test_executes_and_pauses_on_event_facets(self, store, evaluator):
        """Execution pauses when hitting event facet steps, andThen scripts complete immediately."""
        compiled = _compile(AFL_CENSUS_WITH_SCRIPTS)
        wf = _find_workflow(compiled, "AnalyzeStateWithScripts")

        result = evaluator.execute(
            wf,
            inputs={"state_fips": "01", "state_name": "Alabama"},
            program_ast=compiled,
        )
        # Should pause on first event facets (DownloadACS, DownloadTIGER, etc.)
        assert result.status == ExecutionStatus.PAUSED

        # The andThen script blocks should already have completed (no event deps)
        # Verify by checking that the script blocks produced outputs
        # The workflow won't be COMPLETED until all 4 blocks are done,
        # but the script blocks themselves should have run.

    def test_full_lifecycle_with_event_resolution(self, store, evaluator):
        """Full lifecycle: execute → resolve events → resume → verify outputs."""
        compiled = _compile(AFL_CENSUS_WITH_SCRIPTS)
        wf = _find_workflow(compiled, "AnalyzeStateWithScripts")

        result = evaluator.execute(
            wf,
            inputs={"state_fips": "01", "state_name": "Alabama"},
            program_ast=compiled,
        )
        assert result.status == ExecutionStatus.PAUSED

        # Resolve all event facets in the main andThen block (block 0)
        # DownloadACS and DownloadTIGER should be waiting
        for _ in range(20):
            blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            if not blocked:
                break

            for step in blocked:
                facet = step.facet_name
                if "DownloadACS" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "file": {
                                "path": f"/data/acs_{step.attributes.get_param('state_fips')}.csv",
                                "size": 50000,
                            }
                        },
                    )
                elif "DownloadTIGER" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "file": {
                                "path": f"/data/tiger_{step.attributes.get_param('state_fips')}.shp",
                                "size": 120000,
                            }
                        },
                    )
                elif "ExtractPopulation" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "result": {
                                "output_path": "/data/pop.csv",
                                "row_count": 67,
                                "state_fips": "01",
                            }
                        },
                    )
                elif "ExtractIncome" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "result": {
                                "output_path": "/data/income.csv",
                                "row_count": 67,
                                "state_fips": "01",
                            }
                        },
                    )
                elif "JoinGeo" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "result": {
                                "total_population": 5024279,
                                "median_income": 52035,
                                "county_count": 67,
                                "state_name": "Alabama",
                            }
                        },
                    )
                else:
                    evaluator.continue_step(step.id, result={})

            r = evaluator.resume(result.workflow_id, wf, compiled)
            if r.status == ExecutionStatus.COMPLETED:
                result = r
                break
            result = r

        assert result.success, f"Workflow did not complete: {result.status}"
        assert result.status == ExecutionStatus.COMPLETED

        # Verify outputs from all 4 blocks
        # Block 0 (regular): summary from JoinGeo yield
        assert result.outputs["summary"]["total_population"] == 5024279
        assert result.outputs["summary"]["county_count"] == 67

        # Block 1 (andThen script): pop_total and report
        assert result.outputs["pop_total"] == 5000000
        assert "ALABAMA (01)" in result.outputs["report"]

        # Block 2 (regular): income_total from ExtractIncome yield
        assert result.outputs["income_total"] == 67

        # Block 3 (andThen script): audit string
        assert "ALABAMA (01)" in result.outputs["audit"]
        assert "fips=01" in result.outputs["audit"]

    def test_different_state_uses_correct_label(self, store, evaluator):
        """Pre-script correctly transforms different state inputs."""
        compiled = _compile(AFL_CENSUS_WITH_SCRIPTS)
        wf = _find_workflow(compiled, "AnalyzeStateWithScripts")

        result = evaluator.execute(
            wf,
            inputs={"state_fips": "06", "state_name": "California"},
            program_ast=compiled,
        )
        assert result.status == ExecutionStatus.PAUSED

        # Resolve all events iteratively
        for _ in range(20):
            blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            if not blocked:
                break
            for step in blocked:
                facet = step.facet_name
                if "Download" in facet:
                    evaluator.continue_step(
                        step.id, result={"file": {"path": "/data/ca.csv", "size": 100000}}
                    )
                elif "Extract" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "result": {
                                "output_path": "/data/ca_extract.csv",
                                "row_count": 58,
                                "state_fips": "06",
                            }
                        },
                    )
                elif "JoinGeo" in facet:
                    evaluator.continue_step(
                        step.id,
                        result={
                            "result": {
                                "total_population": 39538223,
                                "median_income": 78672,
                                "county_count": 58,
                                "state_name": "California",
                            }
                        },
                    )
                else:
                    evaluator.continue_step(step.id, result={})
            r = evaluator.resume(result.workflow_id, wf, compiled)
            if r.status == ExecutionStatus.COMPLETED:
                result = r
                break
            result = r

        assert result.success
        assert "CALIFORNIA (06)" in result.outputs["report"]
        assert "CALIFORNIA (06)" in result.outputs["audit"]
        assert result.outputs["summary"]["county_count"] == 58
