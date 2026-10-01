"""GameSwitch-owned read-only weather program view.

The view serves an already-published JMA snapshot. It never fetches data or
enqueues speech; the future corner manager owns those explicit actions.
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
from ..weather import WeatherError
from ..weather_view import DEFAULT_PORT, read_view
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import ProgramViewAdapter, _browser_bin

WEATHER_VIEW_NAME = "weather-view"
WEATHER_STATE_NAME = "weather"
READY_PATH = "/api/weather"
READY_TIMEOUT_S = 30.0


def weather_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=WEATHER_VIEW_NAME,
        title="docich 全国の天気",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


def _weather_bin() -> str:
    return str(Path(__file__).resolve().parents[3] / "bin" / "docich-weather")


class WeatherProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server and contained 960x540 browser view."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, weather_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT
        self.weather_state_dir = Path(g.state_dir) / WEATHER_STATE_NAME
        self.snapshot_path = self.weather_state_dir / "snapshot.json"

    def _game_command(self) -> list[str]:
        # serve is read-only: the caller must explicitly publish a current
        # snapshot before asking GameSwitch to start this runtime.
        return [
            _weather_bin(), "--state-dir", str(self.weather_state_dir), "serve",
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", self.spec.lease_id,
        ]

    def _viewer_command(self) -> list[str]:
        browser = _browser_bin()
        if not browser:
            raise AdapterError("chromium が見つかりません (weather HTML view)")
        command = [
            browser,
            f"--app=http://127.0.0.1:{self.dashboard_port}/",
            "--window-size=960,540",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-features=Translate,BackForwardCache",
            f"--user-data-dir={self.spec.runtime_dir / 'weather-browser-profile'}",
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
            # Validate the source again immediately before GameSwitch starts
            # the runtime. This is deliberately a read; no hidden fetch occurs.
            read_view(self.snapshot_path)
        except (OSError, WeatherError):
            raise AdapterError("weather snapshot is missing, invalid, or stale") from None
        if not Path(_weather_bin()).is_file():
            raise AdapterError("docich-weather executable is missing")
        super().preflight(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        # Reuse the normal generation-owned process/window readiness checks,
        # then prove the local service is serving this exact runtime's data.
        CliCoordinatorAdapter.readiness(self, deadline, cancel)
        url = f"http://127.0.0.1:{self.dashboard_port}{READY_PATH}"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urlopen(url, timeout=min(2.0, max(0.05, deadline - monotonic()))) as response:
                    data = json.loads(response.read(512 * 1024))
                if (
                    response.status == 200
                    and isinstance(data, dict)
                    and data.get("ok") is True
                    and data.get("runtime_id") == self.spec.runtime_id
                    and type(data.get("generation")) is int
                    and data.get("generation") == self.spec.generation
                    and data.get("lease_id") == self.spec.lease_id
                    and isinstance(data.get("cities"), list)
                    and len(data["cities"]) == 11
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("weather view is not fresh and ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("weather view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_weather_view_adapter(g, spec: RuntimeSpec):
    if spec.game != WEATHER_VIEW_NAME:
        raise AdapterError(f"weather program view is not registered for {spec.game}")
    return WeatherProgramViewAdapter(g, spec)
