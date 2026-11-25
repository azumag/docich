"""Hard end-to-end deadlines and bounded retry backoff.

A socket read timeout alone is not a request deadline: an adapter that hangs
after connecting must not hold a bridge handler (or a Linux poll) forever.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
from typing import Callable, TypeVar

T = TypeVar("T")


class DeadlineExceeded(Exception):
    """The callable did not finish before its overall deadline."""


class Caller:
    """Deadline-bounded, fail-closed caller.

    A running future is assumed to be un-stoppable: once a worker is occupied
    by a hanging adapter it stays occupied for the life of the process.  When
    every worker is busy with such un-stoppable futures the pool is *full*,
    and a new call fails closed immediately (``DeadlineExceeded``) instead of
    being queued and blocking until its deadline.  This prevents indefinite
    waiting from reigniting.
    """

    def __init__(self, workers: int = 4, name: str = "mbridge-deadline") -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._workers = workers
        self._lock = threading.Lock()
        # Counts submitted futures (queued + running), not just workers that
        # have entered _run. Reserve before submit so concurrent callers cannot
        # both observe the same free slot and queue beyond the pool capacity.
        self._running = 0

    def call(self, fn: Callable[[], T], *, timeout_s: float) -> T:
        if timeout_s <= 0:
            raise DeadlineExceeded("non_positive_deadline")
        with self._lock:
            if self._running >= self._workers:
                raise DeadlineExceeded("adapter_timeout")
            self._running += 1
        try:
            future: Future = self._pool.submit(fn)
        except BaseException:
            with self._lock:
                self._running -= 1
            raise
        future.add_done_callback(self._release_slot)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            future.cancel()  # completion callback releases only when it is done/cancelled
            raise DeadlineExceeded("deadline_exceeded") from exc

    def _release_slot(self, _future: Future) -> None:
        with self._lock:
            self._running -= 1

    def close(self, *, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)


# Initial reconnect backoff (seconds).  Jitter is injected by the caller so the
# sequence stays deterministic in tests.
BACKOFF_SECONDS = (1, 2, 4, 8, 15, 30)


def backoff_delay(failures: int, *, rng=None) -> float:
    if failures <= 0:
        return 0.0
    base = BACKOFF_SECONDS[min(failures - 1, len(BACKOFF_SECONDS) - 1)]
    if rng is None:
        return float(base)
    return float(base) * (0.8 + 0.4 * float(rng.random()))
