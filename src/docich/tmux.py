"""tmux session/window management wrapper.

All tmux invocations go through procs.run(strip_tmux=True) so that docich can
be operated from inside a tmux session without the nested `tmux` refusing to
run (architecture.md §9.6).
"""
from __future__ import annotations

import shlex

from . import procs

SESSION = "docich"


class Tmux:
    def __init__(self, session: str = SESSION):
        self.session = session

    def _run(self, args: list[str], **kwargs):
        return procs.run(["tmux", *args], strip_tmux=True, **kwargs)

    # --- default session (SESSION) ---

    def has_session(self) -> bool:
        r = self._run(["has-session", "-t", self.session])
        return r.returncode == 0

    def ensure_session(self) -> None:
        if not self.has_session():
            self._run(["new-session", "-d", "-s", self.session, "-x", "200", "-y", "50"])

    def kill_session(self) -> None:
        self.kill_session_named(self.session)

    def has_window(self, name: str) -> bool:
        return name in self.list_windows()

    def list_windows(self) -> list[str]:
        r = self._run(["list-windows", "-t", self.session, "-F", "#{window_name}"])
        if r.returncode != 0:
            return []
        return [line for line in r.stdout.splitlines() if line]

    def new_window(self, name: str, cmd: list[str], env: dict | None = None) -> None:
        args = ["new-window", "-d", "-t", self.session, "-n", name]
        if env:
            for k, v in env.items():
                args += ["-e", f"{k}={v}"]
        args.append(shlex.join(cmd))
        self._run(args)

    def kill_window(self, name: str) -> None:
        # 存在しない window の kill はエラーになるが無視する
        self._run(["kill-window", "-t", f"{self.session}:{name}"])

    def window_alive(self, name: str) -> bool:
        return self.has_window(name)

    # --- named (separate) sessions, e.g. "docich-game" ---

    def has_session_named(self, session: str) -> bool:
        r = self._run(["has-session", "-t", session])
        return r.returncode == 0

    def new_game_session(self, session: str, cmd: list[str], cols: int, rows: int) -> None:
        self._run(["new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows), shlex.join(cmd)])
        # 配信画面に tmux の緑ステータスバーが映り込むため off にする (architecture.md §4.2)
        self.set_status_off(session)

    def set_status_off(self, session: str) -> None:
        self._run(["set-option", "-t", session, "status", "off"])

    def kill_session_named(self, session: str) -> None:
        # 存在しないセッションの kill はエラーになるが無視する
        self._run(["kill-session", "-t", session])

    def capture_pane(self, session: str) -> str:
        r = self._run(["capture-pane", "-p", "-t", session])
        return r.stdout if r.returncode == 0 else ""

    def send_keys(self, session: str, keys: list[str], literal: bool = False) -> None:
        args = ["send-keys", "-t", session]
        if literal:
            args.append("-l")
        args += keys
        self._run(args)

    def set_manual_size(self, session: str, cols: int, rows: int) -> None:
        self._run(["set-option", "-t", session, "window-size", "manual"])
        self._run(["resize-window", "-t", session, "-x", str(cols), "-y", str(rows)])
