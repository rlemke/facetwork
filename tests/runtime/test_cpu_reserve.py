"""A host can keep CPUs for itself (fleet_config cpu_reserve -> FW_CPU_RESERVE).

Measured 2026-11-19: the fastest host is also a desktop with all 16 cores in
its Docker VM; once fast-host placement kept its five 3-CPU slots full, the
load average sat near 21 and the desktop's own tools stalled for minutes.
"""

from __future__ import annotations

from facetwork.runtime.base_runner import measured_cpus


def test_no_reserve_and_no_limit_offers_every_core():
    assert measured_cpus(16, None, None) == 16.0
    assert measured_cpus(16, "max 100000", "") == 16.0


def test_a_reserve_is_held_back():
    assert measured_cpus(16, None, "4") == 12.0
    # 12 CPUs is four 3-CPU chunks instead of five
    assert int(measured_cpus(16, None, "4") // 3) == 4


def test_a_container_cpu_limit_caps_the_offer():
    assert measured_cpus(16, "800000 100000", None) == 8.0
    assert measured_cpus(16, "800000 100000", "2") == 6.0


def test_a_reserve_never_takes_a_host_below_one_cpu():
    assert measured_cpus(2, None, "4") == 1.0


def test_bad_or_missing_inputs_do_not_break_measurement():
    assert measured_cpus(16, None, "lots") == 16.0
    assert measured_cpus(16, "garbage", None) == 16.0
    assert measured_cpus(None, None, "2") is None
    assert measured_cpus(0, None, None) is None
