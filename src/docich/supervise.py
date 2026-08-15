"""Supervised restart loop used by `docich run <component>` (architecture.md SS2)."""
from __future__ import annotations

import datetime
import os
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
    """Best-effort logging: never raises.

    Both writes are individually guarded so a broken destination (e.g. stdout
    after tmux kills the pane's pty -> OSError [Errno 5] Input/output error)
    does not stop the other write, and never propagates to the caller. A
    signal handler calling this while cleaning up a child process must not
    have that cleanup skipped just because logging failed (found via
    scripts/smoke_cli.sh: orphaned Xvfb/ffmpeg after `docich down`).
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{component}] {msg}"
    try:
        print(line, file=fh, flush=True)
    except OSError:
        pass
    try:
        print(line, flush=True)
    except OSError:
        pass


def _next_backoff(current: int, alive_s: float) -> int:
    if alive_s >= BACKOFF_RESET_AFTER_S:
        return BACKOFF_START_S
    return current


def _wait_pid_exited(pid: int, timeout_s: float) -> bool:
    """Poll for a child's exit via raw os.waitpid(WNOHANG), deliberately NOT
    going through Popen.wait()/Popen.poll().

    Why: when this runs inside a signal handler that interrupted the main
    loop's un-timed `p.wait()` on the SAME Popen object, CPython's
    subprocess module is still holding that Popen's internal (non-reentrant)
    `_waitpid_lock` -- the interrupted call is paused *inside* the `with
    self._waitpid_lock:` block, not past it. Popen.wait()/poll() only ever
    try a non-blocking `acquire(False)` on that same lock, so a reentrant
    call from here always fails to acquire it and just spins until its own
    timeout, regardless of whether the child already exited (verified: this
    made every `docich stop`/`docich down` take a flat TERMINATE_WAIT_S + 5
    == 15s, even though the child had already died on the first
    p.terminate()). Calling os.waitpid() directly sidesteps that lock
    entirely and is safe here because the interrupted outer p.wait() will
    never resume/retry once this handler raises SystemExit (see run_loop).
    """
    deadline = time.monotonic() + timeout_s
    delay = 0.0005
    while True:
        try:
            reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True  # no such child left: already reaped elsewhere
        if reaped_pid == pid:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)
        delay = min(delay * 2, 0.05)


def run_loop(
    component: str,
    g: GlobalConfig,
    build: Callable[[], "tuple[list[str], dict]"],
    *,
    pre: Callable[[], None] | None = None,
    post_start: Callable[[subprocess.Popen], None] | None = None,
    log_command: Callable[[list[str]], list[str]] | None = None,
) -> None:
    """Run `build()` -> spawn -> wait forever, restarting with backoff on exit/error."""
    state = State(g)
    state.ensure()
    log_path = state.logs_dir / f"{component}.log"

    with log_path.open("a", encoding="utf-8") as fh:
        current_proc: list[subprocess.Popen | None] = [None]

        def handle_signal(signum, _frame):
            # 子プロセスの停止を最優先する: ログ出力より前に terminate/wait/kill を
            # 済ませる。log_line は内部で OSError を握りつぶし例外を漏らさないが
            # (壊れた pty への書き込みで子プロセスが孤児化するバグの再発防止として)、
            # ログ呼び出しを一切挟まない構造にすることで、万一の失敗モードにも
            # 依存せず子プロセスの後始末が必ず完了することを保証する。
            #
            # 生死確認は Popen.wait()/poll() ではなく _wait_pid_exited (raw
            # os.waitpid) を使う: ここは「外側の (中断された) p.wait() が握った
            # ままの _waitpid_lock」を再入することになるため、Popen.wait()/poll()
            # を呼ぶと子の生死に関わらず必ず timeout まで空回りしてしまう
            # (_wait_pid_exited のドキュメント参照)。
            p = current_proc[0]
            force_killed = False
            if p is not None:
                p.terminate()
                if not _wait_pid_exited(p.pid, TERMINATE_WAIT_S):
                    p.kill()
                    force_killed = True
                    _wait_pid_exited(p.pid, 5)
            if force_killed:
                log_line(fh, component, f"シグナル {signum} を受信、子プロセスが応答しないため kill しました")
            else:
                log_line(fh, component, f"シグナル {signum} を受信して停止しました")
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
                shown_cmd = log_command(cmd) if log_command is not None else cmd
                log_line(fh, component, f"起動: {' '.join(shown_cmd)}")
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
            # run_loop と異なり子プロセスを持たない (fn は同一プロセス内で走る
            # callable) ため後始末の対象が無く、並べ替えは不要。log_line が
            # OSError を漏らさないことだけで sys.exit(0) 到達は保証される。
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
