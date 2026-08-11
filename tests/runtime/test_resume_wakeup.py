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

"""No resume request may be lost.

``_resume_with_lock`` deliberately never blocks a worker on a contended
per-workflow lock: a thread that cannot acquire it flags the workflow pending
and returns, and the holder re-runs for the flag. The hand-off has to be
airtight, because the flag is the *only* record that a resume was wanted.

It was not. The holder checked "is anything pending?" and released the lock as
two separate steps, so a thread whose acquire failed in between added a flag
nobody would ever look at again. Under a wide fan-out hundreds of leaf
completions race here per second, so the lost interleaving is routine rather
than exotic — and when the lost wakeup belongs to the LAST leaf, the block steps
above it are never cascaded and the workflow waits for the stuck-step sweep to
notice minutes later.

The observable invariant is simple: **once every caller has returned and no
holder is running, nothing may still be flagged pending.** A leftover flag is a
resume that was requested and dropped.
"""

from __future__ import annotations

import threading

from facetwork.runtime.agent import ToolRegistry
from facetwork.runtime.evaluator import Evaluator
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.runner import RunnerConfig, RunnerService

WF = "wf-1"


def _runner() -> RunnerService:
    store = MemoryStore()
    return RunnerService(store, Evaluator(persistence=store), RunnerConfig(), ToolRegistry())


def test_the_lock_is_released_while_the_pending_lock_is_held():
    """The invariant the fix rests on, asserted directly.

    A caller decides "acquire or flag" under ``_resume_pending_lock``. For that
    decision to be sound the holder must give up the workflow lock under the
    *same* lock — otherwise a caller can observe "still held" (so it flags) after
    the holder has already looked for flags (so it never returns), and the two
    correct-looking halves lose a resume between them.

    Asserting it here rather than through a thread race is deliberate: the losing
    interleaving is a handful of instructions wide, so a timing-based test would
    pass on the broken code most of the time. This cannot.
    """
    svc = _runner()
    real = threading.Lock()
    observed: list[bool] = []

    class WatchedLock:
        def acquire(self, blocking=True):
            return real.acquire(blocking)

        def release(self):
            observed.append(svc._resume_pending_lock.locked())
            real.release()

    svc._resume_locks[WF] = WatchedLock()
    svc._resume_with_lock(WF, lambda: None)

    assert observed, "the workflow lock was never released"
    assert all(observed), (
        "the workflow lock was released without holding _resume_pending_lock — a "
        "caller can flag a resume in that window and nobody will ever run it"
    )


def test_every_deferred_step_runs_not_just_the_lock_holders():
    """The bug that stalled wide fan-outs: siblings deferred, then dropped.

    A resume is per STEP. When many leaves of one workflow finish at once, one
    thread wins the lock and the rest defer. Recording only the workflow made
    the holder re-run *its own* closure — so on a 200-wide fan-out 199 step
    resumes were discarded, the block steps above them were never cascaded, and
    the run sat untouched until the stuck-step sweep found it minutes later.

    Every distinct step must run.
    """
    svc = _runner()
    ran: set[str] = set()
    lock = threading.Lock()
    holder_inside = threading.Event()
    siblings_deferred = threading.Event()

    def resume(step_id: str) -> None:
        with lock:
            ran.add(step_id)
        if step_id == "step-holder":
            holder_inside.set()
            siblings_deferred.wait(timeout=5)

    siblings = [f"step-{i}" for i in range(20)]

    def defer_all():
        holder_inside.wait(timeout=5)
        for s in siblings:  # every one of these fails to acquire and defers
            svc._resume_with_lock(WF, lambda s=s: resume(s), key=s)
        siblings_deferred.set()

    t = threading.Thread(target=defer_all)
    t.start()
    svc._resume_with_lock(WF, lambda: resume("step-holder"), key="step-holder")
    t.join(timeout=10)

    missing = set(siblings) - ran
    assert not missing, f"{len(missing)} deferred step resume(s) were dropped: {sorted(missing)}"
    assert WF not in svc._resume_pending


