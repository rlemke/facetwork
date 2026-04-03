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

"""Per-handler circuit breaker for cascading failure protection.

Each runner maintains an in-memory circuit breaker registry that tracks
handler health independently. When a handler fails repeatedly, its
circuit opens and the runner stops claiming tasks for that handler
until a cooldown period elapses.

States:
    CLOSED   — normal operation, all requests allowed
    OPEN     — handler is failing, requests blocked until cooldown
    HALF_OPEN — cooldown elapsed, one probe request allowed
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breakers."""

    failure_threshold: int = 5  # Consecutive failures to trip OPEN
    cooldown_ms: int = 60_000  # Time in OPEN before transitioning to HALF_OPEN
    success_threshold: int = 2  # Successes in HALF_OPEN to close the circuit

    @classmethod
    def from_env(cls) -> CircuitBreakerConfig:
        """Load configuration from environment variables."""
        return cls(
            failure_threshold=int(
                os.environ.get("AFL_CIRCUIT_BREAKER_THRESHOLD", "5")
            ),
            cooldown_ms=int(
                os.environ.get("AFL_CIRCUIT_BREAKER_COOLDOWN_MS", "60000")
            ),
            success_threshold=int(
                os.environ.get("AFL_CIRCUIT_BREAKER_SUCCESS_THRESHOLD", "2")
            ),
        )


@dataclass
class _HandlerCircuit:
    """Per-handler circuit state."""

    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    opened_at: int = 0  # Epoch ms when circuit opened
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreakerRegistry:
    """Per-runner registry of handler circuit breakers.

    Thread-safe for concurrent handler execution.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig.from_env()
        self._circuits: dict[str, _HandlerCircuit] = {}

    def _get_circuit(self, handler_name: str) -> _HandlerCircuit:
        if handler_name not in self._circuits:
            self._circuits[handler_name] = _HandlerCircuit()
        return self._circuits[handler_name]

    def is_allowed(self, handler_name: str) -> bool:
        """Check if a handler is allowed to process tasks.

        Returns True for CLOSED and HALF_OPEN states. Returns False
        for OPEN state unless the cooldown has elapsed (transitions to
        HALF_OPEN and returns True).
        """
        circuit = self._get_circuit(handler_name)

        if circuit.state == CircuitState.CLOSED:
            return True

        if circuit.state == CircuitState.HALF_OPEN:
            return True

        # OPEN — check if cooldown has elapsed
        now_ms = int(time.time() * 1000)
        if now_ms - circuit.opened_at >= self._config.cooldown_ms:
            circuit.state = CircuitState.HALF_OPEN
            circuit.consecutive_successes = 0
            logger.info(
                "Circuit breaker HALF_OPEN for '%s' — allowing probe request",
                handler_name,
            )
            return True

        return False

    def record_success(self, handler_name: str) -> None:
        """Record a successful handler execution."""
        circuit = self._get_circuit(handler_name)
        circuit.total_successes += 1
        circuit.consecutive_failures = 0

        if circuit.state == CircuitState.HALF_OPEN:
            circuit.consecutive_successes += 1
            if circuit.consecutive_successes >= self._config.success_threshold:
                circuit.state = CircuitState.CLOSED
                logger.info(
                    "Circuit breaker CLOSED for '%s' — handler recovered "
                    "(%d consecutive successes)",
                    handler_name,
                    circuit.consecutive_successes,
                )
                circuit.consecutive_successes = 0

    def record_failure(self, handler_name: str) -> None:
        """Record a failed handler execution."""
        circuit = self._get_circuit(handler_name)
        circuit.total_failures += 1
        circuit.consecutive_failures += 1
        circuit.consecutive_successes = 0

        if circuit.state == CircuitState.HALF_OPEN:
            # Probe failed — back to OPEN
            circuit.state = CircuitState.OPEN
            circuit.opened_at = int(time.time() * 1000)
            logger.warning(
                "Circuit breaker OPEN for '%s' — probe failed, "
                "cooldown %ds",
                handler_name,
                self._config.cooldown_ms // 1000,
            )

        elif circuit.state == CircuitState.CLOSED:
            if circuit.consecutive_failures >= self._config.failure_threshold:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = int(time.time() * 1000)
                logger.warning(
                    "Circuit breaker OPEN for '%s' — %d consecutive failures, "
                    "cooldown %ds",
                    handler_name,
                    circuit.consecutive_failures,
                    self._config.cooldown_ms // 1000,
                )

    def reset(self, handler_name: str) -> None:
        """Manually reset a circuit breaker to CLOSED."""
        if handler_name in self._circuits:
            circuit = self._circuits[handler_name]
            circuit.state = CircuitState.CLOSED
            circuit.consecutive_failures = 0
            circuit.consecutive_successes = 0
            logger.info("Circuit breaker manually reset for '%s'", handler_name)

    def get_state(self, handler_name: str) -> CircuitState:
        """Get the current state of a handler's circuit."""
        return self._get_circuit(handler_name).state

    def get_all_states(self) -> dict[str, dict]:
        """Get all circuit states for status reporting."""
        result: dict[str, dict] = {}
        for name, circuit in self._circuits.items():
            result[name] = {
                "state": circuit.state.value,
                "consecutive_failures": circuit.consecutive_failures,
                "consecutive_successes": circuit.consecutive_successes,
                "total_failures": circuit.total_failures,
                "total_successes": circuit.total_successes,
            }
        return result
