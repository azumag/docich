"""GameSwitch-owned read-only チャート解説コーナー view (issue #659).

The adapter generates the deterministic corner content before GameSwitch
commits the switch, writes a validated transient payload into this generation's
runtime directory, and serves it through :mod:`docich.chart_view`.  It never
fetches external chart data (the offline ``FixedChartFetcher`` is the default),
never talks to Twitch and never touches the stream encoder.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from .. import chart_view
from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..corner_659 import (
    STATE_FILENAME,
    ChartCornerManager,
    ChartError,
    FixedChartFetcher,
    chart_fixture,
)
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

VIEW_NAME = chart_view.VIEW_NAME
READY_PATH = chart_view.READY_PATH
PAYLOAD_FILENAME = "chart_view_payload.json"
MAX_PAYLOAD_BYTES = 600 * 1024


def chart_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="チャート解説コーナー",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class ChartProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing one chart commentary run."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, chart_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = chart_view.DEFAULT_PORT
        # corner_659 persists its generated content here; the view gets a
        # validated copy in the transient runtime directory below.
        self.corner_state_path = Path(g.state_dir) / STATE_FILENAME
        self.payload_path = self.spec.runtime_dir / PAYLOAD_FILENAME

    @property
    def state_path(self) -> Path:
        return self.corner_state_path

    def build_payload(self) -> dict:
        """Generate corner content and return the validated raw state to stage."""
        try:
            snapshot = chart_fixture()
            manager = ChartCornerManager(self.g, fetcher=FixedChartFetcher(snapshot))
            # CornerContent is a frozen dataclass, not a pydantic model: call
            # run() and read the persisted JSON instead of model_validate().
            manager.run(request_id=self.spec.runtime_id)
            state = json.loads(self.corner_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AdapterError("チャート解説の生成物を読み込めません") from exc
        except ChartError as exc:
            raise AdapterError("チャート解説の生成に失敗しました") from exc
        if not isinstance(state, dict):
            raise AdapterError("チャート解説の生成物が不正です")
        # Candles are not part of the generated state file; attach the same
        # offline fixture snapshot so the view can draw the price line.
        state["candles"] = [
            {"low": candle.low, "high": candle.high, "close": candle.close}
            for candle in snapshot.candles
        ]
        try:
            # Fail closed here as well as in the view: a malformed generation
            # must fail the switch, not put a stale board on air.
            chart_view.build_view_payload(state)
        except chart_view.ChartViewError as exc:
            raise AdapterError("チャート解説の生成物が不正です") from exc
        return state

    def write_payload(self) -> dict:
        try:
            payload = self.build_payload()
            self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(body) > MAX_PAYLOAD_BYTES:
                raise AdapterError("チャート解説の生成物が大きすぎます")
            self.payload_path.write_bytes(body)
        except AdapterError:
            raise
        except (OSError, ValueError):
            raise AdapterError("チャート解説の生成物を保存できません") from None
        return payload

    def _game_command(self) -> list[str]:
        # There is no bin/docich "chart-659 serve" subcommand. The view is a
        # Python module started inside the generation-owned session.
        return [
            sys.executable, "-m", "docich.chart_view",
            "--payload-file", str(self.payload_path),
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", self.spec.lease_id,
        ]

    def _game_session_env(self) -> dict[str, str]:
        # tmux starts the command from the server environment and never inherits
        # the operator's PYTHONPATH, so bind this checkout explicitly through
        # the shared broadcast-view helper (repository standard).
        return broadcast_view_session_env()

    def _viewer_command(self) -> list[str]:
        command = broadcast_browser_command(
            f"http://127.0.0.1:{self.dashboard_port}/broadcast",
            window_size=(960, 540),
            window_position=(0, 0),
            profile_dir=self.spec.runtime_dir / "chart-browser-profile",
            label="chart HTML view",
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
        # A broken payload must fail the switch instead of putting a stale
        # chart on air (same fail-closed contract as the other program views).
        self.write_payload()
        super().preflight(deadline, cancel)

    @staticmethod
    def _is_ready_response(data, spec: RuntimeSpec) -> bool:
        return (
            isinstance(data, dict)
            and data.get("ok") is True
            and data.get("corner") == chart_view.CORNER_ID
            # Compare against this generation's runtime id, never a literal.
            and data.get("runtime_id") == spec.runtime_id
            and type(data.get("generation")) is int
            and data.get("generation") == spec.generation
            and data.get("lease_id") == spec.lease_id
            and isinstance(data.get("frames"), list)
            and len(data["frames"]) >= 1
        )

    def readiness(self, deadline: float, cancel) -> None:
        CliCoordinatorAdapter.readiness(self, deadline, cancel)
        url = f"http://127.0.0.1:{self.dashboard_port}{READY_PATH}"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urlopen(url, timeout=min(2.0, max(0.05, deadline - monotonic()))) as response:
                    data = json.loads(response.read(600 * 1024))
                if response.status == 200 and self._is_ready_response(data, self.spec):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("chart view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("chart view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_chart_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"chart program view is not registered for {spec.game}")
    return ChartProgramViewAdapter(g, spec)