def test_the_same_step_deferred_twice_runs_once():
    """Coalescing is the point of deferral — it must still coalesce.

    Distinct steps must not merge; repeat requests for the SAME step should,
    or a busy workflow re-resumes the same step once per completion.
    """
    svc = _runner()
    count: list[int] = []
    holder_inside = threading.Event()
    deferred = threading.Event()

    def holder():
        holder_inside.set()
        deferred.wait(timeout=5)

    def dupe():
        holder_inside.wait(timeout=5)
        for _ in range(5):
            svc._resume_with_lock(WF, lambda: count.append(1), key="step-a")
        deferred.set()

    t = threading.Thread(target=dupe)
    t.start()
    svc._resume_with_lock(WF, holder, key="step-holder")
    t.join(timeout=10)

    assert len(count) == 1, f"same step ran {len(count)} times; deferral should coalesce it"


def test_one_failing_resume_does_not_discard_the_batch():
    """A step that cannot resume must not take its siblings down with it."""
    svc = _runner()
    ran: list[str] = []
    holder_inside = threading.Event()
    deferred = threading.Event()

    def holder():
        holder_inside.set()
        deferred.wait(timeout=5)

    def boom():
        raise RuntimeError("this step cannot resume")

    def enqueue():
        holder_inside.wait(timeout=5)
        svc._resume_with_lock(WF, boom, key="bad")
        svc._resume_with_lock(WF, lambda: ran.append("good"), key="good")
        deferred.set()

    t = threading.Thread(target=enqueue)
    t.start()
    svc._resume_with_lock(WF, holder, key="holder")
    t.join(timeout=10)

    assert ran == ["good"], "a failing sibling discarded the rest of the batch"


def test_flag_added_while_the_holder_runs_is_honoured():
    """The ordinary case: a second caller arrives mid-resume and is picked up."""
    svc = _runner()
    calls: list[int] = []
    holder_inside = threading.Event()
    flag_added = threading.Event()

    def resume_fn():
        calls.append(1)
        if len(calls) == 1:
            holder_inside.set()
            flag_added.wait(timeout=5)  # let the other caller race us

    def other():
        holder_inside.wait(timeout=5)
        svc._resume_with_lock(WF, lambda: calls.append(1), key="other")
        flag_added.set()

    t = threading.Thread(target=other)
    t.start()
    svc._resume_with_lock(WF, resume_fn, key="holder")
    t.join(timeout=10)

    assert len(calls) >= 2, "the flagged resume was never run — lost wakeup"
    assert WF not in svc._resume_pending, "a pending flag was left behind"


def test_no_flag_survives_a_concurrent_storm():
    """The fan-out case, in miniature: many threads, one workflow.

    Nothing may remain flagged once everyone has returned; a leftover flag is a
    resume that was asked for and silently dropped.
    """
    svc = _runner()
    calls: list[int] = []
    lock = threading.Lock()

    def resume_fn():
        with lock:
            calls.append(1)

    threads = [threading.Thread(target=lambda: svc._resume_with_lock(WF, resume_fn))
               for _ in range(64)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert calls, "no resume ran at all"
    assert WF not in svc._resume_pending, (
        "a pending flag outlived every caller — that resume will never run, and "
        "the workflow waits for the stuck-step sweep"
    )


def test_lock_is_released_even_if_resume_raises():
    """A failing resume must not wedge the workflow forever."""
    svc = _runner()

    def boom():
        raise RuntimeError("resume failed")

    try:
        svc._resume_with_lock(WF, boom)
    except RuntimeError:
        pass

    ran: list[int] = []
    svc._resume_with_lock(WF, lambda: ran.append(1))
    assert ran == [1], "the per-workflow lock was not released after a failure"


def test_caller_never_blocks_on_a_contended_workflow():
    """The property the non-blocking design exists for, kept intact.

    A worker thread must never park waiting for another thread's resume — that
    is the convoy this design replaced.
    """
    svc = _runner()
    holder_inside = threading.Event()
    release = threading.Event()

    def slow_resume():
        holder_inside.set()
        release.wait(timeout=5)

    t = threading.Thread(target=lambda: svc._resume_with_lock(WF, slow_resume))
    t.start()
    holder_inside.wait(timeout=5)

    returned = threading.Event()

    def contender():
        svc._resume_with_lock(WF, lambda: None)
        returned.set()

    c = threading.Thread(target=contender)
    c.start()
    assert returned.wait(timeout=2), "a contended caller blocked instead of flagging"

    release.set()
    t.join(timeout=10)
    c.join(timeout=10)
    assert WF not in svc._resume_pending
