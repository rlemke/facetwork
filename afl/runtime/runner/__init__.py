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

"""AFL distributed runner service.

Polls MongoDB for blocked steps (EVENT_TRANSMIT) and pending tasks,
atomically claims tasks via claim_task(), dispatches events to registered
ToolRegistry handlers, and calls Evaluator.continue_step() with results.
"""

from .service import RunnerConfig, RunnerService

__all__ = ["RunnerService", "RunnerConfig"]
