"""The runner-registration contract check.

⚠️ Motivating failure (fwh_unimatch, found in a DEPLOYED image): the function was
`def register_handlers(register)` and called its argument directly. The runtime
passes the RUNNER, so every call raised "'RegistryRunner' object is not callable".

What makes it worth a check is how little it disturbed. The domain installed,
registered its entry point, imported cleanly, seeded its workflow, and the runner
still scoped itself to `unimatch.*`. ONLY the handlers were missing, so its tasks
could never be claimed — with no error anywhere except one line at container
start. `fw util ffl-audit` reported ALL CLEAN throughout.
"""
from facetwork.ffl_audit import check_register_handlers as check


def test_calling_the_parameter_directly_is_flagged():
    src = "def register_handlers(register):\n    register('f', fn, timeout_ms=0)\n"
    hits = check(src, "h.py")
    assert hits and "calls register(...) directly" in hits[0]
    assert "cannot be claimed" in hits[0], "must say what the consequence is"


def test_the_correct_shape_is_clean():
    src = ("def register_handlers(runner):\n"
           "    runner.register_handler(facet_name='f', module_uri='m',"
           " entrypoint='handle')\n")
    assert check(src, "h.py") == []


def test_a_noop_stub_is_not_reported_as_a_contract_error():
    """⚠️ Per-FILE this would be a false alarm on the fleet's biggest domain.
    fwh_osm ships `register_handlers(runner): pass` in boundary_handlers.py while
    registering its facets from other modules and running the heaviest workload
    in the fleet. A check that fires on that earns being ignored.
    """
    hits = check("def register_handlers(runner):\n    pass\n", "h.py")
    assert hits, "it is still noted..."
    assert all(h.startswith("register-handlers-noop") for h in hits), (
        "...but as a NOOP note the caller resolves per-repo, not a contract error")


def test_the_repo_level_rule_is_implemented():
    """A no-op matters only when NOTHING in the repo registers anything."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "facetwork/ffl_audit.py").read_text()
    assert "registers_somewhere" in src
    assert "NO module in this repo registers a handler" in src


def test_a_zero_argument_signature_is_flagged():
    hits = check("def register_handlers():\n    pass\n", "h.py")
    assert hits and "takes no argument" in hits[0]


def test_broken_syntax_is_not_this_check_s_business():
    """Other checks report unparseable files; this one must not double-report."""
    assert check("def register_handlers(  :\n", "h.py") == []
