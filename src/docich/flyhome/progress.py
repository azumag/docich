"""毎ステップの進捗をFIFOで上書きし、実機の入力をファイルI/Oで待たせない。"""
from __future__ import annotations

import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock


class StatePublisher:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flyhome-progress")
        self._pending = deque()
        self._lock = Lock()
        self._submitted = self._completed = self._written = 0
        self._last_s = self._max_s = self._queue_s = 0.0
        self._closed = False

    def publish(self, body: str) -> None:
        if self._closed:
            raise RuntimeError("progress publisher is closed")
        while self._pending and self._pending[0].done():
            self._pending.popleft().result()  # 非同期の書き込み失敗を次stepで通知する。
        with self._lock:
            self._submitted += 1
            sequence = self._submitted
        self._pending.append(self._executor.submit(self._write_timed, sequence, body, time.perf_counter()))

    def _write_timed(self, sequence: int, body: str, queued: float) -> None:
        start = time.perf_counter()
        succeeded = False
        try:
            self._write(body)
            succeeded = True
        finally:
            elapsed = time.perf_counter() - start
            with self._lock:
                self._completed += 1
                self._last_s, self._max_s = elapsed, max(self._max_s, elapsed)
                self._queue_s = start - queued
                if succeeded:
                    self._written = sequence

    def _write(self, body: str) -> None:
        temporary = self.path.with_name("." + self.path.name + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        # Windowsの一時的なreader lockは書き込み側だけで待つ。stepを間引かない。
        for retry in range(200):
            try:
                os.replace(temporary, self.path)
                return
            except PermissionError:
                if retry == 199:
                    raise
                time.sleep(0.005)

    def metrics(self) -> dict:
        with self._lock:
            return {"state_write_s": round(self._last_s, 6), "state_max_write_s": round(self._max_s, 6),
                    "state_queue_s": round(self._queue_s, 6), "state_pending": self._submitted - self._completed,
                    "state_written_sequence": self._written}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True)  # 最終stepも保存してからcampaignを終える。
        while self._pending:
            self._pending.popleft().result()
