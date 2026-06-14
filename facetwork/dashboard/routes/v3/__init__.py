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

"""Dashboard v3 — redesigned UI preview (standalone, opt-in).

The v3 routes render a redesigned, dark-first workflow detail view against
the *same* live data the v2 pages use. They are deliberately self-contained
(no ``base.html`` inheritance, no shared ``style.css``) so this preview can
evolve without touching the production v1/v2 chrome.
"""
