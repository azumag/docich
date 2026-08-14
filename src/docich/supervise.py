"""Supervised restart loop used by `docich run <component>` (architecture.md SS2)."""
from __future__ import annotations

import datetime
import signal
import subprocess
import sys
import time
from typing import Callable

from . import procs
from .config import GlobalConfig
from .state import State

BACKOFF_START_S = 1
BACKOFF_MAX_S = 30
BACKOFF_RESET_AFTER_S = 60
TERMINATE_WAIT_S = 10

STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def log_line(fh, component: str, msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{component}] {msg}"
    print(line, file=fh, flush=True)
    print(line, flush=True)


def _next_backoff(current: int, alive_s: float) -> int:
    if alive_s >= BACKOFF_RESET_AFTER_S:
        return BACKOFF_START_S
    return current


def run_loop(
    component: str,
    g: GlobalConfig,
    build: Callable[[], "tuple[list[str], dict]"],
    *,
    pre: Callable[[], None] | None = None,
    post_start: Callable[[subprocess.Popen], None] | None = None,
) -> None:
    """Run `build()` -> spawn -> wait forever, restarting with backoff on exit/error."""
    state = State(g)
    state.ensure()
    log_path = state.logs_dir / f"{component}.log"

    with log_path.open("a", encoding="utf-8") as fh:
        current_proc: list[subprocess.Popen | None] = [None]

        def handle_signal(signum, _frame):
            log_line(fh, component, f"シグナル {signum} を受信、停止します")
            p = current_proc[0]
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=TERMINATE_WAIT_S)
                except subprocess.TimeoutExpired:
                    log_line(fh, component, "子プロセスが終了しないため kill します")
                    p.kill()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            sys.exit(0)

        for sig in STOP_SIGNALS:
            signal.signal(sig, handle_signal)

        backoff = BACKOFF_START_S
        while True:
            alive_s = 0.0
            p = None
            try:
                if pre is not None:
                    pre()
                cmd, env_extra = build()
                log_line(fh, component, f"起動: {' '.join(cmd)}")
                started_at = time.monotonic()
                p = procs.spawn(cmd, env_extra=env_extra, strip_tmux=True, log_fh=fh)
                current_proc[0] = p
                if post_start is not None:
                    post_start(p)
                rc = p.wait()
                alive_s = time.monotonic() - started_at
                log_line(fh, component, f"終了しました (code={rc}, 稼働時間={alive_s:.1f}s)")
            except SystemExit:
                raise
            except Exception as exc:
                log_line(fh, component, f"エラー: {exc}")
                if p is not None and p.poll() is None:
                    p.terminate()
            finally:
                current_proc[0] = None

            backoff = _next_backoff(backoff, alive_s)
            log_line(fh, component, f"{backoff}s 後に再起動します")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)


def run_callable_loop(component: str, g: GlobalConfig, fn: Callable[[], None]) -> None:
    """Run a plain python callable forever, restarting with backoff on exit/error."""
    state = State(g)
    state.ensure()
    log_path = state.logs_dir / f"{component}.log"

    with log_path.open("a", encoding="utf-8") as fh:

        def handle_signal(signum, _frame):
            log_line(fh, component, f"シグナル {signum} を受信、停止します")
            sys.exit(0)

        for sig in STOP_SIGNALS:
            signal.signal(sig, handle_signal)

        backoff = BACKOFF_START_S
        while True:
            started_at = time.monotonic()
            alive_s = 0.0
            try:
                fn()
                alive_s = time.monotonic() - started_at
                log_line(fh, component, f"正常終了しました (稼働時間={alive_s:.1f}s)")
            except SystemExit:
                raise
            except Exception as exc:
                alive_s = time.monotonic() - started_at
                log_line(fh, component, f"エラー: {exc}")

            backoff = _next_backoff(backoff, alive_s)
            log_line(fh, component, f"{backoff}s 後に再実行します")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)
