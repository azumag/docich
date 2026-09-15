"""Graphical spectator specialization for the persistent CLI NetHack runtime."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import procs
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError
from ..nethack_spectator import MARKER_FILENAME, find_browser
from ..xkit import XKit
from .base import AdapterError
from .nethack import NethackCoordinatorAdapter


class NethackTileCoordinatorAdapter(NethackCoordinatorAdapter):
    """Keep the game as CLI NetHack while replacing only its viewer window."""

    def preflight(self, deadline: float, cancel) -> None:
        # Retain the parent CLI checks for compatibility, then require the
        # browser/presenter stack used only by graphical spectator mode.
        super().preflight(deadline, cancel)
        self._check_active(deadline, cancel)
        if find_browser() is None:
            raise AdapterError(
                "NetHack tile spectatorにはchromium/chromeが必要です "
                "(docs/games/nethack.md のP2を参照してください)"
            )
        for binary in ("Xvfb", "ffplay", "xdotool"):
            if not procs.which(binary):
                raise AdapterError(f"NetHack tile spectatorに{binary}が必要です")
        self._check_active(deadline, cancel)

    def _process_target_before_viewer(self) -> str:
        """Resolve the sole birth window before the spectator window exists."""
        names = self.tmux.list_windows()
        excluded = {self.spec.game_window, self.spec.agent_window}
        candidates = [name for name in names if name not in excluded]
        if len(candidates) != 1:
            raise AdapterError(
                f"NetHack spectator source windowを一意に特定できません: {candidates}"
            )
        return f"{self.spec.adapter_session}:{candidates[0]}"

    def _xterm_command(self) -> list[str]:
        # This override is intentionally only the viewer command.  The actual
        # NetHack process was already created by CliCoordinatorAdapter in the
        # generation-specific tmux session and remains the obs/send source.
        target = self._process_target_before_viewer()
        d = self.g.display
        if d.viewport_width > 0 and d.viewport_height > 0:
            x, y = d.viewport_x, d.viewport_y
            width, height = d.viewport_width, d.viewport_height
        else:
            x, y = 0, 0
            width, height = d.width, d.height
        return [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "nethack_spectator.py"),
            "--tmux-session",
            self.spec.adapter_session,
            "--target",
            target,
            "--state-dir",
            str(self.g.state_dir),
            "--runtime-dir",
            str(self.spec.runtime_dir),
            "--runtime-id",
            self.spec.runtime_id,
            "--display",
            d.name,
            "--x",
            str(x),
            "--y",
            str(y),
            "--width",
            str(width),
            "--height",
            str(height),
        ]

    def _expected_source_target(self) -> str:
        # At readiness the named spectator window exists; use the P1a resolver
        # which also checks that the presentation window is present.
        target = self._runtime_process_window_target()
        if target is None:
            raise ReadinessTimeoutError("NetHack source process windowがありません")
        return target

    def _marker(self) -> dict[str, object] | None:
        path = self.spec.runtime_dir / MARKER_FILENAME
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeError):
            return None
        return value if isinstance(value, dict) else None

    def _health_once(self, expected_target: str, timeout: float) -> bool:
        marker = self._marker()
        if marker is None:
            return False
        if marker.get("schema_version") != 1:
            raise AdapterError("NetHack spectator marker schemaが不正です")
        if marker.get("runtime_id") != self.spec.runtime_id:
            raise AdapterError("NetHack spectator marker runtime_idが一致しません")
        if marker.get("host") != "127.0.0.1":
            raise AdapterError("NetHack spectatorはloopback以外へbindできません")
        if marker.get("target") != expected_target:
            raise AdapterError("NetHack spectator source targetが一致しません")
        port = marker.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise AdapterError("NetHack spectator marker portが不正です")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/healthz",
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=max(0.05, timeout)) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read(4096).decode("utf-8"))
        except (
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeError,
        ):
            return False
        return bool(
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("runtime_id") == self.spec.runtime_id
        )

    def readiness(self, deadline: float, cancel) -> None:
        # Verify all generic tmux/runtime ownership first.
        super().readiness(deadline, cancel)
        expected_target = self._expected_source_target()
        presenter = XKit(self.g.display.name)
        title = f"^docich-present-{self.spec.runtime_id}$"

        while True:
            self._check_active(deadline, cancel)
            game_target = self._game_window_target()
            if not self.tmux.window_target_exists(game_target):
                raise ReadinessTimeoutError("NetHack spectator windowがありません")
            self._verify_window_ownership(game_target, "game")
            states = self.tmux.pane_states_checked(game_target)
            if any(pane.dead for pane in states):
                raise ReadinessTimeoutError("NetHack spectator paneがdeadです")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("NetHack spectatorの準備がタイムアウトしました")
            window_id = presenter.find_window(title, timeout=min(0.4, remaining))
            healthy = self._health_once(expected_target, min(0.4, remaining))
            if window_id is not None and healthy:
                return
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("adapter call はcancelされました")
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("NetHack spectatorの準備がタイムアウトしました")
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
