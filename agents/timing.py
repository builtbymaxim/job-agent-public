"""Lightweight run timing — per-stage wall-clock accounting for the pipeline.

Wrap a phase with `with timer.stage("discovery"): ...`; durations accumulate per label
(so the same label across many jobs sums up). `total_seconds` is wall-clock since the
timer was created. Used for the end-of-run "N applications in M min" banner and the
`run_log.json` trend log.
"""

from __future__ import annotations

import time
from contextlib import contextmanager


class StageTimer:
    def __init__(self):
        self._start = time.monotonic()
        self.stages: dict[str, float] = {}

    @contextmanager
    def stage(self, label: str):
        t0 = time.monotonic()
        try:
            yield
        finally:
            self.stages[label] = self.stages.get(label, 0.0) + (time.monotonic() - t0)

    @property
    def total_seconds(self) -> float:
        return time.monotonic() - self._start

    def breakdown(self) -> list[tuple[str, float]]:
        """Stages sorted slowest-first."""
        return sorted(self.stages.items(), key=lambda kv: kv[1], reverse=True)


def human_duration(seconds: float) -> str:
    """Compact human duration: '45s', '3m 12s', '1h 04m'."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
