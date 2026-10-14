"""GameSwitch-owned read-only 予想王 program view.

The view serves the already-recorded prediction leaderboard on loopback. It
never records a prediction, never calls Twitch and never touches the stream
encoder; the corner manager owns when this runtime exists.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..prediction_leaderboard import LeaderboardError
from ..yoso_view import DEFAULT_PORT, VIEW_NAME, YosoViewError, board
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import ProgramViewAdapter, _browser_bin

#: ``src/`` of this checkout, the import path the view needs at runtime.
DOCICH_SRC = Path(__file__).resolve().parents[2]
READY_PATH = "/api/yosou"


def yoso_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="予想王",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class YosoProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing the recorded ranking."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, yoso_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    def _game_command(self) -> list[str]:
        # ``serve`` is read-only: the corner never mutates the ledger, and a
        # missing ledger is an empty board rather than a hidden write.
        return [
            sys.executable, "-m", "docich.yoso_view",
            "--state-dir", str(self.g.state_dir),
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", self.spec.lease_id,
        ]

    def _game_session_env(self) -> dict[str, str]:
        # tmux starts the command from the server environment and never
        # inherits the operator's PYTHONPATH, so bind this checkout explicitly.
        return {"PYTHONPATH": str(DOCICH_SRC)}

    def _viewer_command(self) -> list[str]:
        browser = _browser_bin()
        if not browser:
            raise AdapterError("chromium が見つかりません (yosou HTML view)")
        command = [
            browser,
            f"--app=http://127.0.0.1:{self.dashboard_port}/broadcast",
            "--window-size=960,540",
            "--window-position=0,0",
            "--lang=ja-JP",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-translate",
            "--disable-features=Translate,TranslateUI,BackForwardCache",
            f"--user-data-dir={self.spec.runtime_dir / 'yosou-browser-profile'}",
        ]
        display = self.g.display
        if display.viewport_width > 0 and display.viewport_height > 0:
            return [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "presentation.py"),
                "--display", display.name,
                "--title", f"docich-present-{self.spec.runtime_id}",
                "--x", str(display.viewport_x),
                "--y", str(display.viewport_y),
                "--width", str(display.viewport_width),
                "--height", str(display.viewport_height),
                "--", *command,
            ]
        return command

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        try:
            # A present-but-broken ledger must fail the switch instead of
            # putting an unverifiable board on air. Missing is an empty board.
            board(self.g.state_dir)
        except (OSError, LeaderboardError, YosoViewError):
            raise AdapterError("予想台帳が不正または読めません") from None
        super().preflight(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        CliCoordinatorAdapter.readiness(self, deadline, cancel)
        url = f"http://127.0.0.1:{self.dashboard_port}{READY_PATH}"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urlopen(url, timeout=min(2.0, max(0.05, deadline - monotonic()))) as response:
                    data = json.loads(response.read(256 * 1024))
                if (
                    response.status == 200
                    and isinstance(data, dict)
                    and data.get("ok") is True
                    and data.get("runtime_id") == self.spec.runtime_id
                    and type(data.get("generation")) is int
                    and data.get("generation") == self.spec.generation
                    and data.get("lease_id") == self.spec.lease_id
                    and isinstance(data.get("ranking"), list)
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("yosou view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("yosou view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_yoso_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"yoso program view is not registered for {spec.game}")
    return YosoProgramViewAdapter(g, spec)
