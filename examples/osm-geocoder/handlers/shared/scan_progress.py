"""Progress tracking for PBF file scanning.

Reports progress at 1/8 file-size milestones during pyosmium scans.
Since pyosmium's ``apply_file()`` doesn't expose byte position,
progress is estimated from element counts using a calibration window.
"""

import os
import time
from collections.abc import Callable


class ScanProgressTracker:
    """Tracks element processing and reports at 1/8 file-size milestones.

    Usage::

        tracker = ScanProgressTracker(file_size, step_log, label="CombinedScan")

        # In each pyosmium callback:
        tracker.tick("node")   # or "way", "area", "relation"

        # After apply_file() returns:
        tracker.finish()
    """

    NUM_CHECKPOINTS = 8
    CALIBRATION_WINDOW = 25_000  # elements before first estimate

    def __init__(
        self,
        file_size: int,
        step_log: Callable | None = None,
        label: str = "Scan",
    ):
        self._file_size = file_size
        self._step_log = step_log
        self._label = label

        self._elements = 0
        self._nodes = 0
        self._ways = 0
        self._areas = 0
        self._relations = 0

        self._next_checkpoint = 1  # 1..NUM_CHECKPOINTS
        self._estimated_total = 0
        self._t0 = time.monotonic()

        # Pre-compute milestone interval using PBF heuristic.
        # Compressed PBF averages ~40-80 bytes per element; use 60 as midpoint.
        # This gives roughly NUM_CHECKPOINTS reports for any file.
        self._milestone_interval = max(5_000, file_size // (self.NUM_CHECKPOINTS * 60))

    def tick(self, element_type: str = "node") -> None:
        """Call after processing each element."""
        self._elements += 1
        if element_type == "node":
            self._nodes += 1
        elif element_type == "way":
            self._ways += 1
        elif element_type == "area":
            self._areas += 1
        elif element_type == "relation":
            self._relations += 1

        # After calibration window, refine the estimate using actual density
        if self._elements == self.CALIBRATION_WINDOW:
            self._recalibrate()

        # Check if we hit the next milestone
        if self._estimated_total > 0:
            threshold = int(self._estimated_total * self._next_checkpoint / self.NUM_CHECKPOINTS)
            if self._elements >= threshold and self._next_checkpoint <= self.NUM_CHECKPOINTS:
                self._report()
                self._next_checkpoint += 1
        elif self._elements % self._milestone_interval == 0:
            # Before calibration, use fixed interval
            if self._next_checkpoint <= self.NUM_CHECKPOINTS:
                self._report()
                self._next_checkpoint += 1

    def _recalibrate(self) -> None:
        """Refine total element estimate from observed density."""
        if self._elements == 0:
            return
        # Estimate bytes per element from calibration window.
        # PBF is read sequentially, so assume uniform density.
        bytes_per_element = self._file_size / max(1, self._elements * 3)
        self._estimated_total = int(self._file_size / max(1, bytes_per_element))
        # Ensure estimate is at least double current count
        self._estimated_total = max(self._estimated_total, self._elements * 2)

    def _report(self) -> None:
        if not self._step_log:
            return
        elapsed = time.monotonic() - self._t0
        pct = min(100, int(self._next_checkpoint / self.NUM_CHECKPOINTS * 100))
        size_mb = self._file_size / (1024 * 1024)

        parts = []
        if self._nodes:
            parts.append(f"{self._nodes:,}N")
        if self._ways:
            parts.append(f"{self._ways:,}W")
        if self._areas:
            parts.append(f"{self._areas:,}A")
        if self._relations:
            parts.append(f"{self._relations:,}R")
        counts = "/".join(parts) if parts else f"{self._elements:,}"

        self._step_log(
            f"{self._label}: ~{pct}% of {size_mb:.1f}MB — "
            f"{self._elements:,} elements ({counts}) in {elapsed:.1f}s"
        )

    def finish(self) -> None:
        """Log final scan completion."""
        if not self._step_log:
            return
        elapsed = time.monotonic() - self._t0
        size_mb = self._file_size / (1024 * 1024)
        rate = self._elements / elapsed if elapsed > 0 else 0
        self._step_log(
            f"{self._label}: complete — {self._elements:,} elements "
            f"from {size_mb:.1f}MB in {elapsed:.1f}s ({rate:,.0f} elem/s)",
            level="success",
        )


def get_file_size(path: str) -> int:
    """Get file size in bytes, returning 0 if not found."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
