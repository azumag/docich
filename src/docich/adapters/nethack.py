"""NetHack-specific coordinator behavior for long-running program sessions.

The legacy/agent observation contract stays the normal CLI adapter: the game
still runs in the generation-specific tmux session and ``docich obs/send`` see
exactly the same text terminal.  This coordinator specialization adds only the
lifecycle semantics needed by the NetHack corner:

* always launch the game with one stable ``-u`` player name so normal NetHack
  save files can be restored by the next runtime;
* before any coordinator switch/stop, use NetHack's normal ``S`` command and
  wait for both the game process window to exit and a save file for that
  player to exist;
* fail closed if durable suspension cannot be verified.  The coordinator then
  keeps/rolls back the runtime rather than silently destroying an adventure.

This is normal NetHack save/restore, not explore-mode save scumming.  A normal
restore consumes the previous save file; the next program boundary creates a
new one.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from dataclasses import replace
from pathlib import Path

from ..game_switch import ReadinessTimeoutError, atomic_write_json
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter

DEFAULT_PLAYER_NAME = "docich"
DEFAULT_SAVE_DIR = Path("/var/games/nethack/save")
BOUNDARY_RESULT_FILENAME = "nethack_boundary.json"
_PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,31}$")


class NethackCoordinatorAdapter(CliCoordinatorAdapter):
    """CLI coordinator adapter with NetHack's suspend/resume boundary."""

    def __init__(self, g, game, spec):
        # NetHack's program boundary is a safe save, even though the generic
        # nethack.toml remains a plain CLI definition for legacy observation.
        lifecycle = replace(game.lifecycle, require_round_boundary=True)
        super().__init__(g, replace(game, lifecycle=lifecycle), spec)
        self.player_name, self.save_dir = self._load_nethack_settings(game)

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

    def preflight(self, deadline: float, cancel) -> None:
        super().preflight(deadline, cancel)
        self._check_active(deadline, cancel)
        if not self.save_dir.is_dir():
            raise AdapterError(f"NetHack save directory がありません: {self.save_dir}")
        try:
            next(self.save_dir.iterdir(), None)
        except OSError as exc:
            raise AdapterError("NetHack save directory を検査できません") from exc
        self._check_active(deadline, cancel)

    def _runtime_process_window_target(self) -> str | None:
        """Resolve the birth window which owns the actual NetHack process.

        The coordinator session contains one unnamed/name-derived birth window
        for the CLI process plus the named presentation/agent windows.  The
        birth window deliberately is not the viewer window.
        """
        names = self.tmux.list_windows()
        excluded = {self.spec.game_window, self.spec.agent_window}
        candidates = [name for name in names if name not in excluded]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise AdapterError(
                f"NetHack runtime process windowを一意に特定できません: {candidates}"
            )
        return f"{self.spec.adapter_session}:{candidates[0]}"

    def _matching_save_files(self) -> tuple[Path, ...]:
        try:
            entries = tuple(self.save_dir.iterdir())
        except OSError as exc:
            raise AdapterError("NetHack save directory を検査できません") from exc
        needle = self.player_name.casefold()
        return tuple(
            entry
            for entry in entries
            if entry.is_file() and needle in entry.name.casefold()
        )

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

    def request_round_boundary(self, request_id: str, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            raise ReadinessTimeoutError("NetHack adapter sessionがありません")
        self._verify_session_ownership()

        process_target = self._runtime_process_window_target()
        if process_target is None:
            # The game already ended (for example a real death).  That is a
            # valid program boundary but not a suspension; later run-history
            # phases classify the terminal outcome from game records.
            self._write_boundary_result(request_id, outcome="ended")
            return

        # Leave menus/prompts before issuing the normal save command.  Escape
        # is non-destructive at the map prompt; if it cannot normalize the UI,
        # the absence of a verified save below makes the operation fail closed.
        self.tmux.send_keys(process_target, ["Escape"], literal=False)
        self._check_active(deadline, cancel)
        self.tmux.send_keys(process_target, ["S"], literal=True)

        while True:
            self._check_active(deadline, cancel)
            try:
                process_alive = self.tmux.window_target_exists(process_target, strict=True)
            except Exception as exc:
                # A vanished whole session is not evidence that the save was
                # durable.  Continue only when a save file independently proves
                # successful suspension; otherwise report a hard error below.
                process_alive = False
                probe_error = exc
            else:
                probe_error = None

            saves = self._matching_save_files()
            if not process_alive and saves:
                newest = max(saves, key=lambda path: path.stat().st_mtime_ns)
                self._write_boundary_result(
                    request_id, outcome="suspended", save_file=newest
                )
                return
            if not process_alive and probe_error is not None and not saves:
                raise AdapterError(
                    "NetHack runtime消失後にsave fileを確認できませんでした"
                ) from probe_error
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError(
                    "NetHackの安全なsave終了を確認できませんでした"
                )
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

    def cancel_round_boundary(self, request_id: str, deadline: float, cancel) -> None:
        # ``S`` has no reversible in-process phase: once NetHack accepts it the
        # game is writing a normal save and exiting.  Cancellation therefore
        # must not send any extra key that could corrupt/alter that operation.
        self._check_active(deadline, cancel)
        return None
