"""GameSwitch-owned read-only AI探偵ミステリー program view.

The view serves the case board the corner manager already wrote on loopback.
It never talks to Twitch, never mutates the case and never touches the stream
encoder; the corner manager owns when this runtime exists.

Like the weather and 予想王 information screens, this is a broadcast-native
information screen rather than a game: when ``display.width/height`` are
known, the board owns the complete frame ``(0, 0, width, height)`` (1280x720
in production) and falls back to the shared game-area viewport
``(0, 90, 960x540)`` otherwise. The full-screen exception now covers 天気予報・
予想王・AI探偵ミステリー (2026-10-21 owner request, on top of #2494). The corner
manager still hides the OBS status rails for this corner only and shows them
again on every exit (#2494, owner approval 2026-10-20).
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
from ..mystery_view import DEFAULT_PORT, VIEW_NAME, read_payload
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/mystery"


def mystery_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="AI探偵ミステリー",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class MysteryProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing the case board."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, mystery_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    def _game_command(self) -> list[str]:
        # ``serve`` is read-only: the corner writes the payload, the view only
        # renders it, and a missing payload is a "preparing" board rather than
        # a hidden write.
        return [
            sys.executable, "-m", "docich.mystery_view",
            "--state-dir", str(self.g.state_dir),
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", self.spec.lease_id,
        ]

    def _game_session_env(self) -> dict[str, str]:
        # tmux starts the command from the server environment and never
        # inherits the operator's PYTHONPATH, so bind this checkout explicitly.
        return broadcast_view_session_env()

    def _viewer_command(self) -> list[str]:
        display = self.g.display
        # The case board is a broadcast-native information screen like the
        # weather and 予想王 views, not a game that needs the shared top/right/
        # bottom status rails.  When the full X11 display dimensions are known,
        # own the complete broadcast frame so the board is not squeezed next to
        # those rails for the whole 30-minute corner (2026-10-21 owner request;
        # the weather precedent is PR #1874, 予想王 is #2237).
        full_width = int(getattr(display, "width", 0) or 0)
        full_height = int(getattr(display, "height", 0) or 0)
        if full_width > 0 and full_height > 0:
            present_x, present_y = 0, 0
            present_width, present_height = full_width, full_height
        else:
            present_x, present_y = display.viewport_x, display.viewport_y
            present_width, present_height = display.viewport_width, display.viewport_height

        browser_width = present_width if present_width > 0 else 960
        browser_height = present_height if present_height > 0 else 540
        browser = broadcast_browser_command(
            f"http://127.0.0.1:{self.dashboard_port}/broadcast",
            window_size=(browser_width, browser_height),
            window_position=(0, 0),
            profile_dir=self.spec.runtime_dir / "mystery-browser-profile",
            label="mystery HTML view",
        )
        if present_width > 0 and present_height > 0:
            return [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "presentation.py"),
                "--display", display.name,
                "--title", f"docich-present-{self.spec.runtime_id}",
                "--x", str(present_x),
                "--y", str(present_y),
                "--width", str(present_width),
                "--height", str(present_height),
                "--", *browser,
            ]
        return browser

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        # A present-but-invalid payload must fail the switch instead of putting
        # an unverifiable board on air. Missing is a "preparing" board.
        try:
            payload = read_payload(
                Path(self.g.state_dir) / "mystery_view.json")
        except OSError:
            payload = None
        if payload is not None and payload.get("phase") not in (
                "intro", "investigate", "interrogate", "deduction",
                "prediction", "reveal", "done"):
            raise AdapterError("事件ボードの状態が不正です") from None
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
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("mystery view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("mystery view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_mystery_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"mystery program view is not registered for {spec.game}")
    return MysteryProgramViewAdapter(g, spec)
