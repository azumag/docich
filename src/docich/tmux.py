"""tmux session/window management wrapper.

All tmux invocations go through procs.run(strip_tmux=True) so that docich can
be operated from inside a tmux session without the nested `tmux` refusing to
run (architecture.md §9.6).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from . import procs
from .naming import runtime_id_generation, validate_runtime_id, validate_tmux_name, validate_tmux_target

SESSION = "docich"


class TmuxError(RuntimeError):
    """A checked tmux operation failed."""


class OwnershipMismatchError(TmuxError):
    """Refuse to mutate a tmux object not owned by the expected runtime."""


@dataclass(frozen=True)
class TmuxOwnership:
    runtime_id: str
    generation: int
    role: str

    def __post_init__(self) -> None:
        validate_runtime_id(self.runtime_id)
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("generation は1以上の整数である必要があります")
        if runtime_id_generation(self.runtime_id) != self.generation:
            raise ValueError("runtime_id がgenerationと一致しません")
        if self.role not in {"game", "agent", "adapter"}:
            raise ValueError("role は game/agent/adapter のいずれかである必要があります")


@dataclass(frozen=True)
class PaneState:
    dead: bool
    pid: int


class Tmux:
    def __init__(self, session: str = SESSION):
        self.session = validate_tmux_name(session)

    def _run(self, args: list[str], **kwargs):
        return procs.run(["tmux", *args], strip_tmux=True, **kwargs)

    def _checked(self, args: list[str], operation: str):
        result = self._run(args)
        if result.returncode != 0:
            detail = (result.stderr or "").replace("\n", " ").strip()[:200]
            suffix = f": {detail}" if detail else ""
            raise TmuxError(f"tmux {operation} に失敗しました{suffix}")
        return result

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

    def new_window_checked(self, name: str, cmd: list[str], env: dict | None = None) -> None:
        validate_tmux_name(name)
        args = ["new-window", "-d", "-t", self.session, "-n", name]
        if env:
            for key, value in env.items():
                args += ["-e", f"{key}={value}"]
        args.append(shlex.join(cmd))
        self._checked(args, "window作成")

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

    def new_game_session_checked(self, session: str, cmd: list[str], cols: int, rows: int) -> None:
        validate_tmux_name(session)
        self._checked(
            ["new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows), shlex.join(cmd)],
            "session作成",
        )
        self._checked(["set-option", "-t", session, "status", "off"], "status設定")

    def set_status_off(self, session: str) -> None:
        self._run(["set-option", "-t", session, "status", "off"])

    def kill_session_named(self, session: str) -> None:
        # 存在しないセッションの kill はエラーになるが無視する
        self._run(["kill-session", "-t", session])

    @staticmethod
    def _ownership_values(ownership: TmuxOwnership) -> tuple[tuple[str, str], ...]:
        return (
            ("@docich_runtime_id", ownership.runtime_id),
            ("@docich_generation", str(ownership.generation)),
            ("@docich_role", ownership.role),
        )

    def set_window_ownership(self, target: str, ownership: TmuxOwnership) -> None:
        validate_tmux_target(target)
        for option, value in self._ownership_values(ownership):
            self._checked(
                ["set-option", "-w", "-t", target, option, value],
                "window ownership設定",
            )

    def set_session_ownership(self, session: str, ownership: TmuxOwnership) -> None:
        validate_tmux_name(session)
        for option, value in self._ownership_values(ownership):
            self._checked(
                ["set-option", "-t", session, option, value],
                "session ownership設定",
            )

    def _read_option(self, target: str, option: str, *, window: bool) -> str:
        args = ["show-options"]
        if window:
            args.append("-w")
        args += ["-v", "-t", target, option]
        result = self._checked(args, "ownership確認")
        return result.stdout.strip()

    def window_target_exists(self, target: str) -> bool:
        validate_tmux_target(target)
        result = self._run(["display-message", "-p", "-t", target, "#{window_id}"])
        if result.returncode == 0:
            return True
        if self._target_missing(result.stderr):
            return False
        detail = (result.stderr or "").replace("\n", " ").strip()[:200]
        raise TmuxError(f"tmux window存在確認に失敗しました: {detail or 'unknown error'}")

    def session_target_exists(self, session: str) -> bool:
        validate_tmux_name(session)
        result = self._run(["has-session", "-t", session])
        if result.returncode == 0:
            return True
        if self._target_missing(result.stderr):
            return False
        detail = (result.stderr or "").replace("\n", " ").strip()[:200]
        raise TmuxError(f"tmux session存在確認に失敗しました: {detail or 'unknown error'}")

    @staticmethod
    def _target_missing(stderr: str | None) -> bool:
        detail = (stderr or "").lower()
        return any(
            marker in detail
            for marker in (
                "not found",
                "can't find",
                "no server running",
                "failed to connect to server",
            )
        )

    def read_window_ownership(self, target: str) -> TmuxOwnership:
        validate_tmux_target(target)
        try:
            return TmuxOwnership(
                runtime_id=self._read_option(target, "@docich_runtime_id", window=True),
                generation=int(self._read_option(target, "@docich_generation", window=True)),
                role=self._read_option(target, "@docich_role", window=True),
            )
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("window ownership tagが不正です") from exc

    def read_session_ownership(self, session: str) -> TmuxOwnership:
        validate_tmux_name(session)
        try:
            return TmuxOwnership(
                runtime_id=self._read_option(session, "@docich_runtime_id", window=False),
                generation=int(self._read_option(session, "@docich_generation", window=False)),
                role=self._read_option(session, "@docich_role", window=False),
            )
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("session ownership tagが不正です") from exc

    def kill_window_owned(self, target: str, expected: TmuxOwnership) -> bool:
        validate_tmux_target(target)
        if not self.window_target_exists(target):
            return False
        actual = self.read_window_ownership(target)
        if actual != expected:
            raise OwnershipMismatchError(
                f"window ownershipが一致しません (expected={expected}, actual={actual})"
            )
        self._checked(["kill-window", "-t", target], "window停止")
        return True

    def kill_session_owned(self, session: str, expected: TmuxOwnership) -> bool:
        validate_tmux_name(session)
        if not self.session_target_exists(session):
            return False
        actual = self.read_session_ownership(session)
        if actual != expected:
            raise OwnershipMismatchError(
                f"session ownershipが一致しません (expected={expected}, actual={actual})"
            )
        self._checked(["kill-session", "-t", session], "session停止")
        return True

    def capture_pane(self, session: str) -> str:
        r = self._run(["capture-pane", "-p", "-t", session])
        return r.stdout if r.returncode == 0 else ""

    def capture_pane_checked(self, target: str) -> str:
        validate_tmux_target(target)
        return self._checked(["capture-pane", "-p", "-t", target], "pane capture").stdout

    def pane_states_checked(self, target: str) -> list[PaneState]:
        validate_tmux_target(target)
        result = self._checked(
            ["list-panes", "-t", target, "-F", "#{pane_dead}\t#{pane_pid}"],
            "pane検査",
        )
        states: list[PaneState] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 2 or parts[0] not in {"0", "1"}:
                raise TmuxError("tmux pane検査の応答形式が不正です")
            try:
                pid = int(parts[1])
            except ValueError as exc:
                raise TmuxError("tmux pane検査のPIDが不正です") from exc
            if pid < 1:
                raise TmuxError("tmux pane検査のPIDが不正です")
            states.append(PaneState(dead=parts[0] == "1", pid=pid))
        if not states:
            raise TmuxError("tmux pane検査でpaneが見つかりません")
        return states

    def send_keys(self, session: str, keys: list[str], literal: bool = False) -> None:
        args = ["send-keys", "-t", session]
        if literal:
            args.append("-l")
        args += keys
        self._run(args)

    def set_manual_size(self, session: str, cols: int, rows: int) -> None:
        self._run(["set-option", "-t", session, "window-size", "manual"])
        self._run(["resize-window", "-t", session, "-x", str(cols), "-y", str(rows)])
