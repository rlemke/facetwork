"""Tests for Monte Carlo risk analysis handlers."""

from __future__ import annotations

import json
import os

import pytest

# ---------------------------------------------------------------------------
# TestMathUtils
# ---------------------------------------------------------------------------


class TestMathUtils:
    """Tests for shared math utilities."""

    def test_gbm_path_shape(self):
        """GBM returns correct number of PnL paths."""
        from handlers.shared.math_utils import generate_correlated_gbm_paths

        positions = [
            {"value": 100_000, "volatility": 0.20, "expected_return": 0.10},
            {"value": 50_000, "volatility": 0.15, "expected_return": 0.08},
        ]
        cholesky = [[1.0, 0.0], [0.6, 0.8]]
        pnl = generate_correlated_gbm_paths(positions, cholesky, 500, 10, seed=42)
        assert len(pnl) == 500
        assert all(isinstance(x, float) for x in pnl)

    def test_gbm_mean_reasonable(self):
        """GBM mean PnL is in a reasonable range (not wildly off)."""
        from handlers.shared.math_utils import generate_correlated_gbm_paths

        positions = [
            {"value": 100_000, "volatility": 0.20, "expected_return": 0.10},
        ]
        cholesky = [[1.0]]
        pnl = generate_correlated_gbm_paths(positions, cholesky, 5000, 10, seed=99)
        mean = sum(pnl) / len(pnl)
        # 10-day drift on 100K with 10% annual return should be small
        assert abs(mean) < 50_000  # within 50% of value

    def test_cholesky_validity(self):
        """Cholesky decomposition satisfies L @ L^T = original."""
        from handlers.shared.math_utils import compute_cholesky

        corr = [[1.0, 0.5], [0.5, 1.0]]
        chol = compute_cholesky(corr)
        # Reconstruct: L @ L^T
        n = len(chol)
        for i in range(n):
            for j in range(n):
                reconstructed = sum(chol[i][k] * chol[j][k] for k in range(n))
                assert abs(reconstructed - corr[i][j]) < 1e-10

    def test_var_on_known_distribution(self):
        """VaR at 95% on a uniform distribution."""
        from handlers.shared.math_utils import calculate_var

        # 1000 uniform values from -100 to 100
        pnl = [float(i) for i in range(-500, 500)]
        var_95 = calculate_var(pnl, 0.95)
        # 5th percentile of [-500, 499] should be around -450
        assert var_95 < 0
        assert -500 <= var_95 <= -400

    def test_cvar_exceeds_var(self):
        """CVaR (expected shortfall) should be more negative than VaR."""
        from handlers.shared.math_utils import calculate_cvar, calculate_var

        pnl = [float(x) for x in range(-500, 500)]
        var_95 = calculate_var(pnl, 0.95)
        cvar_95 = calculate_cvar(pnl, 0.95)
        assert cvar_95 <= var_95

    def test_greeks_structure(self):
        """Greeks computation returns expected keys and list lengths."""
        from handlers.shared.math_utils import compute_finite_difference_greeks

        positions = [
            {"value": 100_000, "volatility": 0.20},
            {"value": 50_000, "volatility": 0.30},
        ]
        cholesky = [[1.0, 0.0], [0.5, 0.866]]
        greeks = compute_finite_difference_greeks(positions, cholesky)

        assert len(greeks["delta"]) == 2
        assert len(greeks["gamma"]) == 2
        assert len(greeks["vega"]) == 2
        assert isinstance(greeks["portfolio_delta"], float)
        assert isinstance(greeks["portfolio_vega"], float)


# ---------------------------------------------------------------------------
# TestMarketData
# ---------------------------------------------------------------------------


