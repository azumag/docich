"""GameSwitch-owned read-only view for the participatory othello MVP (#699).

Like ``doushi_program`` the adapter owns a generation-bound local server that
projects a pre-validated payload; here the payload is the room Worker's
spectator snapshot, polled live by the view process through one bounded
loopback/HTTPS control URL (``DOCICH_OTHELLO_CONTROL_URL``). The adapter
preflight only proves the control endpoint is reachable; the running match
continues even when a later poll fails (the view shows "接続できません"),
because a live match must never be force-quitted from the display side.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..othello_view import (
    OthelloViewError,
    SNAPSHOT_MAX_BYTES,
    VIEW_NAME,
    fetch_snapshot,
    validate_control_url,
)
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/othello"
HTTP_TIMEOUT_SECONDS = 2.5


def othello_view_game_config() -> GameConfig:
    """The synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="参加型オセロ",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


CONTROL_URL_KEY = "DOCICH_OTHELLO_CONTROL_URL"


class OthelloProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing the live room snapshot."""

    dashboard_kind = "html"

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, othello_view_game_config(), spec)
        self.dashboard_kind = "html"
        from ..othello_view import DEFAULT_PORT
        self.dashboard_port = DEFAULT_PORT

    def _game_command(self) -> list[str]:
        command = [
            sys.executable, "-m", "docich.othello_view",
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
        ]
        if self.spec.lease_id is not None:
            # lease_id is a canonical UUID or None (pre-lease start); only a
            # real lease is handed to the view (it validates the format).
            command += ["--lease-id", self.spec.lease_id]
        return command

    def _game_session_env(self) -> dict[str, str]:
        # The view polls the room Worker through the bounded control URL: pass
        # only that capability into the view session (never operator tokens).
        return broadcast_view_session_env(capability_keys=(CONTROL_URL_KEY,))

    def _viewer_command(self) -> list[str]:
        command = broadcast_browser_command(
            f"http://127.0.0.1:{self.dashboard_port}/broadcast",
            window_size=(960, 540),
            window_position=(0, 0),
            profile_dir=self.spec.runtime_dir / "othello-browser-profile",
            label="othello HTML view",
        )
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
        # Fail-closed: starting a view that cannot reach the room Worker would
        # put an unreachable board on air. A later poll failure only degrades
        # the display; it never force-quits a live match (no view-owned stop).
        url = control_url_from_env()
        try:
            validate_control_url(url)
        except OthelloViewError:
            raise AdapterError("othello control url is not configured") from None
        try:
            fetch_snapshot(url, timeout_seconds=HTTP_TIMEOUT_SECONDS)
        except (OSError, TimeoutError, OthelloViewError):
            raise AdapterError("othello room snapshot is unavailable") from None
        super().preflight(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        CliCoordinatorAdapter.readiness(self, deadline, cancel)
        url = f"http://127.0.0.1:{self.dashboard_port}{READY_PATH}"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urlopen(url, timeout=min(2.0, max(0.05, deadline - monotonic()))) as response:
                    data = json.loads(response.read(SNAPSHOT_MAX_BYTES))
                if (
                    response.status == 200
                    and isinstance(data, dict)
                    and data.get("ok") is True
                    and data.get("runtime_id") == self.spec.runtime_id
                    and type(data.get("generation")) is int
                    and data.get("generation") == self.spec.generation
                    and data.get("lease_id") == self.spec.lease_id
                    and (data.get("phase") is None or data.get("phase") in
                         {"IDLE", "ROOM_OPEN", "PLAYING", "FINISHED", "FAILED", "TIMEOUT"})
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("othello view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("othello view readiness was cancelled")
            sleep(min(0.25, remaining))


def control_url_from_env() -> str:
    from ..othello_view import control_url_from_env as _from_env
    return _from_env()


def make_othello_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"othello program view is not registered for {spec.game}")
    return OthelloProgramViewAdapter(g, spec)
