"""Tests for the run-input summary shown in the runs list.

Two runs of the same workflow are otherwise indistinguishable in the list; this
summary is what tells them apart, so it has to stay readable and never raise on
the shapes real runs actually carry.
"""

from __future__ import annotations

from facetwork.dashboard.helpers import run_summary
from facetwork.runtime.entities import Parameter


class FakeRunner:
    def __init__(self, parameters=None):
        self.parameters = parameters


def _runner(**inputs):
    return FakeRunner([Parameter(name=k, value=v) for k, v in inputs.items()])


def test_no_parameters_yields_empty_string():
    assert run_summary(FakeRunner([])) == ""
    assert run_summary(FakeRunner(None)) == ""


def test_runner_without_the_attribute_does_not_raise():
    assert run_summary(object()) == ""


def test_name_is_shown_bare_without_its_key():
    assert run_summary(_runner(name="S&P 500 momentum")) == "S&P 500 momentum"


def test_other_keys_are_shown_as_key_equals_value():
    assert run_summary(_runner(universe="sp500")) == "universe=sp500"


def test_name_is_ordered_first():
    summary = run_summary(_runner(universe="sp500", size=10, name="Week 32"))
    assert summary.startswith("Week 32")


def test_pairs_are_capped():
    summary = run_summary(_runner(a=1, b=2, c=3, d=4, e=5), max_pairs=2)
    assert summary.count("·") == 1


def test_empty_and_false_values_are_dropped():
    summary = run_summary(_runner(name="X", pinned="", use_llm=False, group_id=None))
    assert summary == "X"


def test_zero_is_kept_because_it_is_a_real_value():
    assert "size=0" in run_summary(_runner(size=0))


def test_config_style_inputs_are_skipped():
    """Values identical across every run separate nothing."""
    summary = run_summary(_runner(name="X", force_refresh=True, lookback_days=400, dry_run=True))
    assert summary == "X"


def test_dict_and_list_values_are_skipped():
    summary = run_summary(_runner(name="X", weights={"a": 1}, tickers=["A", "B"]))
    assert summary == "X"


def test_long_values_are_truncated():
    summary = run_summary(_runner(name="y" * 200))
    assert len(summary) <= 72
    assert summary.endswith("...")


def test_overall_length_is_bounded():
    summary = run_summary(_runner(alpha="a" * 30, beta="b" * 30, gamma="c" * 30))
    assert len(summary) <= 72


def test_two_runs_of_one_workflow_are_distinguishable():
    a = run_summary(_runner(name="S&P 500 momentum", universe="sp500", size=10))
    b = run_summary(_runner(name="Dow 30 test", universe="dow30", size=5))
    assert a != b
    assert "sp500" in a and "dow30" in b


def test_group_id_surfaces_for_snapshot_style_runs():
    """Snapshot/close runs carry only a group_id — it must still show."""
    summary = run_summary(_runner(group_id="s-p-500-momentum-2026-08-07-f2debcb7"))
    assert summary.startswith("group_id=s-p-500")