class TestMarketData:
    """Tests for market data handlers."""

    def test_load_portfolio_default(self):
        """LoadPortfolio returns a portfolio with 5 positions."""
        from handlers.market_data.market_handlers import handle_load_portfolio

        result = handle_load_portfolio({"portfolio_name": "default"})
        portfolio = result["portfolio"]
        assert portfolio["name"] == "default"
        assert len(portfolio["positions"]) == 5
        assert portfolio["total_value"] > 0
        assert portfolio["base_currency"] == "USD"

    def test_load_portfolio_custom_name(self):
        """LoadPortfolio uses the provided name."""
        from handlers.market_data.market_handlers import handle_load_portfolio

        result = handle_load_portfolio({"portfolio_name": "aggressive"})
        assert result["portfolio"]["name"] == "aggressive"

    def test_fetch_historical_data(self):
        """FetchHistoricalData returns correlation matrix and Cholesky."""
        from handlers.market_data.market_handlers import handle_fetch_historical_data

        positions = [
            {"asset_id": "SPY"},
            {"asset_id": "AAPL"},
            {"asset_id": "GOOGL"},
            {"asset_id": "TLT"},
            {"asset_id": "GLD"},
        ]
        result = handle_fetch_historical_data(
            {
                "asset_ids": positions,
                "lookback_days": 252,
            }
        )
        corr = result["correlation"]
        assert len(corr["matrix"]) == 5
        assert len(corr["cholesky"]) == 5
        assert len(corr["asset_ids"]) == 5
        # Diagonal of correlation should be 1
        for i in range(5):
            assert abs(corr["matrix"][i][i] - 1.0) < 1e-10


# ---------------------------------------------------------------------------
# TestSimulation
# ---------------------------------------------------------------------------


class TestSimulation:
    """Tests for simulation handlers."""

    def test_simulate_batch_result_structure(self):
        """SimulateBatch returns expected result fields."""
        from handlers.simulation.simulation_handlers import handle_simulate_batch

        positions = [
            {"value": 100_000, "volatility": 0.20, "expected_return": 0.10},
            {"value": 50_000, "volatility": 0.15, "expected_return": 0.08},
        ]
        cholesky = [[1.0, 0.0], [0.6, 0.8]]
        result = handle_simulate_batch(
            {
                "positions": positions,
                "cholesky": cholesky,
                "num_paths": 100,
                "time_horizon_days": 10,
                "batch_id": 3,
                "random_seed": 42,
            }
        )
        batch = result["result"]
        assert batch["batch_id"] == 3
        assert batch["num_paths"] == 100
        assert len(batch["pnl_distribution"]) == 100
        assert isinstance(batch["mean_pnl"], int)
        assert isinstance(batch["worst_pnl"], int)

    def test_simulate_batch_json_string_params(self):
        """SimulateBatch handles JSON string params."""
        from handlers.simulation.simulation_handlers import handle_simulate_batch

        positions = json.dumps(
            [
                {"value": 100_000, "volatility": 0.20, "expected_return": 0.10},
            ]
        )
        cholesky = json.dumps([[1.0]])
        result = handle_simulate_batch(
            {
                "positions": positions,
                "cholesky": cholesky,
                "num_paths": 10,
                "batch_id": 0,
            }
        )
        assert len(result["result"]["pnl_distribution"]) == 10

    def test_simulate_stress_result_fields(self):
        """SimulateStress returns scenario name and PnL."""
        from handlers.simulation.simulation_handlers import handle_simulate_stress

        positions = [
            {"asset_id": "SPY", "value": 225000.0},
            {"asset_id": "AAPL", "value": 35000.0},
        ]
        result = handle_simulate_stress(
            {
                "positions": positions,
                "scenario_name": "market_crash",
                "shock_factors": [-0.20, -0.30],
            }
        )
        stress = result["result"]
        assert stress["scenario_name"] == "market_crash"
        assert stress["portfolio_pnl"] < 0  # crash should lose money
        assert stress["worst_position"] in ("SPY", "AAPL")
        assert stress["best_position"] in ("SPY", "AAPL")


# ---------------------------------------------------------------------------
# TestAnalytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    """Tests for analytics handlers."""

    def test_compute_var(self):
        """ComputeVaR returns metrics at 95% and 99%."""
        from handlers.analytics.analytics_handlers import handle_compute_var

        pnl = [float(x) for x in range(-500, 500)]
        result = handle_compute_var(
            {
                "pnl_distributions": pnl,
                "confidence_levels": [0.95, 0.99],
            }
        )
        metrics = result["metrics"]
        assert metrics["var_95"] < 0
        assert metrics["var_99"] < metrics["var_95"]  # 99% is more extreme
        assert "cvar_95" in metrics
        assert "cvar_99" in metrics
        assert "sharpe_ratio" in metrics

    def test_cvar_exceeds_var_in_handler(self):
        """CVaR should be at least as extreme as VaR from handler output."""
        from handlers.analytics.analytics_handlers import handle_compute_var

        pnl = [float(x) for x in range(-1000, 1000)]
        result = handle_compute_var({"pnl_distributions": pnl})
        m = result["metrics"]
        assert m["cvar_95"] <= m["var_95"]
        assert m["cvar_99"] <= m["var_99"]

    def test_compute_greeks(self):
        """ComputeGreeks returns delta, gamma, vega arrays."""
        from handlers.analytics.analytics_handlers import handle_compute_greeks

        positions = [
            {"value": 100_000, "volatility": 0.20},
            {"value": 50_000, "volatility": 0.30},
        ]
        cholesky = [[1.0, 0.0], [0.5, 0.866]]
        result = handle_compute_greeks(
            {
                "positions": positions,
                "cholesky": cholesky,
            }
        )
        greeks = result["greeks"]
        assert len(greeks["delta"]) == 2
        assert len(greeks["vega"]) == 2
        assert greeks["portfolio_delta"] > 0


