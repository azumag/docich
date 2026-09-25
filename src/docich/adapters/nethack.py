"""NetHack-specific coordinator behavior for long-running program sessions.

The legacy/agent observation contract stays the normal CLI adapter: the game
still runs in the generation-specific tmux session and ``docich obs/send`` see
exactly the same text terminal.  This coordinator specialization adds only the
lifecycle semantics needed by the NetHack corner:

* always launch the game with one stable ``-u`` player name so normal NetHack
  save files can be restored by the next runtime;
* before any coordinator switch/stop, use NetHack's normal ``S`` command and
  wait for both the game process window to exit and a newly-created/changed
  save file for that player to exist;
* fail closed if durable suspension cannot be verified.  The coordinator then
  keeps/rolls back the runtime rather than silently destroying an adventure.

This is normal NetHack save/restore, not explore-mode save scumming.  A normal
restore consumes the previous save file; the next program boundary creates a
new one.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, atomic_write_json
from ..nethack_tiles_supervisor import (
    PRESENTATION_WINDOW_WAIT_S,
    browser_binary,
    presentation_window_pattern,
)
from ..xkit import XKit
from .base import AdapterError
from .cli_game import (
    CliCoordinatorAdapter,
    cli_cols,
    cli_font,
    cli_font_size,
    cli_rows,
)

DEFAULT_PLAYER_NAME = "docich"
DEFAULT_SAVE_DIR = Path("/var/games/nethack/save")
BOUNDARY_RESULT_FILENAME = "nethack_boundary.json"
BOUNDARY_DIAG_FILENAME = "nethack_boundary_diag.json"
# #1015: bounded, sanitized observation for a refused cancel / timed-out save
# boundary. Fixed enums only -- never raw pane text, paths, tokens or argv.
CANCEL_REFUSAL_REASONS = frozenset(
    {
        "deadline_exceeded",
        "cancel_requested",
        "session_missing",
        "session_unowned",
        "process_target_absent",
        "process_window_ambiguous",
        "process_window_probe_failed",
        "capture_failed",
        "prompt_not_pending",
        "process_gone",
        "post_key_probe_failed",
        "post_key_capture_failed",
        "wait_timeout",
    }
)
PROMPT_CLASSES = frozenset(
    {
        "save_prompt_pending",
        "save_confirmation",
        "character_creation",
        "capture_failed",
        "unknown",
    }
)
# The outcome already recorded in ``nethack_boundary.json`` for this runtime.
# Needed because "the birth window is gone" is only safe to act on together
# with reviewed boundary evidence (#1015).
BOUNDARY_OUTCOMES = frozenset({"suspended", "ended", "unknown"})
TILES_MANIFEST_STATUSES = frozenset(
    {
        "starting",
        "tiles_active",
        "fallback_starting",
        "fallback_tty",
        "failed",
        "stopped",
        "cleanup_failed",
    }
)
_PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,31}$")
_SAVE_CONFIRMATION_RE = re.compile(r"really\s+save\?\s*\[yn\]", re.IGNORECASE)
# The confirmation while it is still waiting for an answer: NetHack shows the
# default ``(n)`` and nothing after it.  Once an answer has been typed the line
# ends with that key, which is no longer something we may take back.
_SAVE_PROMPT_PENDING_RE = re.compile(
    r"^\s*really\s+save\?\s*\[yn\]\s*\(n\)\s*$", re.IGNORECASE | re.MULTILINE
)

# Character-creation prompts have no durable run to save: NetHack has not
# created an adventure yet.  The normal ``S`` boundary only succeeds after a
# fresh save file appears, which can never happen here, so a switch away from a
# game that never reached gameplay would otherwise wait until the deadline,
# fail closed, and leave the canonical active game stuck on NetHack.
#
# Do not include post-creation banners such as "Welcome to NetHack!": that
# message can remain visible after the map/status line exists, at which point
# an adventure has started and must use the normal durable save boundary.
_PREGAME_SCREEN_MARKERS = (
    "do you want a tutorial",
    "shall i pick",
    "pick a character",
    "pick a role",
    "pick a race",
    "pick an alignment",
    "pick a gender",
    "is this ok",
)


def _is_character_creation_screen(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _PREGAME_SCREEN_MARKERS)


def _is_save_confirmation_screen(text: str) -> bool:
    return _SAVE_CONFIRMATION_RE.search(text) is not None


def _is_save_prompt_pending(text: str) -> bool:
    return _SAVE_PROMPT_PENDING_RE.search(text) is not None


class NethackCoordinatorAdapter(CliCoordinatorAdapter):
    """CLI coordinator adapter with NetHack's suspend/resume boundary."""

    def __init__(self, g, game, spec):
        # NetHack's program boundary is a safe save, even though legacy
        # observation/action remains the normal CLI adapter.
        lifecycle = replace(game.lifecycle, require_round_boundary=True)
        super().__init__(g, replace(game, lifecycle=lifecycle), spec)
        self.player_name, self.save_dir = self._load_nethack_settings(game)
        self.presentation_mode = self._load_presentation_mode(game)

    @staticmethod
    def _load_presentation_mode(game) -> str:
        raw = game.raw.get("nethack", {}) if isinstance(game.raw, dict) else {}
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise AdapterError("[nethack] はtableである必要があります")
        presentation = raw.get("presentation", {})
        if presentation is None:
            presentation = {}
        if not isinstance(presentation, dict):
            raise AdapterError("[nethack.presentation] はtableである必要があります")
        mode = presentation.get("mode", "tty")
        if not isinstance(mode, str) or mode not in {"tty", "tiles"}:
            raise AdapterError("nethack.presentation.mode は tty または tiles を指定してください")
        return mode

    def _tiles_manifest_path(self) -> Path:
        return self.spec.runtime_dir / "nethack_tiles.json"

    def _presentation_path(self) -> Path:
        return self.spec.runtime_dir / "presentation.json"

    def _tiles_manifest(self) -> dict:
        path = self._tiles_manifest_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise AdapterError("NetHack tiles manifestを読めません") from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise AdapterError("NetHack tiles manifestが不正です") from exc
        if not isinstance(value, dict):
            raise AdapterError("NetHack tiles manifestがobjectではありません")
        return value

    def _validate_tiles_manifest(self, value: dict) -> None:
        if (
            type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
            or type(value.get("generation")) is not int
            or value.get("runtime_id") != self.spec.runtime_id
            or value.get("generation") != self.spec.generation
            or value.get("adapter_session") != self.spec.adapter_session
            or value.get("game_window") != self.spec.game_window
        ):
            raise AdapterError("NetHack tiles manifest のruntime所有権が一致しません")
        if (
            not isinstance(value.get("status"), str)
            or value.get("status") not in TILES_MANIFEST_STATUSES
        ):
            raise AdapterError("NetHack tiles manifest statusが不正です")
        if (
            not isinstance(value.get("mode"), str)
            or value.get("mode") not in {"tiles", "tty"}
        ):
            raise AdapterError("NetHack tiles manifest modeが不正です")

    @staticmethod
    def _load_nethack_settings(game) -> tuple[str, Path]:
        raw = game.raw.get("nethack", {}) if isinstance(game.raw, dict) else {}
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise AdapterError("[nethack] はtableである必要があります")
        player = raw.get("player_name", DEFAULT_PLAYER_NAME)
        if not isinstance(player, str) or _PLAYER_RE.fullmatch(player) is None:
            raise AdapterError(
                "nethack.player_name は1-31文字の英数字/underscoreで指定してください"
            )
        save_dir_raw = raw.get("save_dir", str(DEFAULT_SAVE_DIR))
        if not isinstance(save_dir_raw, str) or not save_dir_raw.strip():
            raise AdapterError("nethack.save_dir は絶対path文字列で指定してください")
        save_dir = Path(save_dir_raw)
        if not save_dir.is_absolute():
            raise AdapterError("nethack.save_dir は絶対pathで指定してください")
        return player, save_dir

    def _game_command(self) -> list[str]:
        command = super()._game_command()
        # A second player-name source makes resume identity ambiguous.  Keep
        # this contract in [nethack] and let -u override rc/env name options.
        if any(arg == "-u" or arg.startswith("-u") for arg in command[1:]):
            raise AdapterError("[cli].command に -u を含めず nethack.player_name を使用してください")
        if any(arg in {"-D", "-X"} for arg in command[1:]):
            raise AdapterError("NetHack corner はwizard/explore modeを使用できません")
        return [*command, "-u", self.player_name]

    def _xterm_command(self) -> list[str]:
        if self.presentation_mode != "tiles":
            return super()._xterm_command()
        display = self.g.display
        if display.viewport_width <= 0 or display.viewport_height <= 0:
            raise AdapterError("tiles presentation にはdisplay viewportが必要です")
        window_title = f"docich-present-{self.spec.runtime_id}"
        supervisor = Path(__file__).resolve().parents[1] / "nethack_tiles_supervisor.py"
        presentation = Path(__file__).resolve().parents[1] / "presentation.py"
        return [
            sys.executable,
            str(presentation),
            "--display", display.name,
            "--title", window_title,
            "--x", str(display.viewport_x),
            "--y", str(display.viewport_y),
            "--width", str(display.viewport_width),
            "--height", str(display.viewport_height),
            "--viewer-wait-sec", str(int(PRESENTATION_WINDOW_WAIT_S)),
            "--window-pattern", presentation_window_pattern(window_title),
            "--rebind-window",
            "--runtime-state", str(self._presentation_path()),
            "--",
            sys.executable,
            str(supervisor),
            "--state-dir", str(Path(self.g.state_dir).resolve()),
            "--runtime-dir", str(self.spec.runtime_dir),
            "--manifest", str(self._tiles_manifest_path()),
            "--presentation-state", str(self._presentation_path()),
            "--runtime-id", str(self.spec.runtime_id),
            "--generation", str(self.spec.generation),
            "--adapter-session", str(self.spec.adapter_session),
            "--game-window", str(self.spec.game_window),
            "--cols", str(cli_cols(self.game)),
            "--rows", str(cli_rows(self.game)),
            "--font", cli_font(self.game),
            "--font-size", str(cli_font_size(self.game)),
            "--window-title", window_title,
        ]

    def preflight(self, deadline: float, cancel) -> None:
        super().preflight(deadline, cancel)
        self._check_active(deadline, cancel)
        if self.presentation_mode == "tiles":
            if self.g.display.viewport_width <= 0 or self.g.display.viewport_height <= 0:
                raise AdapterError("tiles presentation にはdisplay viewportが必要です")
            if browser_binary() is None:
                raise AdapterError("chromium が見つかりません (NetHack tiles presentation)")
            supervisor = Path(__file__).resolve().parents[1] / "nethack_tiles_supervisor.py"
            if not supervisor.is_file():
                raise AdapterError("NetHack tiles supervisor が見つかりません")
            prior = self._tiles_manifest()
            if prior:
                self._validate_tiles_manifest(prior)
                if prior.get("status") != "stopped" or prior.get("cleanup_complete") is not True:
                    raise AdapterError("previous NetHack tiles child cleanupが未確認です")
        if not self.save_dir.is_dir():
            raise AdapterError(f"NetHack save directory がありません: {self.save_dir}")
        try:
            next(self.save_dir.iterdir(), None)
        except OSError as exc:
            raise AdapterError("NetHack save directory を検査できません") from exc
        self._check_active(deadline, cancel)

    def _runtime_process_window_target(self) -> str | None:
        """Resolve the birth window which owns the actual NetHack process.

        A healthy coordinator CLI runtime always has the named presentation
        window.  Therefore an empty/torn window listing is never interpreted
        as a real game end: that would turn a tmux probe failure into false
        success.  With the presentation window present, no remaining birth
        window means the NetHack process has genuinely ended.
        """
        names = self.tmux.list_windows()
        if not names:
            raise AdapterError("NetHack runtime window一覧を取得できません")
        if self.spec.game_window not in names:
            raise AdapterError("NetHack presentation windowを確認できません")
        excluded = {self.spec.game_window, self.spec.agent_window}
        candidates = [name for name in names if name not in excluded]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise AdapterError(
                f"NetHack runtime process windowを一意に特定できません: {candidates}"
            )
        return f"{self.spec.adapter_session}:{candidates[0]}"

    def readiness(self, deadline: float, cancel) -> None:
        if self.presentation_mode != "tiles":
            return super().readiness(deadline, cancel)
        # Keep the existing game/session/ownership checks and require both the
        # fixed outer presenter and this generation's supervisor handshake.
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
        self.tmux.capture_pane_checked(self.spec.adapter_session)
        presenter = XKit(self.g.display.name)
        pattern = presentation_window_pattern(f"docich-present-{self.spec.runtime_id}")
        while True:
            self._check_active(deadline, cancel)
            if not self.tmux.session_target_exists(self.spec.adapter_session):
                raise ReadinessTimeoutError("adapter sessionがありません")
            self._verify_session_ownership()
            self._verify_window_ownership(game_target, "game")
            game_states = self.tmux.pane_states_checked(game_target)
            if any(pane.dead for pane in game_states):
                raise ReadinessTimeoutError("game window paneがdeadです")
            manifest = self._tiles_manifest()
            if manifest:
                self._validate_tiles_manifest(manifest)
                status = manifest.get("status")
                if status in {"failed", "stopped", "cleanup_failed"}:
                    raise ReadinessTimeoutError(f"NetHack tiles presentation is {status}")
                if status in {"tiles_active", "fallback_tty"}:
                    if status == "tiles_active" and (
                        manifest.get("mode") != "tiles"
                        or type(manifest.get("browser_pid")) is not int
                        or type(manifest.get("frame_port")) is not int
                        or not 1 <= manifest["frame_port"] <= 65535
                    ):
                        raise AdapterError("NetHack tiles readiness handshakeが不正です")
                    if status == "fallback_tty" and (
                        manifest.get("mode") != "tty"
                        or type(manifest.get("tty_pid")) is not int
                    ):
                        raise AdapterError("NetHack TTY fallback handshakeが不正です")
                    if presenter.find_window(pattern, timeout=0.1) is not None:
                        return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("NetHack tiles presentationの準備がタイムアウトしました")
            if cancel is not None and cancel.wait(min(0.2, remaining)):
                raise DeadlineExceededError("adapter call はcancelされました")
            if cancel is None:
                time.sleep(min(0.2, remaining))

    def alive(self, deadline: float, cancel) -> bool:
        if not super().alive(deadline, cancel):
            return False
        if self.presentation_mode != "tiles":
            return True
        target = self._game_window_target()
        if not self.tmux.window_target_exists(target):
            return False
        self._verify_window_ownership(target, "game")
        if any(pane.dead for pane in self.tmux.pane_states_checked(target)):
            return False
        manifest = self._tiles_manifest()
        if not manifest:
            return False
        self._validate_tiles_manifest(manifest)
        status = manifest.get("status")
        if status == "tiles_active":
            return (
                manifest.get("mode") == "tiles"
                and type(manifest.get("browser_pid")) is int
                and type(manifest.get("frame_port")) is int
                and 1 <= manifest["frame_port"] <= 65535
            )
        if status == "fallback_tty":
            return manifest.get("mode") == "tty" and type(manifest.get("tty_pid")) is int
        return False

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        if self.presentation_mode != "tiles":
            return super().cleanup_runtime(deadline, cancel)
        prior = self._tiles_manifest()
        if prior:
            self._validate_tiles_manifest(prior)
        super().cleanup_runtime(deadline, cancel)
        while True:
            self._check_active(deadline, cancel)
            manifest = self._tiles_manifest()
            presentation = self._read_presentation_state()
            if not manifest:
                if prior:
                    raise AdapterError("NetHack tiles ownership manifestがcleanup中に消失しました")
                if not presentation:
                    return
                if presentation.get("status") == "stopped":
                    return
            else:
                self._validate_tiles_manifest(manifest)
            if (
                manifest.get("status") in {"stopped", "cleanup_failed"}
                and manifest.get("cleanup_complete") is True
                and manifest.get("browser_pid") is None
                and manifest.get("tty_pid") is None
                and presentation.get("status") == "stopped"
            ):
                return
            if manifest.get("status") == "cleanup_failed":
                raise AdapterError("NetHack tiles child cleanupに失敗しました")
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("NetHack tiles child cleanupが確認できません")
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _read_presentation_state(self) -> dict:
        try:
            value = json.loads(self._presentation_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_name_matches_player(self, name: str) -> bool:
        # Unix NetHack save files are named ``<uid><player>``; an optional
        # compression suffix may be present while the save is at rest.  A
        # plain substring match can incorrectly treat another player's save
        # such as ``1000otherdocich`` as proof that this run was suspended.
        player = re.escape(self.player_name)
        return re.fullmatch(
            rf"\d+{player}(?:\.[A-Za-z0-9]+)?",
            name,
            flags=re.IGNORECASE,
        ) is not None

    def _matching_save_files(self) -> tuple[Path, ...]:
        try:
            entries = tuple(self.save_dir.iterdir())
        except OSError as exc:
            raise AdapterError("NetHack save directory を検査できません") from exc
        return tuple(
            entry
            for entry in entries
            if entry.is_file() and self._save_name_matches_player(entry.name)
        )

    def _save_signatures(self) -> dict[str, tuple[int, int]]:
        signatures: dict[str, tuple[int, int]] = {}
        for path in self._matching_save_files():
            try:
                stat = path.stat()
            except OSError as exc:
                raise AdapterError("NetHack save fileを検査できません") from exc
            signatures[path.name] = (stat.st_mtime_ns, stat.st_size)
        return signatures

    def _new_or_changed_save(
        self, before: dict[str, tuple[int, int]]
    ) -> Path | None:
        candidates: list[tuple[int, Path]] = []
        for path in self._matching_save_files():
            try:
                stat = path.stat()
            except OSError as exc:
                raise AdapterError("NetHack save fileを検査できません") from exc
            signature = (stat.st_mtime_ns, stat.st_size)
            if before.get(path.name) != signature:
                candidates.append((stat.st_mtime_ns, path))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _write_boundary_result(
        self,
        request_id: str,
        *,
        outcome: str,
        save_file: Path | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "request_id": request_id,
            "game": self.spec.game,
            "runtime_id": self.spec.runtime_id,
            "generation": self.spec.generation,
            "player_name": self.player_name,
            "outcome": outcome,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if save_file is not None:
            # Do not persist host layout beyond the already configured save
            # directory; the basename is sufficient for operator diagnostics.
            payload["save_file"] = save_file.name
        atomic_write_json(self.spec.runtime_dir / BOUNDARY_RESULT_FILENAME, payload)

    def _record_ended_process_boundary(self, request_id: str) -> str:
        """Record the terminal boundary of a NetHack process that already ended.

        Shared by ``request_round_boundary`` and ``cancel_round_boundary`` so
        both paths classify the same evidence identically.  A player/agent may
        have used NetHack's normal save command: any matching save is kept as a
        suspension (the newest one).  Stale saves are not expected here because
        a normal restore consumes the previous save file, and no pre-``S``
        baseline exists when the cancel runs in a different process.  Without a
        save this is a terminal boundary (death/quit/ascension is classified
        later).  Raises when the evidence cannot be read or written.
        """
        existing = self._matching_save_files()
        save_file = None
        if existing:
            try:
                save_file = max(existing, key=lambda path: path.stat().st_mtime_ns)
            except OSError as exc:
                raise AdapterError("NetHack save fileを検査できません") from exc
        outcome = "suspended" if save_file is not None else "ended"
        self._write_boundary_result(request_id, outcome=outcome, save_file=save_file)
        return outcome

    def _boundary_wait_check(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            self._record_boundary_diag(
                "wait",
                reason="cancel_requested",
                process_target_present=None,
                process_alive=None,
                prompt_class="unknown",
                save_signature_changed=None,
            )
            raise DeadlineExceededError("NetHack save boundaryはcancelされました")
        if time.monotonic() >= deadline:
            self._record_boundary_diag(
                "wait",
                reason="wait_timeout",
                process_target_present=None,
                process_alive=None,
                prompt_class="unknown",
                save_signature_changed=self._save_signature_changed(),
            )
            raise ReadinessTimeoutError("NetHackの安全なsave終了を確認できませんでした")

    @staticmethod
    def _classify_prompt(text: str) -> str:
        """Map observed pane text onto a fixed enum. Never returns pane text."""
        if not text:
            return "unknown"
        if _is_save_prompt_pending(text):
            return "save_prompt_pending"
        if _is_save_confirmation_screen(text):
            return "save_confirmation"
        if _is_character_creation_screen(text):
            return "character_creation"
        return "unknown"

    def _save_signature_changed(self) -> bool | None:
        """Whether a save differs from the signature taken at boundary start.

        ``None`` means "unknown": either the wait never recorded a baseline
        (a different driver sent ``S``) or the save directory cannot be read
        right now. Unknown must never be reported as ``False``.
        """
        before = getattr(self, "_boundary_save_before", None)
        if before is None:
            return None
        try:
            return self._new_or_changed_save(before) is not None
        except AdapterError:
            return None

    def _classify_process_window_for_diag(self) -> str:
        """Classify the birth-window probe for diagnostics only (#1015).

        Returns one of ``present`` / ``absent`` / ``ambiguous`` / ``probe_failed``.
        This never feeds the fail-closed decision -- the caller already
        refused -- it only labels *why* the reviewed resolver could not give a
        target.
        """
        try:
            names = self.tmux.list_windows()
        except Exception:
            return "probe_failed"
        if not names:
            return "probe_failed"
        if getattr(self.spec, "game_window", None) not in names:
            return "probe_failed"
        excluded = {
            getattr(self.spec, "game_window", None),
            getattr(self.spec, "agent_window", None),
        }
        candidates = [name for name in names if name not in excluded]
        if not candidates:
            return "absent"
        if len(candidates) != 1:
            return "ambiguous"
        return "present"

    def _recorded_boundary_outcome(self) -> str:
        """Outcome already durably recorded for this runtime, else ``unknown``.

        Read-only and best effort: a missing/unreadable file is ``unknown``,
        never a success claim (#1015).
        """
        try:
            raw = (self.spec.runtime_dir / BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
            data = json.loads(raw)
        except Exception:
            return "unknown"
        if not isinstance(data, dict):
            return "unknown"
        outcome = data.get("outcome")
        return outcome if outcome in ("suspended", "ended") else "unknown"

    def _record_boundary_diag(
        self,
        operation: str,
        *,
        reason: str,
        process_target_present: bool | None,
        process_alive: bool | None,
        prompt_class: str,
        save_signature_changed: bool | None,
        boundary_outcome: str | None = None,
    ) -> None:
        """Best-effort bounded observation for owner-only diagnostics (#1015).

        Never raises and never writes pane text, paths or argv: a diagnostic
        failure must not change the fail-closed cancel decision. The whole
        body is guarded because fixtures and callers may carry a spec without
        a runtime directory at all.
        """
        try:
            if reason not in CANCEL_REFUSAL_REASONS or prompt_class not in PROMPT_CLASSES:
                return
            if boundary_outcome is None:
                boundary_outcome = self._recorded_boundary_outcome()
            if boundary_outcome not in BOUNDARY_OUTCOMES:
                boundary_outcome = "unknown"
            payload: dict[str, object] = {
                "schema_version": 1,
                "operation": operation,
                "reason": reason,
                "process_target_present": process_target_present,
                "process_alive": process_alive,
                "prompt_class": prompt_class,
                "save_signature_changed": save_signature_changed,
                "boundary_outcome": boundary_outcome,
                "generation": self.spec.generation,
                "runtime_id": self.spec.runtime_id,
                "recorded_at": dt.datetime.now(dt.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            runtime_dir = self.spec.runtime_dir
            atomic_write_json(Path(runtime_dir) / BOUNDARY_DIAG_FILENAME, payload)
        except Exception:
            pass

    def _refuse_cancel(
        self,
        reason: str,
        *,
        present: bool | None,
        alive: bool | None,
        prompt_class: str,
    ) -> None:
        """Record a bounded observation for a refused cancel (#1015).

        Best effort: the caller still returns ``False`` / re-raises exactly as
        before, so a diagnostic problem can never turn a refusal into success.
        """
        self._record_boundary_diag(
            "cancel",
            reason=reason,
            process_target_present=present,
            process_alive=alive,
            prompt_class=prompt_class,
            save_signature_changed=self._save_signature_changed(),
        )

    def _at_character_creation(self, process_target: str) -> bool:
        try:
            text = self.tmux.capture_pane(process_target)
        except Exception:
            # A failed capture is not evidence of character creation.  Fall
            # through to the normal (fail-closed) save boundary.
            return False
        return _is_character_creation_screen(text)

    def request_round_boundary(self, request_id: str, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            raise ReadinessTimeoutError("NetHack adapter sessionがありません")
        self._verify_session_ownership()

        process_target = self._runtime_process_window_target()
        if process_target is None:
            self._record_ended_process_boundary(request_id)
            return

        if self._at_character_creation(process_target):
            # The adventure never started, so there is nothing to suspend.
            # Record a terminal boundary instead of waiting for a save file
            # that cannot be written, which would leave the active game stuck.
            self._write_boundary_result(request_id, outcome="ended")
            return

        before = self._save_signatures()
        # Keep the baseline so a refused cancel can report whether a save
        # changed while waiting (#1015). Unknown must stay ``None``.
        self._boundary_save_before = before
        # Leave menus/prompts before issuing the normal save command.  Escape
        # is non-destructive at the map prompt; if it cannot normalize the UI,
        # the absence of a verified fresh save below makes the operation fail closed.
        self.tmux.send_keys(process_target, ["Escape"], literal=False)
        self._check_active(deadline, cancel)
        self.tmux.send_keys(process_target, ["S"], literal=True)
        save_confirmed = False

        while True:
            self._boundary_wait_check(deadline, cancel)
            try:
                process_alive = self.tmux.window_target_exists(process_target, strict=True)
            except Exception as exc:
                # A failed tmux probe is not evidence that the game exited.
                raise AdapterError("NetHack runtime processを確認できません") from exc

            if process_alive and not save_confirmed:
                try:
                    text = self.tmux.capture_pane(process_target)
                except Exception:
                    # A failed capture is not evidence that confirmation is needed.
                    # Keep waiting under the existing fail-closed deadline.
                    text = ""
                if _is_save_confirmation_screen(text):
                    self._boundary_wait_check(deadline, cancel)
                    self.tmux.send_keys(process_target, ["y"], literal=True)
                    save_confirmed = True

            save_file = self._new_or_changed_save(before)
            if not process_alive and save_file is not None:
                self._write_boundary_result(
                    request_id, outcome="suspended", save_file=save_file
                )
                return
            # Process exit without a fresh/changed save is deliberately not
            # success; wait until the deadline so a just-finishing fsync can land.
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

    def cancel_round_boundary(self, request_id: str, deadline: float, cancel) -> bool:
        """Withdraw a save request, but only while its confirmation is unanswered.

        ``S`` is reversible exactly until it is confirmed: NetHack asks
        ``Really save? [yn] (n)`` and its default answer ``n`` simply continues
        the game.  If the driver that sent ``S`` died or timed out at that
        prompt, the game sits there and canonical state stays ``draining``
        forever, because generic recovery needs this method to acknowledge.
        So answer ``n`` -- and only ``n`` -- when the runtime is verified to be
        ours, its process window exists and the screen shows the *unanswered*
        prompt, then require the prompt gone and the process still alive before
        acknowledging.  After ``y`` the game is writing its save and exiting,
        which cannot be undone; a live process without that prompt and any
        capture failure refuse without sending a key so recovery stays
        fail-closed.

        There is one more acknowledgment (#1015 requirement 3): when the
        presentation window exists but the birth window is gone,
        ``_runtime_process_window_target`` guarantees the NetHack process
        genuinely ended, so no save prompt can be pending and no process can be
        mid-save.  The same terminal boundary the request path derives from that
        evidence (``_record_ended_process_boundary``) is recorded first and only
        then acknowledged, letting canonical leave ``draining`` while the active
        runtime identity is retained.  If the evidence cannot be recorded, the
        cancel still refuses.  This path never sends a key, never kills a
        process and never edits canonical state.

        Every refusal path records a bounded observation first (#1015) so the
        owner can classify *why* the cancel was refused without pane text.
        """
        try:
            self._check_active(deadline, cancel)
        except DeadlineExceededError:
            reason = (
                "cancel_requested"
                if cancel is not None and cancel.is_set()
                else "deadline_exceeded"
            )
            self._refuse_cancel(
                reason,
                present=None,
                alive=None,
                prompt_class="unknown",
            )
            raise
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            self._refuse_cancel(
                "session_missing", present=None, alive=None, prompt_class="unknown"
            )
            return False
        try:
            self._verify_session_ownership()
        except Exception:
            self._refuse_cancel(
                "session_unowned", present=None, alive=None, prompt_class="unknown"
            )
            raise
        try:
            process_target = self._runtime_process_window_target()
        except AdapterError:
            # Presentation missing / ambiguous birth window: refuse without a
            # key, and keep the distinction visible (#1015).
            window_state = self._classify_process_window_for_diag()
            self._refuse_cancel(
                "process_window_ambiguous"
                if window_state == "ambiguous"
                else "process_window_probe_failed",
                present=None,
                alive=None,
                prompt_class="unknown",
            )
            raise
        if process_target is None:
            # Birth window gone + presentation window present means the game
            # process genuinely ended (see _runtime_process_window_target).
            # Record the same terminal boundary request_round_boundary derives
            # from this evidence, then acknowledge so the expired drain can be
            # cancelled (#1015 requirement 3). Refuse when the evidence itself
            # cannot be recorded.
            try:
                outcome = self._record_ended_process_boundary(request_id)
            except Exception:
                self._refuse_cancel(
                    "process_target_absent",
                    present=False,
                    alive=None,
                    prompt_class="unknown",
                )
                return False
            self._record_boundary_diag(
                "cancel",
                reason="process_target_absent",
                process_target_present=False,
                process_alive=None,
                prompt_class="unknown",
                save_signature_changed=None,
                boundary_outcome=outcome,
            )
            return True
        present = True
        try:
            alive = self.tmux.window_target_exists(process_target, strict=True)
        except Exception:
            alive = None
        try:
            text = self.tmux.capture_pane(process_target)
        except Exception:
            self._refuse_cancel(
                "capture_failed",
                present=present,
                alive=alive,
                prompt_class="capture_failed",
            )
            return False
        prompt_class = self._classify_prompt(text)
        if not _is_save_prompt_pending(text):
            self._refuse_cancel(
                "prompt_not_pending",
                present=present,
                alive=alive,
                prompt_class=prompt_class,
            )
            return False

        self._check_active(deadline, cancel)
        self.tmux.send_keys(process_target, ["n"], literal=True)
        while True:
            try:
                self._check_active(deadline, cancel)
            except DeadlineExceededError:
                reason = (
                    "cancel_requested"
                    if cancel is not None and cancel.is_set()
                    else "deadline_exceeded"
                )
                self._refuse_cancel(
                    reason, present=present, alive=True, prompt_class="save_confirmation"
                )
                raise
            try:
                alive = self.tmux.window_target_exists(process_target, strict=True)
            except Exception:
                self._refuse_cancel(
                    "post_key_probe_failed",
                    present=present,
                    alive=None,
                    prompt_class="unknown",
                )
                return False
            if not alive:
                # The save went through anyway; nothing was cancelled.
                self._refuse_cancel(
                    "process_gone",
                    present=present,
                    alive=False,
                    prompt_class="unknown",
                )
                return False
            try:
                text = self.tmux.capture_pane(process_target)
            except Exception:
                self._refuse_cancel(
                    "post_key_capture_failed",
                    present=present,
                    alive=True,
                    prompt_class="capture_failed",
                )
                return False
            if not _is_save_confirmation_screen(text):
                return True
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
