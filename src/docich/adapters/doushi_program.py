"""GameSwitch-owned read-only 同志カード ranking view.

The adapter fetches the public TwiCa ranking exactly once before the switch
commits, validates it fail-closed and hands the result to the view process as
a transient runtime file. It never writes rankings anywhere else, never talks
to Twitch and never touches the stream encoder; the corner manager owns when
this runtime exists.

The endpoint is derived from ``SOREN_DIRECT_TWICA_OVERLAY_URL`` and is never
printed: not in argv, not in logs, not in state (``twica_renderer.validate_url``
same contract).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..doushi_ranking import (
    DEFAULT_LIMIT,
    DEFAULT_PERIOD,
    DoushiRankingError,
    derive_endpoint,
    fetch_ranking,
)
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..doushi_view import DEFAULT_PORT, VIEW_NAME, DoushiViewError
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/doushi"
PAYLOAD_FILENAME = "doushi_ranking.json"
#: design §6/§4: 開始時に 1 回だけ取得する（表示中の再取得はしない）。
HTTP_TIMEOUT_SECONDS = 10.0
MAX_PAYLOAD_BYTES = 512 * 1024


def doushi_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="同志カード大賞",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


def http_get(url: str, timeout: float) -> bytes:
    """The only network call in this corner. Used as the injected ``http_get``.

    Errors are deliberately generic: the URL may carry the streamer identity
    and must not travel through exception text into logs.
    """
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "docich-corner"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise DoushiRankingError("doushi ranking unavailable (fetch)")
        body = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(body) > MAX_PAYLOAD_BYTES:
        raise DoushiRankingError("doushi ranking unavailable (fetch)")
    return body


class DoushiProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing the fetched ranking."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, doushi_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    @property
    def payload_path(self) -> Path:
        # Transient runtime file: regenerated on every start and removed with
        # the runtime directory. It is never promoted into persistent state.
        return self.spec.runtime_dir / PAYLOAD_FILENAME

    def _game_command(self) -> list[str]:
        return [
            sys.executable, "-m", "docich.doushi_view",
            "--payload-file", str(self.payload_path),
            "--port", str(self.dashboard_port),
            "--runtime-id", self.spec.runtime_id,
            "--generation", str(self.spec.generation),
            "--lease-id", str(self.spec.lease_id),
        ]

    def _game_session_env(self) -> dict[str, str]:
        # tmux starts the command from the server environment and never
        # inherits the operator's PYTHONPATH, so bind this checkout explicitly
        # through the shared broadcast-view helper (repository standard).
        return broadcast_view_session_env()

    def _viewer_command(self) -> list[str]:
        # 翻訳UI抑止・日本語UIの固定は共通の broadcast_browser_command に一本化する
        # （docs/broadcast-html-view-standard.md）。
        command = broadcast_browser_command(
            f"http://127.0.0.1:{self.dashboard_port}/broadcast",
            window_size=(960, 540),
            window_position=(0, 0),
            profile_dir=self.spec.runtime_dir / "doushi-browser-profile",
            label="doushi HTML view",
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

    def fetch_ranking_payload(self) -> dict:
        """Fetch + validate once and persist it for the view process."""
        try:
            endpoint = derive_endpoint(
                # Environment-only, like twica_renderer: never argv / logs.
                os.environ.get("SOREN_DIRECT_TWICA_OVERLAY_URL", "")
            )
            payload = fetch_ranking(
                endpoint,
                period=DEFAULT_PERIOD,
                limit=DEFAULT_LIMIT,
                http_get=http_get,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except DoushiRankingError:
            # Fixed message: the endpoint and the response body stay out of it.
            raise AdapterError(
                "同志カードランキングを取得できないため開始できません"
            ) from None
        try:
            self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
            body = json.dumps(
                {
                    "period": DEFAULT_PERIOD,
                    "limit": DEFAULT_LIMIT,
                    "payload": payload,
                },
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            self.payload_path.write_bytes(body)
        except (OSError, ValueError):
            raise AdapterError("同志カードランキングを保存できません") from None
        return payload

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        # Present-but-broken remote data must fail the switch instead of
        # putting an unverifiable board on air (design §4: fail-closed skip).
        self.fetch_ranking_payload()
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
                    and isinstance(data.get("by_count"), list)
                    and isinstance(data.get("popular_cards"), list)
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("doushi view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("doushi view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_doushi_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"doushi program view is not registered for {spec.game}")
    return DoushiProgramViewAdapter(g, spec)
