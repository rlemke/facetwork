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

"""Event view-data helpers.

Events are backed by the tasks collection (tasks represent event-facet work
dispatched to agents). The events UI lives in the v3 routes; this module now
only provides the shared data helper they import.
"""

from __future__ import annotations


def _count_events_by_state(store) -> dict[str, int]:
    """Count events per state for subnav tabs."""
    all_tasks = store.get_all_tasks()
    counts: dict[str, int] = {
        "all": 0,
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
    }
    for t in all_tasks:
        counts["all"] += 1
        s = t.state if isinstance(t.state, str) else t.state.value
        if s in counts:
            counts[s] += 1
    return counts


