"""Terminating a run must close its dead letters too.

A dead letter is terminal to the RUNTIME (nothing retries it) but open to the
OPERATOR: `fw maint dead-letters` reports it as forgotten, forever. Nothing could
close one, so an unrecoverable run left a permanent false alarm behind -- on a
check whose entire value is that it fires only on something worth acting on.

Measured 2026-09-08: one osm.planet.BuildAdminSet dead letter, 3.9 days old, on a
runner ALREADY in state `failed`, for an admin set the catalog declares
`expect: 0`. It could never succeed and nothing could ever close it.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO / "scripts/lib/maint/terminate-workflow").read_text()


def test_dead_letters_are_closed_when_a_run_is_terminated():
    assert 'OPERATOR_CLOSABLE_TASK_STATES = ["dead_letter"]' in SRC


def test_dead_letters_stay_a_separate_list_from_non_terminal_states():
    """Terminating running work and closing a dead letter are different acts.

    Folding "dead_letter" into NON_TERMINAL_TASK_STATES would also change what
    the runtime considers non-terminal elsewhere in this file.
    """
    assert 'NON_TERMINAL_TASK_STATES = ["pending", "running"]' in SRC


def test_the_original_failure_is_not_overwritten():
    """A generic 'terminated by operator' would erase why it died."""
    assert "was dead_letter" in SRC
    assert "the original failure is preserved in the step log" in SRC


def test_the_count_is_reported_separately():
    """'work we stopped' and 'work that had already given up' are different
    numbers; summing them hides that the run was already dead."""
    assert 'counts["dead_letters"]' in SRC
    assert "dead-letters→canceled=" in SRC
    assert 'total["dead_letters"]' in SRC


def test_a_zero_count_is_not_printed():
    """A permanent 'dead_letters=0' on every line trains the eye to skip it."""
    assert 'if c.get("dead_letters") else ""' in SRC
    assert 'if total["dead_letters"] else ""' in SRC
