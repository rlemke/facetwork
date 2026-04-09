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

"""Centralized logging configuration for Facetwork services.

Provides a Splunk-compatible JSON formatter and a shared ``configure_logging``
helper that replaces the duplicated ``logging.basicConfig`` pattern across CLI
entry points.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime

_TEXT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


class SplunkJsonFormatter(logging.Formatter):
    """Emit one compact JSON object per log line for Splunk ingestion.

    Output fields follow the Splunk Common Information Model (CIM):
    - ``timestamp`` — ISO 8601 with milliseconds and ``Z`` suffix (UTC)
    - ``level`` — uppercase level name
    - ``logger`` — logger name
    - ``message`` — formatted log message
    - ``source`` — constant ``"facetwork"``
    - ``exc_info`` — traceback string (only when present)
    """

    def format(self, record: logging.LogRecord) -> str:
        # Build UTC ISO 8601 timestamp with ms precision
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}Z"

        obj: dict[str, str] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "source": "facetwork",
        }

        if record.exc_info and record.exc_info[0] is not None:
            obj["exc_info"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    level: str = "WARNING",
    log_file: str | None = None,
    log_format: str = "json",
    service_name: str | None = None,
) -> None:
    """Set up root logging with the chosen format.

    Args:
        level: Uppercase level name (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to log file.  ``None`` → stderr.
        log_format: ``"json"`` for Splunk-compatible JSON, ``"text"`` for the
            legacy plain-text format.
        service_name: Reserved for future per-service tagging.
    """
    handler: logging.Handler
    if log_file:
        handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler()

    if log_format == "json":
        handler.setFormatter(SplunkJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))

    # basicConfig is a no-op when the root logger already has handlers, so
    # force-add ours and set the level explicitly.
    logging.basicConfig(
        level=getattr(logging, level),
        handlers=[handler],
        force=True,
    )
