"""
gui_stats.py - Real-Time Conversion Metrics, Moving-Average Throughput & Dynamic ETA Tracker.

Calculates live conversion performance, accurate estimated time of arrival (ETA),
and human-readable progress metrics.
"""

from __future__ import annotations

import collections
import time
from typing import Any, Dict, List, Optional

from .gui_queue import QueueItemState


def format_duration(seconds: float) -> str:
    """Format duration in seconds to MM:SS or HH:MM:SS."""
    if seconds < 0 or seconds != seconds:  # NaN check
        return "--:--"

    total_secs = int(seconds)
    hours = total_secs // 3600
    minutes = (total_secs % 3600) // 60
    secs = total_secs % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def format_throughput(songs_per_sec: float) -> str:
    """Format conversion throughput."""
    if songs_per_sec <= 0.0 or songs_per_sec != songs_per_sec:
        return "0.0 songs/s"
    if songs_per_sec >= 10.0:
        return f"{songs_per_sec:.1f} songs/s"
    else:
        return f"{songs_per_sec:.2f} songs/s"


class LiveStatsTracker:
    """
    Tracks live conversion progress and calculates throughput, elapsed time,
    and remaining ETA using a sliding window.
    """

    def __init__(self, window_size: int = 20) -> None:
        self.window_size = window_size
        self._total: int = 0
        self._started: int = 0
        self._done: int = 0
        self._skipped: int = 0
        self._error: int = 0
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._is_running: bool = False
        # Timestamps of finished items in recent window for moving average
        self._finish_timestamps: collections.deque[float] = collections.deque(
            maxlen=window_size
        )

    def start(self, total_items: int) -> None:
        """Initialize or reset tracking for a new batch conversion run."""
        self._total = max(0, total_items)
        self._started = 0
        self._done = 0
        self._skipped = 0
        self._error = 0
        self._start_time = time.perf_counter()
        self._end_time = None
        self._is_running = True
        self._finish_timestamps.clear()

    def record_item_started(self, item_id: str) -> None:
        """Record that an item began converting."""
        self._started += 1

    def record_item_finished(
        self,
        item_id: str,
        state: QueueItemState,
        elapsed_time: float = 0.0,
    ) -> None:
        """Record that an item completed, was skipped, or failed."""
        now = time.perf_counter()
        self._finish_timestamps.append(now)

        if state == QueueItemState.DONE:
            self._done += 1
        elif state == QueueItemState.SKIPPED:
            self._skipped += 1
        elif state == QueueItemState.ERROR:
            self._error += 1

    def finish_batch(self) -> None:
        """Record that the batch run completed."""
        self._end_time = time.perf_counter()
        self._is_running = False

    def reset(self) -> None:
        """Reset all tracking counters."""
        self._total = 0
        self._started = 0
        self._done = 0
        self._skipped = 0
        self._error = 0
        self._start_time = None
        self._end_time = None
        self._is_running = False
        self._finish_timestamps.clear()

    def get_metrics(self) -> Dict[str, Any]:
        """Compute and return a dictionary of real-time metrics."""
        now = time.perf_counter() if self._is_running else (self._end_time or time.perf_counter())
        processed = self._done + self._skipped + self._error
        remaining = max(0, self._total - processed)

        # Elapsed time
        if self._start_time is not None:
            elapsed_sec = max(0.0, now - self._start_time)
        else:
            elapsed_sec = 0.0

        # Percentage
        if self._total > 0:
            fraction = min(1.0, max(0.0, processed / self._total))
            percent = fraction * 100.0
        else:
            fraction = 0.0
            percent = 0.0

        # Throughput (songs/sec)
        throughput = 0.0
        if len(self._finish_timestamps) >= 2:
            # Moving average over window
            dt = self._finish_timestamps[-1] - self._finish_timestamps[0]
            if dt > 0.01:
                throughput = (len(self._finish_timestamps) - 1) / dt
        elif elapsed_sec > 0.1 and processed > 0:
            throughput = processed / elapsed_sec

        # ETA calculation
        if remaining > 0 and throughput > 0.05:
            eta_sec = remaining / throughput
            eta_str = format_duration(eta_sec)
        elif remaining == 0:
            eta_sec = 0.0
            eta_str = "00:00"
        else:
            eta_sec = -1.0
            eta_str = "--:--"

        return {
            "total": self._total,
            "processed": processed,
            "remaining": remaining,
            "done": self._done,
            "skipped": self._skipped,
            "error": self._error,
            "percent": percent,
            "progress_fraction": fraction,
            "throughput": throughput,
            "throughput_str": format_throughput(throughput),
            "elapsed_sec": elapsed_sec,
            "elapsed_str": format_duration(elapsed_sec),
            "eta_sec": eta_sec,
            "eta_str": eta_str,
            "is_running": self._is_running,
        }
