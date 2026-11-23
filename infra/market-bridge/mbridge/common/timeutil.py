"""Hard end-to-end deadlines and bounded retry backoff.

A socket read timeout alone is not a request deadline: an adapter that hangs
after connecting must not hold a bridge handler (or a Linux poll) forever.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, TypeVar

T = TypeVar("T")


class DeadlineExceeded(Exception):
    """The callable did not finish before its overall deadline."""


class Caller:
    def __init__(self, workers: int = 4, name: str = "mbridge-deadline") -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)

    def call(self, fn: Callable[[], T], *, timeout_s: float) -> T:
        if timeout_s <= 0:
            raise DeadlineExceeded("non_positive_deadline")
        future: Future = self._pool.submit(fn)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            future.cancel()
            raise DeadlineExceeded("deadline_exceeded") from exc

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
