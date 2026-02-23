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

"""Jinja2 template filters for the dashboard."""

from __future__ import annotations

import datetime

from jinja2 import Environment
from markupsafe import Markup


def timestamp_fmt(value: int | float | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a millisecond-epoch timestamp as a <time> element.

    Renders a ``<time>`` tag whose ``datetime`` attribute holds the ISO-8601
    UTC value and whose ``data-ts`` attribute holds the epoch-millisecond
    value.  A small script in ``base.html`` converts the visible text to the
    viewer's local timezone on page load.
    """
    if not value:
        return "—"
    dt = datetime.datetime.fromtimestamp(value / 1000, tz=datetime.UTC)
    iso = dt.isoformat()
    utc_text = dt.strftime(fmt)
    return Markup(
        f'<time datetime="{iso}" data-ts="{int(value)}">{utc_text}</time>'
    )


def duration_fmt(ms: int | float | None) -> str:
    """Format a duration in milliseconds as a compact string."""
    if not ms:
        return "—"
    seconds = int(ms) // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


_STATE_COLORS: dict[str, str] = {
    # Runner states
    "created": "primary",
    "running": "primary",
    "completed": "success",
    "failed": "danger",
    "paused": "warning",
    "cancelled": "secondary",
    # Server states
    "startup": "secondary",
    "shutdown": "secondary",
    "error": "danger",
    "down": "danger",
    # Task states
    "pending": "warning",
    "ignored": "secondary",
    "canceled": "secondary",
}


def state_color(state: str | None) -> str:
    """Return a CSS colour class for the given state string."""
    if not state:
        return "secondary"
    key = state.lower().rsplit(".", 1)[-1] if "." in (state or "") else (state or "").lower()
    return _STATE_COLORS.get(key, "secondary")


def state_label(state: str | None) -> str:
    """Extract a short label from a dotted state string."""
    if not state:
        return "unknown"
    return state.rsplit(".", 1)[-1]


def truncate_uuid(value: str | None, length: int = 8) -> str:
    """Truncate a UUID for display."""
    if not value:
        return "—"
    return value[:length]


def doc_description(doc: dict | str | None) -> str:
    """Render the description portion of a doc comment as HTML.

    Accepts both structured dict (new format) and plain string (legacy).
    """
    if doc is None:
        return ""
    if isinstance(doc, str):
        text = doc
    elif isinstance(doc, dict):
        text = doc.get("description", "")
    else:
        return ""
    if not text:
        return ""
    try:
        import markdown as md
        from markupsafe import Markup

        return Markup(md.markdown(text))
    except ImportError:
        # Fallback: escape and convert newlines to <br>
        from markupsafe import Markup, escape

        return Markup(str(escape(text)).replace("\n", "<br>"))


def doc_params(doc: dict | str | None) -> list[dict]:
    """Extract params list from a doc comment."""
    if isinstance(doc, dict):
        return doc.get("params", [])
    return []


def doc_returns(doc: dict | str | None) -> list[dict]:
    """Extract returns list from a doc comment."""
    if isinstance(doc, dict):
        return doc.get("returns", [])
    return []


_STEP_LOG_COLORS: dict[str, str] = {
    "info": "primary",
    "warning": "warning",
    "error": "danger",
    "success": "success",
}


def step_log_color(level: str | None) -> str:
    """Return a CSS colour class for a step log level."""
    if not level:
        return "secondary"
    return _STEP_LOG_COLORS.get(level.lower(), "secondary")


_STEP_STATE_BG: dict[str, str] = {
    "complete": "state-bg-complete",
    "error": "state-bg-error",
    "eventtransmit": "state-bg-transmit",
    "created": "state-bg-running",
    "continue": "state-bg-continue",
}


def step_state_bg(state: str | None) -> str:
    """Return a CSS background class for the given step state string."""
    if not state:
        return "state-bg-other"
    key = state.rsplit(".", 1)[-1].lower()
    return _STEP_STATE_BG.get(key, "state-bg-other")


def short_workflow_name_filter(name: str) -> str:
    """Extract the short name from a qualified workflow name."""
    from .helpers import short_workflow_name

    return short_workflow_name(name)


def step_category_filter(state: str) -> str:
    """Categorize a step state as running/complete/error."""
    from .helpers import categorize_step_state

    return categorize_step_state(state)


def namespace_of_filter(name: str) -> str:
    """Extract the namespace from a qualified workflow name."""
    from .helpers import extract_namespace

    return extract_namespace(name)


def register_filters(env: Environment) -> None:
    """Register all custom filters on a Jinja2 environment."""
    env.filters["timestamp"] = timestamp_fmt
    env.filters["duration"] = duration_fmt
    env.filters["state_color"] = state_color
    env.filters["state_label"] = state_label
    env.filters["truncate_uuid"] = truncate_uuid
    env.filters["doc_description"] = doc_description
    env.filters["doc_params"] = doc_params
    env.filters["doc_returns"] = doc_returns
    env.filters["step_log_color"] = step_log_color
    env.filters["step_state_bg"] = step_state_bg
    env.filters["short_workflow_name"] = short_workflow_name_filter
    env.filters["step_category"] = step_category_filter
    env.filters["namespace_of"] = namespace_of_filter
