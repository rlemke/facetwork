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

"""Entry point: python -m afl.dashboard"""

from __future__ import annotations

import argparse
import sys


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Facetwork Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--config", default=None, help="Path to FFL config file")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="FILE",
        help="Log to file instead of stderr",
    )
    parser.add_argument(
        "--log-format",
        default="json",
        choices=["json", "text"],
        help="Log format (default: json)",
    )
    args = parser.parse_args()

    # Configure logging
    from facetwork.logging import configure_logging

    configure_logging(
        level=args.log_level,
        log_file=args.log_file,
        log_format=args.log_format,
    )

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is required. Install with: pip install 'facetwork[dashboard]'",
            file=sys.stderr,
        )
        sys.exit(1)

    # Pass config via environment so the app factory can pick it up
    import os

    if args.config:
        os.environ["AFL_CONFIG"] = args.config

    uvicorn.run(
        "facetwork.dashboard.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
