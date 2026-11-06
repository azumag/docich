"""Bounded host telemetry for the direct-stream status file (#3553, ADR 0007 C2).

Ported verbatim from soviet_now ``lib/direct_stream.py`` at 0971b36
(``SystemMetricsSampler``). Re-diff against the then-current soviet_now before
the production cutover (ADR 0007 stage E).

Read-only: parses ``/proc/stat``, ``/proc/meminfo`` and ``statvfs("/")`` only.
No process, network, lock or file write.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

STREAM_METRICS_CONTRACT_VERSION = 1
SYSTEM_METRICS_INTERVAL_SEC = 5.0
SYSTEM_METRICS_HISTORY_LIMIT = 36


class SystemMetricsSampler:
    """Read bounded host CPU/RAM/root-disk telemetry in the FFmpeg progress loop."""

    def __init__(self, interval_sec: float = SYSTEM_METRICS_INTERVAL_SEC) -> None:
        self.interval_sec = interval_sec
        self.last_sample_monotonic: float | None = None
        self.previous_cpu: tuple[int, int] | None = None
        self.history: list[dict[str, int | float | None]] = []
        self.latest: dict[str, object] = {
            "sampled_at": None,
            "cpu_percent": None,
            "memory_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "disk_percent": None,
            "disk_used_bytes": None,
            "disk_total_bytes": None,
            "disk_free_bytes": None,
            "history": self.history,
        }

    @staticmethod
    def _read_cpu(path: Path) -> tuple[int, int] | None:
        try:
            fields = path.read_text(encoding="ascii").splitlines()[0].split()
            if fields[0] != "cpu" or len(fields) < 5:
                return None
            ticks = [int(field) for field in fields[1:]]
            if any(value < 0 for value in ticks):
                return None
            # Linux repeats guest/guest_nice inside user/nice. Ignore those
            # trailing counters to keep the denominator from double-counting.
            total = sum(ticks[:8])
            idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
            return (total - idle, total)
        except (OSError, IndexError, ValueError, UnicodeError):
            return None

    @staticmethod
    def _read_memory(path: Path) -> tuple[int, int] | None:
        try:
            values: dict[str, int] = {}
            for line in path.read_text(encoding="ascii").splitlines():
                key, sep, rest = line.partition(":")
                if sep and key in {"MemTotal", "MemAvailable"}:
                    fields = rest.split()
                    if len(fields) < 2 or fields[1] != "kB":
                        return None
                    values[key] = int(fields[0]) * 1024
            total = values["MemTotal"]
            available = values["MemAvailable"]
            if total <= 0 or not 0 <= available <= total:
                return None
            return total - available, total
        except (OSError, KeyError, ValueError, UnicodeError):
            return None

    @staticmethod
    def _read_disk(path: str = "/") -> tuple[int, int, int, float] | None:
        """Read root filesystem usage the way df reports it.

        Reserved blocks count as used but are not available to the runtime
        user, so the percentage uses used + user-available as its denominator.
        """
        try:
            info = os.statvfs(path)
        except (OSError, ValueError):
            return None
        block = int(info.f_frsize or info.f_bsize or 0)
        total = block * int(info.f_blocks)
        free = block * int(info.f_bfree)
        available = block * int(info.f_bavail)
        if block <= 0 or total <= 0:
            return None
        used = max(0, total - free)
        available = max(0, available)
        denominator = used + available
        if denominator <= 0:
            return None
        percent = min(100.0, round(100.0 * used / denominator, 1))
        return used, total, available, percent

    def sample(
        self,
        *,
        now_epoch: int,
        now_monotonic: float,
        proc_root: Path = Path("/proc"),
        disk_root: str = "/",
        platform: str = sys.platform,
    ) -> dict[str, object]:
        if self.last_sample_monotonic is not None and now_monotonic - self.last_sample_monotonic < self.interval_sec:
            return self.latest
        self.last_sample_monotonic = now_monotonic

        cpu_percent: float | None = None
        memory_percent: float | None = None
        memory_used: int | None = None
        memory_total: int | None = None
        disk_used: int | None = None
        disk_total: int | None = None
        disk_free: int | None = None
        disk_percent: float | None = None
        if platform.startswith("linux"):
            current = self._read_cpu(proc_root / "stat")
            if current is not None and self.previous_cpu is not None:
                busy_delta = current[0] - self.previous_cpu[0]
                total_delta = current[1] - self.previous_cpu[1]
                if busy_delta >= 0 and total_delta > 0 and busy_delta <= total_delta:
                    cpu_percent = round(100.0 * busy_delta / total_delta, 1)
            self.previous_cpu = current

            memory = self._read_memory(proc_root / "meminfo")
            if memory is not None:
                memory_used, memory_total = memory
                memory_percent = round(100.0 * memory_used / memory_total, 1)

            disk = self._read_disk(disk_root)
            if disk is not None:
                disk_used, disk_total, disk_free, disk_percent = disk

        point: dict[str, int | float | None] = {
            "ts": now_epoch,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "disk_percent": disk_percent,
        }
        self.history.append(point)
        del self.history[:-SYSTEM_METRICS_HISTORY_LIMIT]
        self.latest = {
            "sampled_at": now_epoch,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "memory_used_bytes": memory_used,
            "memory_total_bytes": memory_total,
            "disk_percent": disk_percent,
            "disk_used_bytes": disk_used,
            "disk_total_bytes": disk_total,
            "disk_free_bytes": disk_free,
            "history": self.history,
        }
        return self.latest
