"""Tests for the --continuation runner mode and the runtime-version gate.

Covers steps 1 & 2 of the ffl-runner orchestration-tier rollout
(docs/architecture/ffl-runner-orchestration-tier.md). Both are designed to be
no-ops by default (mode "inline"; every step stamped STEP_RUNTIME_VERSION).
"""

import pytest

from facetwork.runtime.runner_config import BaseRunnerConfig
from facetwork.runtime.types import (
    STEP_RUNTIME_VERSION,
    VersionInfo,
    is_runtime_compatible,
)


# --- Step 1: continuation mode -------------------------------------------------

def test_default_mode_is_inline_and_matches_today():
    c = BaseRunnerConfig()
    assert c.continuation_mode == "inline"
    assert c.polls_shared_continuations() is True
    assert c.runs_stuck_step_sweep() is True


def test_off_mode_is_handler_only():
    c = BaseRunnerConfig(continuation_mode="off")
    assert c.polls_shared_continuations() is False
    assert c.runs_stuck_step_sweep() is False


def test_shared_mode_owns_backlog_and_sweep():
    c = BaseRunnerConfig(continuation_mode="shared")
    assert c.polls_shared_continuations() is True
    assert c.runs_stuck_step_sweep() is True


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        BaseRunnerConfig(continuation_mode="bogus")


def test_mode_from_env(monkeypatch):
    monkeypatch.setenv("AFL_CONTINUATION_MODE", "off")
    assert BaseRunnerConfig().continuation_mode == "off"


def test_explicit_mode_beats_env(monkeypatch):
    monkeypatch.setenv("AFL_CONTINUATION_MODE", "off")
    assert BaseRunnerConfig(continuation_mode="shared").continuation_mode == "shared"


def test_blank_env_defaults_to_inline(monkeypatch):
    monkeypatch.setenv("AFL_CONTINUATION_MODE", "")
    assert BaseRunnerConfig().continuation_mode == "inline"


# --- Step 2: runtime-version gate ---------------------------------------------

def test_versioninfo_default_uses_constant():
    assert VersionInfo().runtime_version == STEP_RUNTIME_VERSION


def test_matching_version_is_compatible():
    assert is_runtime_compatible(STEP_RUNTIME_VERSION) is True


def test_empty_or_none_version_is_permissive():
    # Legacy steps without the field must never be stranded.
    assert is_runtime_compatible("") is True
    assert is_runtime_compatible(None) is True


def test_mismatched_version_is_incompatible():
    assert is_runtime_compatible("9.9.9") is False
    assert is_runtime_compatible(STEP_RUNTIME_VERSION + "-x") is False


def test_gate_is_noop_for_todays_steps():
    # A freshly-stamped step (default VersionInfo) is always processable —
    # proving the gate only activates once STEP_RUNTIME_VERSION is bumped.
    assert is_runtime_compatible(VersionInfo().runtime_version) is True
