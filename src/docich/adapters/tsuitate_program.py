"""Generation-owned program view for the Tsuitate beta corner."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..tsuitate_beta_control import CAPABILITY_ENV_KEYS, ControlError, call_beta_control
from ..tsuitate_view import DEFAULT_PORT, VIEW_NAME
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)


def tsuitate_view_game_config() -> GameConfig:
    return GameConfig(
        name=VIEW_NAME,
        title="AI衝立将棋",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class TsuitateProgramViewAdapter(ProgramViewAdapter):
    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, tsuitate_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    def _game_command(self) -> list[str]:
        return [
            sys.executable, "-m", "docich.tsuitate_view",
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", self.spec.lease_id,
        ]

    def _game_session_env(self) -> dict[str, str]:
        # The view runs as ``python -m docich.tsuitate_view`` inside this
        # generation's own tmux session.  tmux starts that command from the
        # server environment and never inherits the operator's PYTHONPATH, so
        # without the import path the pane dies on ModuleNotFoundError before
        # ownership can be verified — the session is already gone by the time
        # create_game_session_owned reads it back, which surfaces as
        # "no such session: $N" and rolls the whole switch back.
        #
        # The same rule applies to the beta-control capability: the broadcast
        # view calls ``POST /beta-control status`` server-side to decide between
        # a lifecycle label and 「制御接続を確認できません」.  Without the two
        # capability keys in this session it can never answer ``ok:true``, the
        # adapter's readiness loop above never settles, and the start switch
        # burns its deadline and rolls back.  Only the reviewed control keys are
        # carried; WebUI operator/viewer tokens are never passed.
        return broadcast_view_session_env(capability_keys=CAPABILITY_ENV_KEYS)

    def _viewer_command(self) -> list[str]:
        command = broadcast_browser_command(
            f"http://127.0.0.1:{self.dashboard_port}/broadcast",
            window_size=(960, 540),
            window_position=(0, 0),
            profile_dir=self.spec.runtime_dir / "tsuitate-browser-profile",
            label="tsuitate HTML view",
        )
        display = self.g.display
        if display.viewport_width > 0 and display.viewport_height > 0:
            from pathlib import Path
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
            status = call_beta_control("status")
        except ControlError as exc:
            raise AdapterError("衝立将棋BOTの制御APIを確認できません") from exc
        if status.get("readyForNextRun") is not True:
            raise AdapterError("衝立将棋BOTは次局を開始できる状態ではありません")
        super().preflight(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        CliCoordinatorAdapter.readiness(self, deadline, cancel)
        url = f"http://127.0.0.1:{self.dashboard_port}/api/tsuitate"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urlopen(url, timeout=min(2.0, max(0.05, deadline - monotonic()))) as response:
                    data = json.loads(response.read(64 * 1024))
                if (
                    response.status == 200
                    and isinstance(data, dict)
                    and data.get("ok") is True
                    and data.get("runtime_id") == self.spec.runtime_id
                    and data.get("generation") == self.spec.generation
                    and data.get("lease_id") == self.spec.lease_id
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("tsuitate view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("tsuitate view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_tsuitate_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"tsuitate program view is not registered for {spec.game}")
    return TsuitateProgramViewAdapter(g, spec)
