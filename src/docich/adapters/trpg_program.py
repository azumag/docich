"""GameSwitch-owned read-only TRPG / murder-mystery program view (Issue #684).

The view serves the synthetic HTML board. It never talks to Twitch, never
mutates game state and never touches the stream encoder; the corner manager
owns when this runtime exists.
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
from ..trpg_view import DEFAULT_PORT, VIEW_NAME
from ..trpg_session import SESSION_FILE, prepare
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/trpg"


def trpg_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="TRPG / マーダーミステリー",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class TrpgProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing the TRPG board."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, trpg_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    def _game_command(self) -> list[str]:
        return [
            sys.executable, "-m", "docich.trpg_view",
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", self.spec.lease_id,
            "--session-file", str(self.spec.runtime_dir / SESSION_FILE),
        ]

    def _game_session_env(self) -> dict[str, str]:
        return broadcast_view_session_env()

    def _viewer_command(self) -> list[str]:
        display = self.g.display
        browser = broadcast_browser_command(
            f"http://127.0.0.1:{self.dashboard_port}/broadcast",
            window_size=(960, 540),
            window_position=(0, 0),
            profile_dir=self.spec.runtime_dir / "trpg-browser-profile",
            label="trpg HTML view",
        )
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
                "--", *browser,
            ]
        return browser

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        prepare(self.g, self.spec)
        super().preflight(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        CliCoordinatorAdapter.readiness(self, deadline, cancel)
        url = f"http://127.0.0.1:{self.dashboard_port}{READY_PATH}"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urlopen(url, timeout=min(2.0, max(0.05, deadline - monotonic()))) as response:
                    data = json.loads(response.read(64 * 1024))
                    if (response.status == 200 and isinstance(data, dict)
                            and data.get("ok") is True
                            and data.get("game") == self.spec.game
                            and data.get("runtime_id") == self.spec.runtime_id
                            and type(data.get("generation")) is int
                            and data.get("generation") == self.spec.generation
                            and data.get("lease_id") == self.spec.lease_id):
                        return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("trpg view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("trpg view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_trpg_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"trpg program view is not registered for {spec.game}")
    return TrpgProgramViewAdapter(g, spec)
