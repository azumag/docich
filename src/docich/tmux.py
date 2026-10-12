"""tmux session/window management wrapper.

All tmux invocations go through procs.run(strip_tmux=True) so that docich can
be operated from inside a tmux session without the nested `tmux` refusing to
run (architecture.md §9.6).
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from . import procs
from .naming import (
    runtime_id_generation,
    validate_runtime_id,
    validate_tmux_name,
    validate_tmux_session_id,
    validate_tmux_session_ref,
    validate_tmux_target,
    validate_tmux_window_id,
    validate_tmux_window_ref,
)
from .process_tree import (
    PaneProcessScope,
    ProcessIdentityError,
    TerminationResult,
    ancestor_pids,
    descendant_pids,
    is_running,
    process_cgroup,
    process_environ,
    process_pgid,
    processes_in_pane_scopes,
    processes_with_env,
    terminate_owned_processes,
    terminate_process_tree,
)
from .teardown_evidence import (
    PaneScopeEvidence,
    TeardownEvidence,
    adapter_recorder,
)

logger = logging.getLogger(__name__)

SESSION = "docich"

# Every pane process is tagged with the runtime identity that owns it
# (Issue #1105).  A process that escapes the pane's descendant tree — e.g. the
# pane leader exited and tmux reparented the game — keeps these tags, so a
# teardown can still prove ownership of exactly the runtime it is stopping.
OWNERSHIP_RUNTIME_ID_ENV = "DOCICH_TMUX_RUNTIME_ID"
OWNERSHIP_GENERATION_ENV = "DOCICH_TMUX_GENERATION"
OWNERSHIP_ROLE_ENV = "DOCICH_TMUX_ROLE"

# Fail closed instead of reaping an implausible number of processes: a bigger
# population means the ownership inference itself is not trustworthy.
MAX_ORPHAN_SWEEP_PROCESSES = 64


class ProtectedTeardownPids(frozenset):
    """PIDs a teardown must never signal, re-evaluated at sweep time.

    The protection set is necessarily a snapshot: it is taken *before* the
    pane leaders are stopped, while their ancestors (the tmux server) and the
    shared-infrastructure panes are still observable.  A process spawned after
    that snapshot — a child forked by a pane that is still running, or the
    leader of a pane created concurrently — inherits the runtime's ownership
    tags from the tagged server, so it matches ``processes_with_env`` and
    would be reclaimed even though it never belonged to the target (Issue
    #1105).  This set therefore keeps the *roots* it was built from (the
    server and every other pane leader) and re-derives their descendants from
    a fresh process table on every membership test, so a later child of a
    protected pane is protected too.

    The target panes and their subtrees stay excluded: the post-condition
    sweep must still reclaim the orphans that escaped the target's own tree,
    so reclaiming them is never blocked by a stale ancestor chain.
    """

    __slots__ = ("roots", "targets")

    def __new__(
        cls,
        pids: Iterable[int],
        roots: Iterable[int] = (),
        targets: Iterable[int] = (),
    ):
        members = super().__new__(cls, (pid for pid in pids if pid > 1))
        members.roots = frozenset(pid for pid in roots if pid > 1)
        members.targets = frozenset(pid for pid in targets if pid > 1)
        return members

    def _dynamic_descendants(self, roots: list[int]) -> set[int]:
        return set(descendant_pids(roots))

    def _excluded_by_target(self, pid: int) -> bool:
        """True when ``pid`` belongs to the target subtree (stay reclaimable)."""

        if not self.targets:
            return False
        return pid in self._dynamic_descendants(list(self.targets))

    def __contains__(self, pid: object) -> bool:
        if super().__contains__(pid):
            return True
        if type(pid) is not int or not self.roots:
            return False
        if self._excluded_by_target(pid):
            return False
        # Only a *fresh* descendant of a protected root is protected.  The
        # snapshot's own members were already checked above.
        return pid in self._dynamic_descendants(list(self.roots))

    def expanded(self) -> frozenset[int]:
        """The snapshot plus every dynamic descendant of its roots.

        Callers that test many candidates use this to derive the whole set
        once; :meth:`__contains__` stays available for the fresh per-PID proof
        read later, after more time has passed.
        """

        if not self.roots:
            return frozenset(self)
        fresh = self._dynamic_descendants(list(self.roots))
        fresh.difference_update(self._dynamic_descendants(list(self.targets)))
        return frozenset((*self, *fresh))


# Throwaway evaluation sessions (resolver/bot_eval/improve/ninvaders arena)
# must never share the production tmux server.  A tmux client with no server
# running starts one in the caller's cgroup; when that caller is a transient
# `systemd-run` improvement unit, systemd kills the whole cgroup when the unit
# ends and takes every production pane with it (Issue #1280).  Evaluations get
# their own tmux socket so a crashed or unit-owned server can only lose eval
# sessions.
EVAL_SERVER = "docich-eval"


def eval_server_name() -> str:
    """Name of the private tmux server used for evaluation sessions.

    ``DOCICH_EVAL_TMUX_SERVER`` overrides the default for isolated tests or
    side-by-side evaluators.
    """

    override = os.environ.get("DOCICH_EVAL_TMUX_SERVER", "").strip()
    if override:
        validate_tmux_name(override)
        return override
    return EVAL_SERVER


def eval_tmux_argv(args: list[str]) -> list[str]:
    """tmux argv targeting the private evaluation server."""

    return ["tmux", "-L", eval_server_name(), *args]


# strict existence で「不在」とみなす stderr マーカー。共有
# _target_missing() の "failed to connect to server" は含めない: 接続
# 失敗は存在確認そのものの失敗であり、不在の証明ではない。
_STRICT_MISSING_MARKERS = ("not found", "can't find", "no server running")


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


def ownership_environment(ownership: TmuxOwnership) -> dict[str, str]:
    """Environment tags that mark every process spawned for ``ownership``."""

    return {
        OWNERSHIP_RUNTIME_ID_ENV: ownership.runtime_id,
        OWNERSHIP_GENERATION_ENV: str(ownership.generation),
        OWNERSHIP_ROLE_ENV: ownership.role,
    }


class Tmux:
    def __init__(self, session: str = SESSION, *, server: str | None = None):
        self.session = validate_tmux_name(session)
        # An explicit private server (``tmux -L``) keeps evaluation sockets
        # apart from the production server (Issue #1280).  ``None`` keeps the
        # default server used by every production component.
        self.server = validate_tmux_name(server) if server is not None else None
        # Set by bind_teardown_owner() when an adapter binds itself, so the
        # teardown evidence (Issue #1936) this wrapper records reaches the
        # adapter's recorder.  ``None`` means nobody is recording.
        self._docich_adapter: object | None = None

    def bind_teardown_owner(self, adapter: object) -> None:
        """Record which adapter's teardown this wrapper is reporting on.

        Every adapter owns its own :class:`Tmux`, so binding here is enough
        for the wrapper to find the recorder of the runtime being torn down.
        """

        self._docich_adapter = adapter

    def _run(self, args: list[str], **kwargs):
        command = ["tmux", "-L", self.server, *args] if self.server is not None else ["tmux", *args]
        return procs.run(command, strip_tmux=True, **kwargs)

    def _checked(self, args: list[str], operation: str):
        result = self._run(args)
        if result.returncode != 0:
            detail = (result.stderr or "").replace("\n", " ").strip()[:200]
            suffix = f": {detail}" if detail else ""
            raise TmuxError(f"tmux {operation} に失敗しました{suffix}")
        return result

    @staticmethod
    def _run_bounded(
        args: list[str],
        *,
        operation: str,
        max_output_bytes: int,
        timeout_s: float,
    ) -> bytes:
        try:
            result = procs.run_bounded_output(
                ["tmux", *args],
                max_output_bytes=max_output_bytes,
                timeout=timeout_s,
                strip_tmux=True,
            )
        except (procs.OutputLimitExceeded, subprocess.TimeoutExpired) as exc:
            raise TmuxError(f"tmux {operation} exceeded its resource bound") from exc
        if result.returncode != 0:
            raise TmuxError(f"tmux {operation} failed")
        return result.stdout

    # --- default session (SESSION) ---

    def has_session(self) -> bool:
        r = self._run(["has-session", "-t", self.session])
        return r.returncode == 0

    def ensure_session(self) -> None:
        if not self.has_session():
            self._run(["new-session", "-d", "-s", self.session, "-x", "200", "-y", "50"])

    def kill_session(self, *, allow_shared: bool = False) -> None:
        self.kill_session_named(self.session, allow_shared=allow_shared)

    def has_window(self, name: str) -> bool:
        return name in self.list_windows()

    def list_windows(self) -> list[str]:
        r = self._run(["list-windows", "-t", self.session, "-F", "#{window_name}"])
        if r.returncode != 0:
            return []
        return [line for line in r.stdout.splitlines() if line]

    def list_windows_bounded(
        self,
        session: str,
        *,
        max_bytes: int = 4096,
        timeout_s: float = 0.5,
    ) -> list[str]:
        validate_tmux_session_ref(session)
        output = self._run_bounded(
            ["list-windows", "-t", session, "-F", "#{window_name}"],
            operation="window list",
            max_output_bytes=max_bytes,
            timeout_s=timeout_s,
        )
        return [line for line in output.decode("utf-8", errors="replace").splitlines() if line]

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

    def create_window_owned(
        self,
        name: str,
        cmd: list[str],
        ownership: TmuxOwnership,
        env: dict | None = None,
        cwd: str | None = None,
    ) -> str:
        """Create, tag and verify a window, rolling back its stable ID on failure."""

        validate_tmux_name(name)
        args = [
            "new-window", "-d", "-P", "-F", "#{window_id}",
            "-t", self.session, "-n", name,
        ]
        if cwd:
            args += ["-c", cwd]
        for key, value in self._pane_environment(ownership, env).items():
            args += ["-e", f"{key}={value}"]
        args.append(shlex.join(cmd))
        result = self._checked(args, "window作成")
        try:
            window_id = validate_tmux_window_id(result.stdout.strip())
        except ValueError as exc:
            raise TmuxError("tmux window作成の応答IDが不正です") from exc
        try:
            self.set_window_ownership(window_id, ownership)
            actual = self.read_window_ownership(window_id)
            if actual != ownership:
                raise OwnershipMismatchError(
                    f"window ownership検証に失敗しました (expected={ownership}, actual={actual})"
                )
        except Exception as exc:
            self._rollback_created("window", window_id, exc)
            raise
        return window_id

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

    def create_game_session_owned(
        self,
        session: str,
        cmd: list[str],
        cols: int,
        rows: int,
        ownership: TmuxOwnership,
        env: dict | None = None,
    ) -> str:
        """Create, configure, tag and verify a session as one rollback-safe primitive."""

        validate_tmux_name(session)
        args = [
            "new-session", "-d", "-P", "-F", "#{session_id}",
            "-s", session, "-x", str(cols), "-y", str(rows),
        ]
        for key, value in self._pane_environment(ownership, env).items():
            args += ["-e", f"{key}={value}"]
        args.append(shlex.join(cmd))
        result = self._checked(args, "session作成")
        try:
            session_id = validate_tmux_session_id(result.stdout.strip())
        except ValueError as exc:
            raise TmuxError("tmux session作成の応答IDが不正です") from exc
        try:
            self._checked(["set-option", "-t", session_id, "status", "off"], "status設定")
            self.set_session_ownership(session_id, ownership)
            actual = self.read_session_ownership(session_id)
            if actual != ownership:
                raise OwnershipMismatchError(
                    f"session ownership検証に失敗しました (expected={ownership}, actual={actual})"
                )
        except Exception as exc:
            self._rollback_created("session", session_id, exc)
            raise
        return session_id

    def _rollback_created(self, object_type: str, object_id: str, cause: Exception) -> None:
        command = "kill-window" if object_type == "window" else "kill-session"
        result = self._run([command, "-t", object_id])
        if result.returncode != 0 and not self._target_missing(result.stderr):
            detail = (result.stderr or "unknown error").replace("\n", " ").strip()[:200]
            raise TmuxError(
                f"tmux {object_type}初期化失敗後のrollbackにも失敗しました: {detail}"
            ) from cause

    def set_status_off(self, session: str) -> None:
        self._run(["set-option", "-t", session, "status", "off"])

    def kill_session_named(self, session: str, *, allow_shared: bool = False) -> None:
        # Issue #219: 共有 session ("docich") の kill は明示的な全体停止
        # (cmd_down) 経由でのみ許可する。汎用経路からの誤 kill は
        # fail-closed で拒否し、将来の再発を大音量の証拠つきで検出する。
        # 共有 session が最後の session だった場合、その kill は tmux server
        # 自体の終了を招き、watchdog の再作成で「server 再起動」に見える
        # (9/10 の socket mtime 更新の観測と整合する再構成)。
        if session == SESSION and not allow_shared:
            raise OwnershipMismatchError(
                "共有 session 'docich' の kill は汎用経路では拒否します"
                " (全体停止 cmd_down 経由で allow_shared=True が必要)"
            )
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
        validate_tmux_window_ref(target)
        for option, value in self._ownership_values(ownership):
            self._checked(
                ["set-option", "-w", "-t", target, option, value],
                "window ownership設定",
            )

    def set_session_ownership(self, session: str, ownership: TmuxOwnership) -> None:
        validate_tmux_session_ref(session)
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

    def _read_option_bounded(
        self,
        target: str,
        option: str,
        *,
        window: bool,
        timeout_s: float,
    ) -> str:
        args = ["show-options"]
        if window:
            args.append("-w")
        args += ["-v", "-t", target, option]
        output = self._run_bounded(
            args,
            operation="ownership check",
            max_output_bytes=256,
            timeout_s=timeout_s,
        )
        return output.decode("utf-8", errors="replace").strip()

    def window_target_exists(self, target: str, *, strict: bool = False) -> bool:
        """Return True when the window exists.

        Name targets are checked by listing the selected session's windows:
        `display-message` may fall back to another window for a nonexistent
        name. Stable `@N` window IDs remain exact tmux targets and are probed
        directly so this helper keeps accepting every ref allowed by
        `validate_tmux_window_ref()`.

        Non-strict mode treats "target missing" markers (including
        "failed to connect to server") as absent.  Strict mode only treats
        genuine-absence markers as absent and raises TmuxError otherwise, so
        callers can distinguish "confirmed absent" from "the check itself
        failed" (a connection failure is never proof of absence).
        """
        validate_tmux_window_ref(target)
        if target.startswith("@"):
            result = self._run(["display-message", "-p", "-t", target, "#{window_id}"])
            if result.returncode == 0:
                return result.stdout.strip() == target
        else:
            if ":" in target:
                session, _, name = target.partition(":")
            else:
                session, name = self.session, target
            result = self._run(["list-windows", "-t", session, "-F", "#{window_name}"])
            if result.returncode == 0:
                return name in [line for line in result.stdout.splitlines() if line]
        detail = (result.stderr or "").replace("\n", " ").strip()
        if strict:
            if any(marker in detail.lower() for marker in _STRICT_MISSING_MARKERS):
                return False
        elif self._target_missing(result.stderr):
            return False
        raise TmuxError(f"tmux window存在確認に失敗しました: {detail[:200] or 'unknown error'}")

    def session_target_exists(self, session: str, *, strict: bool = False) -> bool:
        """Return True when the session exists.  See window_target_exists for
        the strict distinction."""
        validate_tmux_session_ref(session)
        result = self._run(["has-session", "-t", session])
        if result.returncode == 0:
            return True
        detail = (result.stderr or "").replace("\n", " ").strip()
        if strict:
            if any(marker in detail.lower() for marker in _STRICT_MISSING_MARKERS):
                return False
        elif self._target_missing(result.stderr):
            return False
        raise TmuxError(f"tmux session存在確認に失敗しました: {detail[:200] or 'unknown error'}")

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
        validate_tmux_window_ref(target)
        try:
            return TmuxOwnership(
                runtime_id=self._read_option(target, "@docich_runtime_id", window=True),
                generation=int(self._read_option(target, "@docich_generation", window=True)),
                role=self._read_option(target, "@docich_role", window=True),
            )
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("window ownership tagが不正です") from exc

    def read_session_ownership(self, session: str) -> TmuxOwnership:
        validate_tmux_session_ref(session)
        try:
            return TmuxOwnership(
                runtime_id=self._read_option(session, "@docich_runtime_id", window=False),
                generation=int(self._read_option(session, "@docich_generation", window=False)),
                role=self._read_option(session, "@docich_role", window=False),
            )
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("session ownership tagが不正です") from exc

    def read_window_ownership_bounded(
        self,
        target: str,
        *,
        timeout_s: float = 0.5,
    ) -> TmuxOwnership:
        validate_tmux_window_ref(target)
        try:
            return TmuxOwnership(
                runtime_id=self._read_option_bounded(
                    target, "@docich_runtime_id", window=True, timeout_s=timeout_s
                ),
                generation=int(
                    self._read_option_bounded(
                        target, "@docich_generation", window=True, timeout_s=timeout_s
                    )
                ),
                role=self._read_option_bounded(
                    target, "@docich_role", window=True, timeout_s=timeout_s
                ),
            )
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("bounded window ownership tag is invalid") from exc

    def read_session_ownership_bounded(
        self,
        session: str,
        *,
        timeout_s: float = 0.5,
    ) -> TmuxOwnership:
        validate_tmux_session_ref(session)
        try:
            return TmuxOwnership(
                runtime_id=self._read_option_bounded(
                    session, "@docich_runtime_id", window=False, timeout_s=timeout_s
                ),
                generation=int(
                    self._read_option_bounded(
                        session, "@docich_generation", window=False, timeout_s=timeout_s
                    )
                ),
                role=self._read_option_bounded(
                    session, "@docich_role", window=False, timeout_s=timeout_s
                ),
            )
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("bounded session ownership tag is invalid") from exc

    @staticmethod
    def _pane_pid_listing(stdout: str) -> list[int]:
        """Parse a ``#{pane_pid}`` listing into positive, unique PIDs."""

        pids: list[int] = []
        for raw in stdout.splitlines():
            try:
                pid = int(raw.strip())
            except ValueError:
                continue
            if pid > 0:
                pids.append(pid)
        return list(dict.fromkeys(pids))

    def _pane_pids(self, target: str) -> list[int]:
        """Return pane leaders for an already ownership-checked target."""

        # list-panes -a ignores -t and would enumerate every pane on the tmux
        # server.  Cleanup must stay scoped to the already ownership-checked
        # window/session target, so never use -a here.
        result = self._run(["list-panes", "-t", target, "-F", "#{pane_pid}"])
        if result.returncode != 0:
            return []
        return self._pane_pid_listing(result.stdout)

    def _server_pane_leaders(self) -> list[int] | None:
        """Pane leaders of *every* pane on the server hosting the target.

        ``list-panes -a`` enumerates the whole shared server, which is exactly
        the population a teardown must leave alone outside its own target
        (Issue #1105).  ``None`` means the enumeration failed: the caller then
        cannot prove which panes are outside its target and must not sweep.
        """

        result = self._run(["list-panes", "-a", "-F", "#{pane_pid}"])
        if result.returncode != 0:
            return None
        return self._pane_pid_listing(result.stdout)

    @staticmethod
    def _pane_environment(ownership: TmuxOwnership, env: dict | None) -> dict[str, str]:
        """Explicit ``env`` plus the runtime ownership tags (Issue #1105)."""

        merged = {str(key): str(value) for key, value in (env or {}).items()}
        merged.update(ownership_environment(ownership))
        return merged

    def _server_pid(self) -> int | None:
        """PID of the tmux server that hosts this wrapper (best effort).

        ``#{pid}`` is the server PID for every client attached to a server, so
        this identifies the shared server a teardown must never signal
        (Issue #1105).  A missing/unreachable server degrades to ``None``.
        """

        result = self._run(["display-message", "-p", "#{pid}"])
        if result.returncode != 0:
            return None
        try:
            pid = int((result.stdout or "").strip())
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None

    def _protected_teardown_pids(
        self, pane_leaders: list[int] | tuple[int, ...]
    ) -> ProtectedTeardownPids:
        """PIDs a teardown sweep must never signal (Issue #1105).

        The runtime/role tags are exported into every process started from a
        tagged pane, and tmux hands a client's environment to the server it
        starts plus every pane that server later creates without an explicit
        ``-e`` override.  The shared-infrastructure windows (display, audio,
        watchdog, stream, trading) are created by the untagged ``new_window``
        helper, so on a tagged server they — and the server itself — carry the
        runtime's tags and look owned to ``processes_with_env``.  Signalling
        any of them topples the shared foundation and, with it, the whole
        server.  The sweep therefore protects:

        * the server hosting the target and its ancestors,
        * every *other* live pane leader on that server, plus its descendant
          tree,
        * this process's own ancestors and the target panes' ancestors.

        The returned set re-derives the descendants of those roots on every
        membership test, so a process spawned after this snapshot — a child of
        a still-running protected pane, or the leader of a pane created
        concurrently — is protected as well instead of being swept as an
        "orphan" that merely inherited the tags.

        The target panes themselves stay legitimate victims.  An orphan that
        already left its pane has been reparented, so it is a descendant of
        none of the protected panes and remains reclaimable.  When the server's
        pane listing cannot be read the caller cannot prove which panes are
        outside its target, so this fails closed instead of sweeping.
        """

        protected: set[int] = set(ancestor_pids())
        protected_roots: set[int] = set()
        server_pid = self._server_pid()
        if server_pid is not None:
            protected.add(server_pid)
            protected.update(ancestor_pids(server_pid))
        target_leaders = {pid for pid in pane_leaders if pid > 0}
        server_leaders = self._server_pane_leaders()
        if server_leaders is None:
            raise TmuxError(
                "teardown: 同一 tmux server の pane 一覧を取得できないため、"
                "共有基盤を巻き込まずに回収対象を確定できません"
            )
        others = [pid for pid in server_leaders if pid not in target_leaders]
        protected.update(others)
        if others:
            protected.update(descendant_pids(others))
            # The server and every other pane keep running after this target
            # is stopped, so their descendants are exactly the population that
            # can appear *after* the snapshot.  Re-deriving from these roots at
            # sweep time protects a child forked by a live pane and a pane
            # created concurrently — both inherit the ownership tags from the
            # tagged server and would otherwise be reclaimed as "orphans".
            protected_roots.update(others)
            if server_pid is not None:
                protected_roots.add(server_pid)
        for leader in target_leaders:
            # The pane leader's parent chain is the tmux server and above; the
            # leader itself stays a legitimate victim.
            protected.update(ancestor_pids(leader))
        protected.difference_update(target_leaders)
        # The target subtree stays reclaimable: a fresh descendant of a
        # protected root that is also a descendant of a target pane (a spawn
        # racing with the teardown) must not be shielded by the dynamic set.
        return ProtectedTeardownPids(protected, protected_roots, target_leaders)

    def _stop_scoped_processes(
        self, target: str
    ) -> tuple[tuple[int, ...], dict[int, PaneProcessScope], frozenset[int]]:
        """Stop the pane trees of an ownership-checked target.

        Returns the PIDs that survived, the pane leaders' exact group/scopes (used
        by the post-condition sweep to find children that left the descendant
        tree) and the PIDs the sweep must never signal.  The protection set is
        snapshotted *before* the leaders are stopped, while their ancestors
        (the tmux server) and the shared-infrastructure panes are still
        observable.
        """

        pids = self._pane_pids(target)
        protected = self._protected_teardown_pids(pids)
        if not pids:
            return (), {}, protected
        groups: dict[int, PaneProcessScope] = {}
        for pid in pids:
            cgroup = process_cgroup(pid)
            pgid = process_pgid(pid)
            if pgid is not None and cgroup == process_cgroup(pid):
                scope = PaneProcessScope.from_cgroup(pgid, cgroup)
                if scope is not None:
                    groups[pid] = scope
        outcome = terminate_process_tree(pids)
        self._record_termination(groups, outcome)
        return outcome.remaining, groups, protected

    # --- teardown evidence (Issue #1936) ---------------------------------

    def _active_recorder(self) -> "TeardownEvidence | None":
        """The recorder bound to the runtime currently being torn down.

        The teardown layer binds the recorder onto the adapter that owns the
        teardown, and every adapter holds its own :class:`~docich.tmux.Tmux`.
        A shared/unbound Tmux has no recorder, so recording stays opt-in and a
        non-instrumented teardown behaves exactly as before.
        """

        owner = getattr(self, "_docich_adapter", None)
        return adapter_recorder(owner) if owner is not None else None

    def _record_target(self, target: str, expected: TmuxOwnership, *, window: bool) -> None:
        """Record which tmux object and runtime identity a teardown stopped.

        Called *before* the stop so the ids and ownership tags are still
        readable.  Silent when nobody is recording.
        """

        recorder = self._active_recorder()
        if recorder is None:
            return
        target_id = self._target_id(target, window=window)
        if window:
            if expected.role == "agent":
                recorder.agent_window_id = target_id
            else:
                recorder.game_window_id = target_id
        else:
            recorder.session_id = target_id
        if target not in recorder.targets:
            recorder.targets = (*recorder.targets, target)
        tag = f"{expected.runtime_id}:{expected.generation}:{expected.role}"
        if tag not in recorder.ownership:
            recorder.ownership = (*recorder.ownership, tag)

    def _target_id(self, target: str, *, window: bool) -> str | None:
        """Stable tmux id (``@N`` window / ``$N`` session) for a target ref."""

        fmt = "#{window_id}" if window else "#{session_id}"
        result = self._run(["display-message", "-p", "-t", target, fmt])
        if result.returncode != 0:
            return None
        try:
            value = (result.stdout or "").strip()
            return (
                validate_tmux_window_id(value) if window else validate_tmux_session_id(value)
            )
        except ValueError:
            return None

    def _record_termination(
        self,
        groups: Mapping[int, PaneProcessScope],
        outcome: TerminationResult,
    ) -> None:
        """Record each pane leader's scope and the signals it actually took.

        Per-PID attribution matters: a teardown that TERMed one pane and had to
        KILL another must not report both as KILL-only.
        """

        recorder = self._active_recorder()
        if recorder is None:
            return
        for leader in sorted(groups):
            scope = groups[leader]
            recorder.pane_scopes = (
                *[entry for entry in recorder.pane_scopes if entry.pane_pid != leader],
                PaneScopeEvidence(
                    pane_pid=leader,
                    pgid=scope.pgid,
                    cgroup=scope.cgroup,
                    term_sent=(leader,) if leader in outcome.term_sent else (),
                    kill_sent=(leader,) if leader in outcome.kill_sent else (),
                    remaining=(leader,) if leader in outcome.remaining else (),
                ),
            )
        self._record_signals(outcome)

    def _record_signals(self, *outcomes: TerminationResult) -> None:
        """Record the escalation actually used, in order (TERM -> KILL)."""

        recorder = self._active_recorder()
        if recorder is None:
            return
        signals = list(recorder.signals)
        for outcome in outcomes:
            term_sent = tuple(getattr(outcome, "term_sent", ()) or ())
            kill_sent = tuple(getattr(outcome, "kill_sent", ()) or ())
            if term_sent and "term" not in signals:
                signals.append("term")
            if kill_sent and "kill" not in signals:
                signals.append("kill")
        recorder.signals = tuple(signals)

    def _record_reclaim(self, victims: Iterable[int], outcome: TerminationResult) -> None:
        """Record an escaped-process sweep and the PIDs it actually reclaimed."""

        recorder = self._active_recorder()
        if recorder is None:
            return
        found = tuple(int(pid) for pid in victims)
        recorder.reclaimed = tuple(
            dict.fromkeys((*recorder.reclaimed, *found))
        )
        recorder.reclaim_term_sent = tuple(
            dict.fromkeys((*recorder.reclaim_term_sent, *outcome.term_sent))
        )
        recorder.reclaim_kill_sent = tuple(
            dict.fromkeys((*recorder.reclaim_kill_sent, *outcome.kill_sent))
        )
        self._record_signals(outcome)

    def _record_remaining(self, remaining: tuple[int, ...], *, reason: str) -> None:
        """Record PIDs that survived, keeping the first reason offered."""

        recorder = self._active_recorder()
        if recorder is None or not remaining:
            return
        recorder.remaining = tuple(
            dict.fromkeys((*recorder.remaining, *tuple(int(pid) for pid in remaining)))
        )
        recorder.remaining_reason = recorder.remaining_reason or reason


    def _escaped_process_ids(
        self,
        ownership: TmuxOwnership | None,
        roles: tuple[str, ...] | None,
        pane_groups: dict[int, PaneProcessScope],
        *,
        protected: frozenset[int] | None = None,
    ) -> tuple[int, ...]:
        """Candidate PIDs; ownership must be revalidated under a pidfd.

        Two independent proofs are accepted and nothing else is ever touched:

        * the ownership tags exported into every pane environment of the
          runtime (they survive reparenting, ``setsid`` and re-exec), and
          optionally one of ``roles`` (``None`` accepts every role, which is
          the right scope for a whole-session teardown), and
        * a pane leader's process group **and its exact captured cgroup**,
          which also catches a process spawned before the tags existed as long
          as it stayed in the pane it was started from.

        ``protected`` carries the PIDs the caller proved must never be
        signalled — pre-eminently the tmux server hosting the target and every
        pane on it outside the target, whose environment also carries the tags
        (Issue #1105).
        """

        found: list[int] = []
        if ownership is not None:
            base_tags = {
                OWNERSHIP_RUNTIME_ID_ENV: ownership.runtime_id,
                OWNERSHIP_GENERATION_ENV: str(ownership.generation),
            }
            for role in (roles if roles is not None else (None,)):
                tags = dict(base_tags)
                if role is not None:
                    tags[OWNERSHIP_ROLE_ENV] = role
                found.extend(processes_with_env(tags))
        if pane_groups and not any(is_running(pid) for pid in pane_groups):
            # Numeric PGIDs can be reused by other tmux panes. Only a process
            # in the original pane's exact captured scope is attributable.
            found.extend(
                processes_in_pane_scopes(set(pane_groups.values()))
            )
        guarded = set(ancestor_pids())
        if protected is not None:
            if isinstance(protected, ProtectedTeardownPids):
                # Re-derive the dynamic descendants now (children forked and
                # panes created after the snapshot) so a candidate that merely
                # inherited the tags never becomes a victim.
                guarded.update(protected.expanded())
            else:
                guarded.update(protected)
        return tuple(
            pid for pid in dict.fromkeys(found) if pid > 1 and pid not in guarded
        )

    def _escaped_process_is_owned(
        self,
        pid: int,
        ownership: TmuxOwnership | None,
        roles: tuple[str, ...] | None,
        pane_groups: dict[int, PaneProcessScope],
        protected: frozenset[int] | None = None,
    ) -> bool:
        """Fresh proof read only after the caller has opened this PID's pidfd."""

        if pid <= 1 or pid in (protected or ()) or pid in ancestor_pids():
            return False
        if ownership is not None:
            env = process_environ(pid)
            if (
                env.get(OWNERSHIP_RUNTIME_ID_ENV) == ownership.runtime_id
                and env.get(OWNERSHIP_GENERATION_ENV) == str(ownership.generation)
                and (roles is None or env.get(OWNERSHIP_ROLE_ENV) in roles)
            ):
                return True
        return bool(
            pane_groups
            and not any(is_running(leader) for leader in pane_groups)
            and (process_pgid(pid), process_cgroup(pid))
            in {(scope.pgid, scope.cgroup) for scope in pane_groups.values()}
        )

    def _reap_escaped_processes(
        self,
        ownership: TmuxOwnership | None,
        roles: tuple[str, ...] | None,
        pane_groups: dict[int, PaneProcessScope],
        *,
        operation: str,
        protected: frozenset[int] | None = None,
    ) -> tuple[int, ...]:
        """Stop processes that escaped the pane descendant tree (Issue #1105).

        An orphan that survived the pane sweep keeps burning CPU after the
        switch reports success, so every teardown ends with this
        post-condition: reclaim what is provably owned, and fail closed when
        something owned cannot be stopped.  The tmux server hosting the target
        is never reclaimed even though it carries the ownership tags.
        """

        victims = self._escaped_process_ids(
            ownership, roles, pane_groups, protected=protected
        )
        if not victims:
            return ()
        if len(victims) > MAX_ORPHAN_SWEEP_PROCESSES:
            raise TmuxError(
                f"{operation}: 回収対象の孤立プロセスが上限"
                f"{MAX_ORPHAN_SWEEP_PROCESSES}件を超えました: {victims}"
            )
        logger.warning(
            "tmux %s: 回収対象の孤立プロセスを停止します victims=%s", operation, victims
        )
        try:
            outcome = terminate_owned_processes(
                victims,
                lambda pid: self._escaped_process_is_owned(
                    pid, ownership, roles, pane_groups, protected
                ),
            )
        except ProcessIdentityError as exc:
            raise TmuxError(f"{operation}: 孤立プロセスの安全な停止に失敗しました: {exc}") from exc
        self._record_reclaim(victims, outcome)
        return outcome.remaining

    def _kill_after_process_stop(self, args: list[str], operation: str) -> None:
        """Finish cleanup while accepting only a target that already vanished.

        Stopping the final pane leader can make tmux remove its window/session
        before the explicit kill runs.  That is a successful cleanup race, not
        an error.  Transport/permission failures still fail closed.
        """

        result = self._run(args)
        if result.returncode == 0:
            return
        detail = (result.stderr or "").replace("\n", " ").strip()
        if any(marker in detail.lower() for marker in _STRICT_MISSING_MARKERS):
            return
        suffix = f": {detail[:200]}" if detail else ""
        raise TmuxError(f"tmux {operation} に失敗しました{suffix}")

    def stop_game_session_named(self, session: str) -> None:
        """Stop the legacy game-only session, never a shared runtime session."""

        generation_suffix = session.removeprefix("docich-game-g")
        if session != "docich-game" and not generation_suffix.isdigit():
            raise ValueError("legacy game cleanup only accepts docich-game")
        remaining, pane_groups, protected = self._stop_scoped_processes(session)
        self._run(["kill-session", "-t", session])
        # Legacy sessions carry no runtime tags, so only the pane process
        # group / cgroup scope evidence is available on this path.
        remaining = self._merge_remaining(
            remaining,
            self._reap_escaped_processes(
                None,
                None,
                pane_groups,
                operation="legacy session停止",
                protected=protected,
            ),
        )
        if remaining:
            self._record_remaining(remaining, reason="teardown後も停止を確認できなかった")
            raise TmuxError(f"legacy game sessionの子プロセスが停止しませんでした: {remaining}")

    @staticmethod
    def _merge_remaining(*groups: tuple[int, ...]) -> tuple[int, ...]:
        merged: list[int] = []
        for group in groups:
            merged.extend(group)
        return tuple(dict.fromkeys(merged))

    def kill_window_owned(self, target: str, expected: TmuxOwnership) -> bool:
        validate_tmux_window_ref(target)
        if not self.window_target_exists(target):
            return False
        actual = self.read_window_ownership(target)
        if actual != expected:
            raise OwnershipMismatchError(
                f"window ownershipが一致しません (expected={expected}, actual={actual})"
            )
        # Record the target identity and the ownership tags before anything is
        # signalled: this is the receipt for which runtime was torn down.
        self._record_target(target, expected, window=True)
        remaining, pane_groups, protected = self._stop_scoped_processes(target)
        self._kill_after_process_stop(["kill-window", "-t", target], "window停止")
        # Only this window's role is reclaimed: a sibling window of the same
        # runtime (game vs agent) must keep running.
        remaining = self._merge_remaining(
            remaining,
            self._reap_escaped_processes(
                expected,
                (expected.role,),
                pane_groups,
                operation="window停止",
                protected=protected,
            ),
        )
        if remaining:
            self._record_remaining(remaining, reason="teardown後も停止を確認できなかった")
            raise TmuxError(f"tmux windowの子プロセスが停止しませんでした: {remaining}")
        return True

    def kill_session_owned(self, session: str, expected: TmuxOwnership) -> bool:
        validate_tmux_session_ref(session)
        if not self.session_target_exists(session):
            return False
        actual = self.read_session_ownership(session)
        if actual != expected:
            raise OwnershipMismatchError(
                f"session ownershipが一致しません (expected={expected}, actual={actual})"
            )
        self._record_target(session, expected, window=False)
        remaining, pane_groups, protected = self._stop_scoped_processes(session)
        self._kill_after_process_stop(["kill-session", "-t", session], "session停止")
        # The session owns every role of the runtime (its own pane and the
        # generation windows), so no role filter applies here.
        remaining = self._merge_remaining(
            remaining,
            self._reap_escaped_processes(
                expected,
                None,
                pane_groups,
                operation="session停止",
                protected=protected,
            ),
        )
        if remaining:
            self._record_remaining(remaining, reason="teardown後も停止を確認できなかった")
            raise TmuxError(f"tmux sessionの子プロセスが停止しませんでした: {remaining}")
        return True

    def capture_pane(self, session: str) -> str:
        r = self._run(["capture-pane", "-p", "-t", session])
        return r.stdout if r.returncode == 0 else ""

    def capture_pane_colored(self, session: str) -> str:
        """Capture visible text with cell colors and trailing spaces preserved."""
        r = self._run(["capture-pane", "-e", "-N", "-p", "-t", session])
        return r.stdout if r.returncode == 0 else ""

    def capture_pane_checked(self, target: str) -> str:
        validate_tmux_target(target)
        return self._checked(["capture-pane", "-p", "-t", target], "pane capture").stdout

    def capture_pane_bounded_checked(
        self,
        target: str,
        *,
        cols: int,
        rows: int,
        max_bytes: int = 65_536,
        timeout_s: float = 1.0,
    ) -> str:
        """Capture only the visible, expected-sized pane under a hard byte cap."""
        validate_tmux_target(target)
        if type(cols) is not int or not 1 <= cols <= 80:
            raise ValueError("cols must be between 1 and 80")
        if type(rows) is not int or not 3 <= rows <= 24:
            raise ValueError("rows must be between 3 and 24")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 65_536:
            raise ValueError("max_bytes must be between 1 and 65536")
        if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 5:
            raise ValueError("timeout_s must be greater than 0 and at most 5 seconds")

        def pane_size() -> tuple[int, int]:
            output = self._run_bounded(
                [
                    "display-message",
                    "-p",
                    "-t",
                    target,
                    "#{pane_width}\t#{pane_height}",
                ],
                operation="pane size check",
                max_output_bytes=64,
                timeout_s=float(timeout_s),
            )
            parts = output.decode("ascii", errors="replace").strip().split("\t")
            if len(parts) != 2:
                raise TmuxError("tmux pane size response is invalid")
            try:
                return int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise TmuxError("tmux pane size response is invalid") from exc

        if pane_size() != (cols, rows):
            raise TmuxError("tmux pane size does not match the NetHack TTY")

        try:
            result = procs.run_bounded_output(
                [
                    "tmux",
                    "capture-pane",
                    "-p",
                    "-t",
                    target,
                ],
                max_output_bytes=max_bytes,
                timeout=float(timeout_s),
                strip_tmux=True,
            )
        except (procs.OutputLimitExceeded, subprocess.TimeoutExpired) as exc:
            raise TmuxError("tmux pane capture exceeded its resource bound") from exc
        if result.returncode != 0:
            raise TmuxError("tmux pane capture failed")
        if pane_size() != (cols, rows):
            raise TmuxError("tmux pane size changed during the NetHack TTY capture")
        return result.stdout.decode("utf-8", errors="replace")

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
