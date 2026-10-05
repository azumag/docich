"""Generation-owned program view for the Tsuitate beta corner."""
from __future__ import annotations

import json
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..tsuitate_beta_control import ControlError, call_beta_control
from ..tsuitate_view import DEFAULT_PORT, VIEW_NAME
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import ProgramViewAdapter, _browser_bin


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

    def _viewer_command(self) -> list[str]:
        browser = _browser_bin()
        if not browser:
            raise AdapterError("chromium が見つかりません (tsuitate HTML view)")
        command = [
            browser,
            f"--app=http://127.0.0.1:{self.dashboard_port}/broadcast",
            "--window-size=960,540",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-features=Translate,BackForwardCache",
            f"--user-data-dir={self.spec.runtime_dir / 'tsuitate-browser-profile'}",
        ]
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
