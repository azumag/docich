"""Small mkdir locks used by the native dispatch lanes."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


class LockTimeout(RuntimeError):
    """A generation/provider slot could not be acquired before its deadline."""


class FileLock:
    def __init__(
        self,
        path: Path,
        *,
        label: str,
        wait_sec: float = 2.0,
        stale_sec: int = 1800,
        max_wait_sec: int = 0,
    ) -> None:
        self.path = Path(path)
        self.label = label
        self.wait_sec = max(float(wait_sec), 0.05)
        self.stale_sec = max(int(stale_sec), 60)
        self.max_wait_sec = max(int(max_wait_sec), 0)
        self.token = f"{os.getpid()}:{time.time_ns()}"

    def acquire(self, *, deadline: float | None = None) -> "FileLock":
        started = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.path.mkdir()
            except FileExistsError:
                if self.path.is_file() or self.path.is_symlink():
                    raise LockTimeout(self.label)
                self._reap_stale()
                elapsed = time.monotonic() - started
                if (self.max_wait_sec and elapsed >= self.max_wait_sec) or (
                    deadline is not None and time.monotonic() >= deadline
                ):
                    raise LockTimeout(self.label)
                time.sleep(self.wait_sec)
                continue
            owner = self.path / "owner"
            owner.write_text(
                f"token={self.token}\npid={os.getpid()}\nlabel={self.label}\n",
                encoding="utf-8",
            )
            return self

    def _reap_stale(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        age = time.time() - stat.st_mtime
        owner_pid = ""
        try:
            for line in (self.path / "owner").read_text(encoding="utf-8").splitlines():
                if line.startswith("pid="):
                    owner_pid = line[4:]
                    break
        except OSError:
            pass
        owner_dead = False
        if owner_pid.isdigit():
            try:
                os.kill(int(owner_pid), 0)
            except ProcessLookupError:
                owner_dead = True
            except PermissionError:
                owner_dead = False
            except OSError:
                owner_dead = True
        if owner_dead or age > self.stale_sec:
            shutil.rmtree(self.path, ignore_errors=True)

    def release(self) -> None:
        try:
            owner = (self.path / "owner").read_text(encoding="utf-8")
        except OSError:
            return
        if f"token={self.token}\n" in owner:
            shutil.rmtree(self.path, ignore_errors=True)