# ---------------------------------------------------------------------------
# TestReporting
# ---------------------------------------------------------------------------


class TestReporting:
    """Tests for reporting handlers."""

    def test_generate_report_structure(self):
        """GenerateReport returns report with summary and timestamp."""
        from handlers.reporting.report_handlers import handle_generate_report

        result = handle_generate_report(
            {
                "portfolio": {"name": "test", "total_value": 100000, "positions": [{"a": 1}]},
                "metrics": {"var_95": -5000, "var_99": -8000},
                "greeks": {"portfolio_delta": 1.0, "portfolio_vega": 500.0},
                "stress_results": "market_crash",
            }
        )
        report = result["report"]
        assert "report_path" in report
        assert "summary" in report
        assert "timestamp" in report
        assert report["summary"]["portfolio_name"] == "test"

    def test_generate_report_timestamp_present(self):
        """Report timestamp is a non-empty ISO string."""
        from handlers.reporting.report_handlers import handle_generate_report

        result = handle_generate_report(
            {
                "portfolio": {"name": "p1", "total_value": 0, "positions": []},
                "metrics": {},
                "greeks": {},
                "stress_results": {},
            }
        )
        ts = result["report"]["timestamp"]
        assert len(ts) > 10  # ISO format is at least 20+ chars


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    """Tests for handler dispatch routing."""

    def test_market_data_dispatch(self):
        """Market data dispatch table has 2 entries."""
        from handlers.market_data.market_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "risk.MarketData.LoadPortfolio" in _DISPATCH
        assert "risk.MarketData.FetchHistoricalData" in _DISPATCH

    def test_simulation_dispatch(self):
        """Simulation dispatch table has 2 entries."""
        from handlers.simulation.simulation_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "risk.Simulation.SimulateBatch" in _DISPATCH
        assert "risk.Simulation.SimulateStress" in _DISPATCH

    def test_analytics_dispatch(self):
        """Analytics dispatch table has 2 entries."""
        from handlers.analytics.analytics_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "risk.Analytics.ComputeVaR" in _DISPATCH
        assert "risk.Analytics.ComputeGreeks" in _DISPATCH

    def test_reporting_dispatch(self):
        """Reporting dispatch table has 1 entry."""
        from handlers.reporting.report_handlers import _DISPATCH

        assert len(_DISPATCH) == 1
        assert "risk.Reporting.GenerateReport" in _DISPATCH

    def test_total_handler_count(self):
        """Total handlers across all namespaces is 7."""
        from handlers.analytics.analytics_handlers import _DISPATCH as ana
        from handlers.market_data.market_handlers import _DISPATCH as md
        from handlers.reporting.report_handlers import _DISPATCH as rpt
        from handlers.simulation.simulation_handlers import _DISPATCH as sim

        total = len(md) + len(sim) + len(ana) + len(rpt)
        assert total == 7


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------


class TestCompilation:
    """Tests for FFL compilation of the risk example."""

    @pytest.fixture()
    def parsed_ast(self):
        """Parse the risk.afl file and return the AST."""
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "risk.ffl")
        with open(afl_path) as f:
            source = f.read()
        return FFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        """AFL source parses without errors."""
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        """risk.afl defines 8 schemas in risk.types."""
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 8

    def test_event_facet_count(self, parsed_ast):
        """risk.afl defines 7 event facets across 4 namespaces."""
        facets = []
        for ns in parsed_ast.namespaces:
            facets.extend(ns.event_facets)
        assert len(facets) == 7

    def test_workflow_count(self, parsed_ast):
        """risk.afl defines 2 workflows."""
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 2
        names = {w.sig.name for w in workflows}
        assert names == {"AnalyzePortfolio", "StressTestPortfolio"}
