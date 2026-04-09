"""Tests for the devops-deploy example handlers."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestSharedUtils — utility function tests
# ---------------------------------------------------------------------------
class TestSharedUtils:
    def test_build_image_returns_fields(self):
        from handlers.shared.deploy_utils import build_image

        result = build_image("myapp", "1.0.0", "gcr.io/proj")
        assert "image_tag" in result
        assert "digest" in result
        assert "size_mb" in result
        assert result["image_tag"] == "gcr.io/proj/myapp:1.0.0"
        assert result["digest"].startswith("sha256:")

    def test_build_image_deterministic(self):
        from handlers.shared.deploy_utils import build_image

        r1 = build_image("svc", "2.0", "reg")
        r2 = build_image("svc", "2.0", "reg")
        assert r1 == r2

    def test_run_tests_returns_tuple(self):
        from handlers.shared.deploy_utils import run_tests

        passed, total, failed = run_tests("myapp", "1.0.0")
        assert isinstance(passed, bool)
        assert total >= 20
        assert failed >= 0

    def test_analyze_deploy_risk_production(self):
        from handlers.shared.deploy_utils import analyze_deploy_risk

        level, score, factors = analyze_deploy_risk("svc", "1.0", "production", 50)
        assert level in ("low", "medium", "critical")
        assert "production target" in factors

    def test_normalize_config_structure(self):
        from handlers.shared.deploy_utils import normalize_config

        cfg = normalize_config("myapp", "staging", 3, "1000m", "1Gi")
        assert cfg["namespace"] == "myapp-staging"
        assert cfg["replicas"] == 3
        assert cfg["image_pull_policy"] == "IfNotPresent"

    def test_normalize_config_production_policy(self):
        from handlers.shared.deploy_utils import normalize_config

        cfg = normalize_config("myapp", "production", 2, "500m", "512Mi")
        assert cfg["image_pull_policy"] == "Always"

    def test_check_health_returns_tuple(self):
        from handlers.shared.deploy_utils import check_health

        healthy, results = check_health("svc", "deploy-abc")
        assert isinstance(healthy, bool)
        assert isinstance(results, dict)

    def test_rollback_deployment_fields(self):
        from handlers.shared.deploy_utils import rollback_deployment

        report = rollback_deployment("myapp", "deploy-123", "health check failed")
        assert "rollback_id" in report
        assert report["service"] == "myapp"
        assert report["reason"] == "health check failed"
        assert report["status"] == "rolled_back"


# ---------------------------------------------------------------------------
# TestBuildHandlers — build handler wrapper tests
# ---------------------------------------------------------------------------
class TestBuildHandlers:
    def test_handle_build_image(self):
        from handlers.build.build_handlers import handle_build_image

        result = handle_build_image({"service": "api", "version": "1.0", "registry": "reg"})
        assert "image_tag" in result
        assert result["image_tag"] == "reg/api:1.0"

    def test_handle_run_tests(self):
        from handlers.build.build_handlers import handle_run_tests

        result = handle_run_tests({"service": "api", "version": "1.0"})
        assert "passed" in result
        assert "total_tests" in result
        assert "failed_count" in result

    def test_handle_analyze_deploy_risk(self):
        from handlers.build.build_handlers import handle_analyze_deploy_risk

        result = handle_analyze_deploy_risk(
            {
                "service": "api",
                "version": "1.0",
                "environment": "staging",
                "change_size": 50,
            }
        )
        assert "risk_level" in result
        assert "risk_score" in result
        assert "risk_factors" in result

    def test_handle_build_image_step_log(self):
        from handlers.build.build_handlers import handle_build_image

        messages: list[tuple[str, str]] = []
        handle_build_image(
            {
                "service": "api",
                "version": "1.0",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Built" in messages[0][0]


# ---------------------------------------------------------------------------
# TestDeployHandlers — deploy handler wrapper tests
# ---------------------------------------------------------------------------
class TestDeployHandlers:
    def test_handle_normalize_config(self):
        from handlers.deploy.deploy_handlers import handle_normalize_config

        result = handle_normalize_config(
            {
                "service": "api",
                "environment": "staging",
                "replicas": 3,
            }
        )
        assert "config" in result
        assert result["config"]["replicas"] == 3

    def test_handle_apply_deployment(self):
        from handlers.deploy.deploy_handlers import handle_apply_deployment

        result = handle_apply_deployment(
            {
                "service": "api",
                "image_tag": "reg/api:1.0",
                "config": {"namespace": "api-staging"},
            }
        )
        assert "deployment_id" in result
        assert result["status"] == "applied"

    def test_handle_apply_deployment_json_config(self):
        from handlers.deploy.deploy_handlers import handle_apply_deployment

        result = handle_apply_deployment(
            {
                "service": "api",
                "image_tag": "reg/api:1.0",
                "config": json.dumps({"namespace": "api-staging"}),
            }
        )
        assert "deployment_id" in result
        assert result["status"] == "applied"

    def test_handle_wait_for_rollout(self):
        from handlers.deploy.deploy_handlers import handle_wait_for_rollout

        result = handle_wait_for_rollout(
            {
                "deployment_id": "deploy-abc",
                "timeout_seconds": 300,
            }
        )
        assert result["ready"] is True
        assert "ready_replicas" in result
        assert "message" in result


# ---------------------------------------------------------------------------
# TestMonitorHandlers — monitor handler wrapper tests
# ---------------------------------------------------------------------------
class TestMonitorHandlers:
    def test_handle_check_health(self):
        from handlers.monitor.monitor_handlers import handle_check_health

        result = handle_check_health(
            {
                "service": "api",
                "deployment_id": "deploy-abc",
            }
        )
        assert "result" in result
        assert "healthy" in result["result"]
        assert "checks_run" in result["result"]

    def test_handle_triage_incident(self):
        from handlers.monitor.monitor_handlers import handle_triage_incident

        result = handle_triage_incident(
            {
                "service": "api",
                "health_results": {"readiness": "fail", "liveness": "pass"},
                "deployment_id": "deploy-abc",
            }
        )
        assert "severity" in result
        assert "recommendation" in result
        assert "failed_checks" in result

    def test_handle_triage_incident_step_log_callable(self):
        from handlers.monitor.monitor_handlers import handle_triage_incident

        messages: list[tuple[str, str]] = []
        handle_triage_incident(
            {
                "service": "api",
                "health_results": {"readiness": "fail"},
                "deployment_id": "deploy-abc",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Triage" in messages[0][0]


# ---------------------------------------------------------------------------
# TestRollbackHandlers — rollback handler wrapper tests
# ---------------------------------------------------------------------------
class TestRollbackHandlers:
    def test_handle_rollback_deployment(self):
        from handlers.rollback.rollback_handlers import handle_rollback_deployment

        result = handle_rollback_deployment(
            {
                "service": "api",
                "deployment_id": "deploy-abc",
                "reason": "health check failed",
            }
        )
        assert "report" in result
        assert result["report"]["status"] == "rolled_back"

    def test_handle_verify_rollback(self):
        from handlers.rollback.rollback_handlers import handle_verify_rollback

        result = handle_verify_rollback(
            {
                "service": "api",
                "rollback_id": "rb-12345678",
            }
        )
        assert "verified" in result
        assert "message" in result

    def test_handle_rollback_json_deser(self):
        from handlers.rollback.rollback_handlers import handle_rollback_deployment

        log: list[dict] = []
        handle_rollback_deployment(
            {
                "service": "api",
                "deployment_id": "deploy-abc",
                "reason": "unhealthy",
                "_step_log": log,
            }
        )
        assert len(log) == 1
        assert "Rolled back" in log[0]["message"]


# ---------------------------------------------------------------------------
# TestDispatch — dispatch table structure and routing
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_build_dispatch_count(self):
        from handlers.build.build_handlers import _DISPATCH

        assert len(_DISPATCH) == 3

    def test_deploy_dispatch_count(self):
        from handlers.deploy.deploy_handlers import _DISPATCH

        assert len(_DISPATCH) == 3

    def test_monitor_dispatch_count(self):
        from handlers.monitor.monitor_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_rollback_dispatch_count(self):
        from handlers.rollback.rollback_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_all_dispatch_names_have_namespace_prefix(self):
        from handlers.build.build_handlers import _DISPATCH as d1
        from handlers.deploy.deploy_handlers import _DISPATCH as d2
        from handlers.monitor.monitor_handlers import _DISPATCH as d3
        from handlers.rollback.rollback_handlers import _DISPATCH as d4

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys()) + list(d4.keys())
        assert len(all_names) == 10
        assert all(n.startswith("deploy.") for n in all_names)


# ---------------------------------------------------------------------------
# TestCompilation — FFL parsing and AST checks
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "deploy.ffl")
        with open(afl_path) as f:
            source = f.read()
        return FFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 3

    def test_event_facet_count(self, parsed_ast):
        event_facets = []
        for ns in parsed_ast.namespaces:
            event_facets.extend(ns.event_facets)
        assert len(event_facets) == 10

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 2

    def test_namespace_count(self, parsed_ast):
        assert len(parsed_ast.namespaces) == 7

    def test_when_block_present(self, parsed_ast):
        """Verify andThen when block appears in DeployService workflow."""
        from facetwork.ast import WhenBlock

        wf_ns = [ns for ns in parsed_ast.namespaces if ns.name == "deploy.workflows"]
        deploy_wf = [w for w in wf_ns[0].workflows if w.sig.name == "DeployService"][0]
        body = deploy_wf.body
        assert isinstance(body, list)
        when_body = body[1]
        assert when_body.when is not None
        assert isinstance(when_body.when, WhenBlock)
        assert len(when_body.when.cases) == 3

    def test_foreach_present(self, parsed_ast):
        """Verify andThen foreach appears in BatchDeploy workflow."""
        from facetwork.ast import ForeachClause

        wf_ns = [ns for ns in parsed_ast.namespaces if ns.name == "deploy.workflows"]
        batch_wf = [w for w in wf_ns[0].workflows if w.sig.name == "BatchDeploy"][0]
        body = batch_wf.body
        assert body.foreach is not None
        assert isinstance(body.foreach, ForeachClause)
        assert body.foreach.variable == "svc"

    def test_array_type_present(self, parsed_ast):
        """Verify [String] array type annotation appears in the AST."""
        from facetwork.ast import ArrayType

        found_array = False
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                for p in ef.sig.params:
                    if isinstance(p.type, ArrayType):
                        found_array = True
            for wf in ns.workflows:
                for p in wf.sig.params:
                    if isinstance(p.type, ArrayType):
                        found_array = True
        assert found_array, "Expected at least one [Type] array annotation"

    def test_prompt_block_present(self, parsed_ast):
        """Verify prompt blocks appear on event facets."""
        from facetwork.ast import PromptBlock

        prompt_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                body = ef.body
                if isinstance(body, PromptBlock):
                    prompt_count += 1
        assert prompt_count >= 2, "Expected at least 2 prompt blocks"


# ---------------------------------------------------------------------------
# TestAgentIntegration — end-to-end handler registration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_registry_runner_poll_once(self):
        """RegistryRunner dispatches all handlers via ToolRegistry."""
        from handlers.build.build_handlers import _DISPATCH as d1
        from handlers.deploy.deploy_handlers import _DISPATCH as d2
        from handlers.monitor.monitor_handlers import _DISPATCH as d3
        from handlers.rollback.rollback_handlers import _DISPATCH as d4

        from facetwork.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "BuildImage",
            "RunTests",
            "AnalyzeDeployRisk",
            "NormalizeConfig",
            "ApplyDeployment",
            "WaitForRollout",
            "CheckHealth",
            "TriageIncident",
            "RollbackDeployment",
            "VerifyRollback",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_registry_runner_handler_names(self):
        """Verify all dispatch tables have correct namespace prefixes."""
        from handlers.build.build_handlers import _DISPATCH as d1
        from handlers.deploy.deploy_handlers import _DISPATCH as d2
        from handlers.monitor.monitor_handlers import _DISPATCH as d3
        from handlers.rollback.rollback_handlers import _DISPATCH as d4

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys()) + list(d4.keys())
        assert len(all_names) == 10
        assert all(n.startswith("deploy.") for n in all_names)
