"""CLI/TUI game adapters.

Legacy :class:`CliGameAdapter` keeps the fixed ``docich-game`` session for the
current CLI lifecycle.  :class:`CliCoordinatorAdapter` is the P2 runtime-aware
adapter: one generation-specific session/window set per runtime, tagged with
tmux ownership options (design v2 §3, §4).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

from .. import procs
from ..actions import Action
from ..game_switch import (
    DeadlineExceededError,
    ReadinessTimeoutError,
    RuntimeSpec,
    atomic_write_json,
)
from ..naming import NameValidationError, validate_tmux_name
from ..tmux import OwnershipMismatchError, Tmux, TmuxOwnership
from ..xkit import XKit
from ..resolver.lease import activity_lock
from .base import Adapter, AdapterError, Observation

GAME_SESSION = "docich-game"
RUNTIME_GAME_SESSION_ENV = "DOCICH_GAME_SESSION"
PRESENTATION_SEARCH_TIMEOUT_S = 0.25
PRESENTATION_POLL_INTERVAL_S = 0.1

# --- round boundary (design v2 §5) ------------------------------------------
# CLIゲーム (robots) の1試合終了は "Another game?" プロンプトの表示のみで
# 判定する (大文字小文字を無視した部分一致)。"Really quit?" は誰かが q を
# 押して停止確認に入っただけの状態であり境界ではない。プロンプトへ答える
# 入力は送らず、観測のみで待ち続け、deadline超過ならfail-closedでtimeout
# にする。
ROUND_BOUNDARY_POLL_INTERVAL_S = 0.5
ROUND_BOUNDARY_PROMPT = "another game?"
ROUND_BOUNDARY_SCORE_RE = re.compile(r"score:\s*([0-9,]+)", re.IGNORECASE)
ROUND_BOUNDARY_TAIL_LINES = 15
ROUND_BOUNDARY_RESULT_FILENAME = "round_boundary_result.json"


# --- shared [cli] table helpers --------------------------------------------


def cli_raw(game) -> dict:
    raw = game.raw.get("cli", {})
    if not isinstance(raw, dict):
        raise AdapterError("[cli] はテーブルである必要があります")
    return raw


def cli_command_list(game) -> list[str]:
    command = cli_raw(game).get("command")
    if isinstance(command, str) and command.strip():
        return shlex.split(command)
    if isinstance(command, list) and command:
        return [str(c) for c in command]
    raise AdapterError("[cli] に command が設定されていません")


def cli_cols(game) -> int:
    return int(cli_raw(game).get("cols", 80))


def cli_rows(game) -> int:
    return int(cli_raw(game).get("rows", 24))


def cli_font(game) -> str:
    return str(cli_raw(game).get("font", "monospace"))


def cli_font_size(game) -> int:
    return int(cli_raw(game).get("font_size", 18))


def cli_game_session() -> str:
    """Return the CLI session bound to this process.

    Legacy processes use ``docich-game``.  A generation-scoped coordinator
    agent receives a validated session name through ``DOCICH_GAME_SESSION`` so
    the unchanged agent loop observes/acts on its own runtime instead of the
    legacy fixed session.
    """

    value = os.environ.get(RUNTIME_GAME_SESSION_ENV, GAME_SESSION)
    try:
        return validate_tmux_name(value)
    except NameValidationError as exc:
        raise AdapterError(f"{RUNTIME_GAME_SESSION_ENV} が不正です") from exc


def _docich_bin() -> str:
    # cli.py の _docich_bin() と同じ repo root を指す。cli_game.py は
    # src/docich/adapters/ 配下なので cli.py より1階層深い。
    return str(Path(__file__).resolve().parents[3] / "bin" / "docich")


def _round_end_prompt_line(text: str) -> str | None:
    """Return the round-end prompt line if the captured pane shows one.

    robots の1試合終了 ("Another game?" プロンプト) のみを境界とする。
    大文字小文字は無視する。見つからなければ None ("Really quit?" も
    境界ではないので None を返す)。
    """

    for line in text.splitlines():
        if ROUND_BOUNDARY_PROMPT in line.lower():
            return line.strip()
    return None


def _max_score_value(text: str) -> int | None:
    """Parse the largest ``Score:`` value in the captured pane text.

    robots は左上に実行中スコアを常時表示し、試合終了時に最終スコアを出す
    ため、最大値を最終スコアとして扱う。カンマは桁区切りとして除く。
    """

    best: int | None = None
    for line in text.splitlines():
        match = ROUND_BOUNDARY_SCORE_RE.search(line)
        if match is None:
            continue
        value = int(match.group(1).replace(",", ""))
        if best is None or value > best:
            best = value
    return best


class CliGameAdapter(Adapter):
    name = "cli"

    def _session(self) -> str:
        return cli_game_session()

    def _command_list(self) -> list[str]:
        return cli_command_list(self.ctx.game)

    def _cols(self) -> int:
        return cli_cols(self.ctx.game)

    def _rows(self) -> int:
        return cli_rows(self.ctx.game)

    def _font(self) -> str:
        return cli_font(self.ctx.game)

    def _font_size(self) -> int:
        return cli_font_size(self.ctx.game)

    # --- Adapter contract ------------------------------------------------

    def prepare(self) -> None:
        cmd = self._command_list()
        # tmux サーバーの PATH に /usr/games が無い場合に備えて絶対パス化する。
        resolved = procs.which(cmd[0])
        if not resolved:
            raise AdapterError(
                f"コマンドが見つかりません: {cmd[0]} (PATH を確認してください。/usr/games も探索します)"
            )
        resolved_cmd = [resolved, *cmd[1:]]
        session = self._session()

        if not self.ctx.tmux.has_session_named(session):
            self.ctx.tmux.new_game_session(session, resolved_cmd, self._cols(), self._rows())
            self.ctx.tmux.set_manual_size(session, self._cols(), self._rows())

    def command(self) -> list[str]:
        # 映像化用の xterm。ゲーム本体は session 内で走り続ける (read-only attach)。
        return [
            "xterm", "-fa", self._font(), "-fs", str(self._font_size()),
            "-bg", "black", "-fg", "grey90",
            "-geometry", f"{self._cols()}x{self._rows()}+0+0",
            "-T", f"docich-{self.ctx.game.name}",
            "-e", "tmux", "attach-session", "-r", "-t", self._session(),
        ]

    def observe(self) -> Observation:
        self._check_fence()
        text = self.ctx.tmux.capture_pane(self._session())
        meta = {}
        if text == "":
            meta["warning"] = "capture が空です (セッション停止の可能性)"
        return Observation(
            game=self.ctx.game.name,
            title=self.ctx.game.title,
            adapter=self.name,
            ts=time.time(),
            kind="text",
            text=text,
            meta=meta,
        )

    def act(self, action: Action) -> None:
        self._check_fence()
        session = self._session()
        if action.type == "text":
            self.ctx.tmux.send_keys(session, [action.text], literal=True)
            return
        if action.type == "special":
            self.ctx.tmux.send_keys(session, [action.key], literal=False)
            return
        if action.type == "key":
            self.ctx.tmux.send_keys(session, action.keys, literal=False)
            return
        if action.type == "wait":
            return
        raise AdapterError(f"cli アダプタは action type '{action.type}' に対応していません")

    def cleanup(self) -> None:
        self.ctx.tmux.kill_session_named(self._session())


class CliCoordinatorAdapter:
    """P2 runtime-aware CLI adapter (design v2 §4.2).

    One instance is bound to exactly one runtime (``RuntimeSpec``).  It
    creates the generation-specific tmux session (``docich-game-gN``) and the
    game/agent windows (``game-gN`` / ``agent-gN``) with ownership tags, and
    only ever tears down those tagged objects.
    """

    name = "cli"

    def __init__(self, g, game, spec: RuntimeSpec):
        self.g = g
        self.game = game
        self.spec = spec
        # Bind to this runtime's own generation session: the Tmux() default
        # ("docich") only reaches docich-game-gN via tmux prefix matching,
        # which breaks the moment any other docich-* session exists.
        self.tmux = Tmux(self.spec.adapter_session)
        self.agent_enabled = game.agent.enabled
        self.requires_round_boundary = game.lifecycle.require_round_boundary
        self.round_boundary_timeout_s = game.lifecycle.boundary_timeout_s
        if not self.requires_round_boundary:
            # policy flagがfalseのCLIゲーム (nethack等) は境界待ちを要求しない。
            # coordinatorはcallableな request_round_boundary の存在だけでも
            # drainingへ入るため、capabilityごと非公開にして従来どおり即時
            # quiesceで切替えられるようにする (test_round_boundary.py の
            # request_round_boundary = None と同じ「未対応」表現)。
            self.request_round_boundary = None
            self.cancel_round_boundary = None

    def _ownership(self, role: str) -> TmuxOwnership:
        return TmuxOwnership(
            runtime_id=self.spec.runtime_id,
            generation=self.spec.generation,
            role=role,
        )

    def _check_active(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            raise DeadlineExceededError("adapter call はcancelされました")
        if time.monotonic() >= deadline:
            raise DeadlineExceededError("adapter call のdeadlineを超過しました")

    def _game_window_target(self) -> str:
        return f"{self.spec.adapter_session}:{self.spec.game_window}"

    def _agent_window_target(self) -> str:
        return f"{self.spec.adapter_session}:{self.spec.agent_window}"

    def _verify_session_ownership(self) -> None:
        expected = self._ownership("adapter")
        try:
            actual = self.tmux.read_session_ownership(self.spec.adapter_session)
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("session ownership tagが不正です") from exc
        if actual != expected:
            raise OwnershipMismatchError(
                f"session ownershipが一致しません (expected={expected}, actual={actual})"
            )

    def _verify_window_ownership(self, target: str, role: str) -> None:
        expected = self._ownership(role)
        try:
            actual = self.tmux.read_window_ownership(target)
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("window ownership tagが不正です") from exc
        if actual != expected:
            raise OwnershipMismatchError(
                f"window ownershipが一致しません (expected={expected}, actual={actual})"
            )

    def _game_command(self) -> list[str]:
        cmd = cli_command_list(self.game)
        resolved = procs.which(cmd[0])
        if not resolved:
            raise AdapterError(
                f"コマンドが見つかりません: {cmd[0]} (PATH を確認してください。/usr/games も探索します)"
            )
        return [resolved, *cmd[1:]]

    def _xterm_bin(self) -> str:
        resolved = procs.which("xterm")
        if not resolved:
            raise AdapterError("xterm が見つかりません (PATH を確認してください)")
        return resolved

    def _xterm_command(self) -> list[str]:
        command = [
            self._xterm_bin(), "-fa", cli_font(self.game), "-fs", str(cli_font_size(self.game)),
            "-bg", "black", "-fg", "grey90",
            "-geometry", f"{cli_cols(self.game)}x{cli_rows(self.game)}+0+0",
            "-T", f"docich-{self.game.name}",
            "-e", "tmux", "attach-session", "-r", "-t", self.spec.adapter_session,
        ]
        d = self.g.display
        if d.viewport_width > 0 and d.viewport_height > 0:
            return [
                sys.executable, str(Path(__file__).resolve().parents[1] / 'presentation.py'),
                '--display', d.name, '--title', f'docich-present-{self.spec.runtime_id}',
                '--x', str(d.viewport_x), '--y', str(d.viewport_y),
                '--width', str(d.viewport_width), '--height', str(d.viewport_height),
                '--', *command,
            ]
        return command

    def _agent_command(self) -> list[str]:
        # Agent は世代別 window 内で run ループとして起動し、runtime identity
        # を束縛する (P3 lease fence)。lease 未発行の rare なspec では束縛なし。
        cmd = [
            _docich_bin(), "--config", str(self.g.config_path),
            "run", "agent", self.spec.game,
        ]
        if (
            self.spec.runtime_id is not None
            and self.spec.generation is not None
            and self.spec.lease_id is not None
        ):
            cmd += [
                "--runtime-id", str(self.spec.runtime_id),
                "--generation", str(self.spec.generation),
                "--lease-id", str(self.spec.lease_id),
            ]
        return cmd

    # --- CoordinatorAdapter contract -------------------------------------

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        self._game_command()
        self._xterm_bin()
        if self.g.display.viewport_width > 0:
            for binary in ('Xvfb', 'ffplay', 'xdotool'):
                if not procs.which(binary):
                    raise AdapterError(f'{binary} が見つかりません')
        if self.agent_enabled and not Path(_docich_bin()).is_file():
            raise AdapterError(f"docich executable が見つかりません: {_docich_bin()}")
        self._check_active(deadline, cancel)

    def materialize_runtime(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._check_active(deadline, cancel)

        if self.tmux.session_target_exists(self.spec.adapter_session):
            self._verify_session_ownership()
        else:
            self._check_active(deadline, cancel)
            self.tmux.create_game_session_owned(
                self.spec.adapter_session,
                self._game_command(),
                cli_cols(self.game),
                cli_rows(self.game),
                self._ownership("adapter"),
            )
        self._check_active(deadline, cancel)

        game_target = self._game_window_target()
        if self.tmux.window_target_exists(game_target):
            self._verify_window_ownership(game_target, "game")
        else:
            self._check_active(deadline, cancel)
            self.tmux.create_window_owned(
                self.spec.game_window,
                self._xterm_command(),
                self._ownership("game"),
                env={"DISPLAY": self.g.display.name},
            )
        self._check_active(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            raise ReadinessTimeoutError("adapter sessionがありません")
        self._verify_session_ownership()

        game_target = self._game_window_target()
        if not self.tmux.window_target_exists(game_target):
            raise ReadinessTimeoutError("game windowがありません")
        self._verify_window_ownership(game_target, "game")

        states = self.tmux.pane_states_checked(self.spec.adapter_session)
        if any(pane.dead for pane in states):
            raise ReadinessTimeoutError("paneがdeadです")
        # capture-pane 自体が成功すること (内容が空でも即失敗にしない)
        self.tmux.capture_pane_checked(self.spec.adapter_session)
        d = self.g.display
        if d.viewport_width > 0 and d.viewport_height > 0:
            # The presenter is a child process inside the owned tmux game
            # window.  Probe in short slices so a wrapper that exits early is
            # noticed promptly instead of leaving xdotool blocked until the
            # whole request deadline.  Recheck the pane on every slice; a
            # live adapter session alone is not proof that its viewer exists.
            presenter = XKit(d.name)
            pattern = f"^docich-present-{self.spec.runtime_id}$"
            while True:
                self._check_active(deadline, cancel)
                if not self.tmux.session_target_exists(self.spec.adapter_session):
                    raise ReadinessTimeoutError("adapter sessionがありません")
                self._verify_session_ownership()
                states = self.tmux.pane_states_checked(self.spec.adapter_session)
                if any(pane.dead for pane in states):
                    raise ReadinessTimeoutError("paneがdeadです")

                if not self.tmux.window_target_exists(game_target):
                    raise ReadinessTimeoutError("game windowがありません")
                self._verify_window_ownership(game_target, "game")
                game_states = self.tmux.pane_states_checked(game_target)
                if any(pane.dead for pane in game_states):
                    raise ReadinessTimeoutError("game window paneがdeadです")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ReadinessTimeoutError("CLI game windowの準備がタイムアウトしました")
                window_id = presenter.find_window(
                    pattern,
                    timeout=min(PRESENTATION_SEARCH_TIMEOUT_S, remaining),
                )
                if window_id is not None:
                    break
                if cancel is not None and cancel.is_set():
                    raise DeadlineExceededError("adapter call はcancelされました")
                if time.monotonic() >= deadline:
                    raise ReadinessTimeoutError("CLI game windowの準備がタイムアウトしました")
                time.sleep(min(PRESENTATION_POLL_INTERVAL_S, deadline - time.monotonic()))
        self._check_active(deadline, cancel)

    def alive(self, deadline: float, cancel) -> bool:
        self._check_active(deadline, cancel)
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            return False
        self._verify_session_ownership()
        return True

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        marker = Path(self.g.state_dir) / "resolver" / "active" / f"{self.game.name}.json"
        while marker.exists():
            self._check_active(deadline, cancel)
            with activity_lock(Path(self.g.state_dir),self.game.name):
                if not marker.exists(): break
                try:
                    data=json.loads(marker.read_text(encoding="utf-8"));pid=int(data["pid"])
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    raise AdapterError("resolver改善の所有情報が不正です") from exc
                if data.get("game") != self.game.name or data.get("generation") != self.spec.generation or data.get("lease_id") != self.spec.lease_id:
                    raise AdapterError("resolver改善の所有情報がruntimeと一致しません")
                cmdline=Path(f"/proc/{pid}/cmdline");sessions=data.get("sessions",[])
                if not isinstance(sessions,list) or any(not isinstance(s,str) or not s.startswith(f"evalr-{pid}-") for s in sessions):
                    raise AdapterError("resolver改善sessionの所有情報が不正です")
                for session in sessions:
                    if self.tmux.session_target_exists(session,strict=True) and not cmdline.exists():
                        raise AdapterError("resolver改善daemon消滅後も評価sessionが残っています")
                if not cmdline.exists(): marker.unlink();break
                raw=cmdline.read_bytes().replace(b"\0",b" ").decode("utf-8",errors="replace")
                if "docich.resolver.improve" not in raw or self.game.name not in raw:
                    raise AdapterError("resolver改善PIDの所有権を確認できません")
            time.sleep(min(0.1,max(0.0,deadline-time.monotonic())))
        for name, role in (
            (self.spec.agent_window, "agent"),
            (self.spec.game_window, "game"),
        ):
            self._check_active(deadline, cancel)
            target = f"{self.spec.adapter_session}:{name}"
            if self.tmux.window_target_exists(target):
                self._check_active(deadline, cancel)
                self.tmux.kill_window_owned(target, self._ownership(role))
        self._check_active(deadline, cancel)
        if self.tmux.session_target_exists(self.spec.adapter_session):
            self._check_active(deadline, cancel)
            self.tmux.kill_session_owned(self.spec.adapter_session, self._ownership("adapter"))

    def start_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._verify_window_ownership(target, "agent")
            return
        self._check_active(deadline, cancel)
        self.tmux.create_window_owned(
            self.spec.agent_window,
            self._agent_command(),
            self._ownership("agent"),
            env={RUNTIME_GAME_SESSION_ENV: self.spec.adapter_session},
        )
        self._check_active(deadline, cancel)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._check_active(deadline, cancel)
            self.tmux.kill_window_owned(target, self._ownership("agent"))

    # --- CoordinatorAdapter contract: round boundary (design v2 §5) -------

    def _round_boundary_result_path(self) -> Path:
        return self.spec.runtime_dir / ROUND_BOUNDARY_RESULT_FILENAME

    def _write_round_boundary_result(
        self,
        request_id: str,
        prompt_line: str,
        score: int | None,
        text: str,
    ) -> None:
        """Persist the boundary result atomically, before the ack (fail closed).

        request_id はackする境界要求のものだけを記録する (pending状態の
        ファイルは作らない)。書き込みに失敗した場合はackせず AdapterError
        にして、coordinator へ runtime の維持を委ねる。
        """

        payload = {
            "schema": 1,
            "request_id": request_id,
            "game": self.spec.game,
            "generation": self.spec.generation,
            "prompt": prompt_line,
            "score": score,
            "captured_pane_tail": text.splitlines()[-ROUND_BOUNDARY_TAIL_LINES:],
            "detected_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        path = self._round_boundary_result_path()
        try:
            atomic_write_json(path, payload)
        except OSError as exc:
            raise AdapterError(f"round boundary結果を書き込めませんでした: {path}") from exc

    def request_round_boundary(self, request_id: str, deadline: float, cancel) -> None:
        """Wait for the current one-game boundary ("Another game?" prompt).

        待機中は観測のみ: 入力を送らず、session/window も停止しない。
        操作AIは境界待機と独立にプレイを続けてよい (fenceは共有ロックで
        保護するため、ここでは一切ロックへ触れない)。

        判定は pane capture のみで行う。"Really quit?" は境界ではないため、
        それしか表示されていない間は待ち続け、deadline超過でfail-closedに
        なる (この経路で session を停止することはない)。

        境界を検出したらスコアを pane から解析し (実行中スコアと最終スコア
        が併存するため最大値)、結果JSONを runtime dir へ原子的に書いてから
        ackする。スコア行が無い場合も prompt が境界の根拠なので score=null
        でackする。書き込みに失敗した場合はackしない。

        deadline超過は ReadinessTimeoutError、cancelは DeadlineExceededError
        (readiness() と同じidiom) を上げる。
        """

        while True:
            # deadline超過は _check_active の DeadlineExceededError ではなく
            # 境界固有のtimeoutへ正規化する。sleepがdeadlineぴったりで終わり
            # 得るため、cancel以外のdeadline判定を先に行う。
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("CLI gameの試合終了境界を確認できませんでした (timeout)")
            self._check_active(deadline, cancel)
            if not self.tmux.session_target_exists(self.spec.adapter_session):
                raise ReadinessTimeoutError("adapter sessionがありません")
            self._verify_session_ownership()
            states = self.tmux.pane_states_checked(self.spec.adapter_session)
            if any(pane.dead for pane in states):
                raise ReadinessTimeoutError("paneがdeadです")
            text = self.tmux.capture_pane_checked(self.spec.adapter_session)
            prompt_line = _round_end_prompt_line(text)
            if prompt_line is not None:
                # スコア保存をackより先に行う (fail closed)。
                self._write_round_boundary_result(
                    request_id, prompt_line, _max_score_value(text), text
                )
                return
            time.sleep(min(ROUND_BOUNDARY_POLL_INTERVAL_S, deadline - time.monotonic()))

    def cancel_round_boundary(self, request_id: str, deadline: float, cancel) -> bool:
        """Cancel a pending boundary request without side effects (quick ack).

        待機は観測のみなので取り消す資源は無い。session があれば ownership
        を照合し、無ければ cleanup と同様に不在を成功として扱い即ackする。
        request_id は状態を保持しない本実装では参照しない (契約上の引数)。
        """

        self._check_active(deadline, cancel)
        if self.tmux.session_target_exists(self.spec.adapter_session):
            self._verify_session_ownership()
        return True
