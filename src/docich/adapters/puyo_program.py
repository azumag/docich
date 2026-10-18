"""GameSwitch-owned read-only ぷよぷよクローン self-play view.

The adapter plays one bounded offline self-play through ``puyo_corner``
exactly once before the switch commits (seed derived from a fresh runtime id,
deterministic brain writing), writes the validated match summary to the view
process as a transient runtime file and hands the broadcast to the local
rendering. It never writes matches anywhere else, never talks to Twitch and
never touches the stream encoder; the corner manager owns when this runtime
exists.

Precedent: ``doushi_program.py`` (single-fetch, fail-closed program view).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..puyo_corner import play_match, finish, new_state, start
from ..puyo_view import (
    DEFAULT_PORT,
    VIEW_NAME,
    match_payload_from_state,
)
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/puyo"
PAYLOAD_FILENAME = "puyo_match.json"
#: one bounded offline self-play; the engine's own budgets bound the loop
MATCH_MAX_STEPS = 1600
MAX_PAYLOAD_BYTES = 512 * 1024


def puyo_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""
    return GameConfig(
        name=VIEW_NAME,
        title="ぷよぷよクローン",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class PuyoProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing one played offline match."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, puyo_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    @property
    def payload_path(self) -> Path:
        # Transient runtime file: regenerated on every start and removed with
        # the runtime directory. It is never promoted into persistent state.
        return self.spec.runtime_dir / PAYLOAD_FILENAME

    def _game_command(self) -> list[str]:
        return [
            sys.executable, "-m", "docich.puyo_view",
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
            profile_dir=self.spec.runtime_dir / "puyo-browser-profile",
            label="puyo HTML view",
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

    def play_match_payload(self) -> dict:
        """Play one bounded offline self-play and persist it for the view."""
        state = new_state({}, seed=uuid.uuid4().int & ((1 << 64) - 1))
        try:
            start(state)
            play_match(state, max_steps=MATCH_MAX_STEPS)
            if state["phase"] == "ROUND_OVER":
                finish(state)
        except Exception as exc:  # engine contracts: never put garbage on air
            raise AdapterError("ぷよぷよクローンの対局を進められなかったため開始できません") from exc
        payload = match_payload_from_state(state)
        try:
            self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
            body = json.dumps(
                {
                    "schema": 1,
                    "payload": payload,
                },
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            if len(body) > MAX_PAYLOAD_BYTES:
                raise AdapterError("ぷよぷよクローンの対局データが大きすぎるため開始できません")
            self.payload_path.write_bytes(body)
        except AdapterError:
            raise
        except (OSError, ValueError):
            raise AdapterError("ぷよぷよクローンの対局データを保存できません") from None
        return payload

    @staticmethod
    def _readiness_ok(data) -> bool:
        return (
            isinstance(data, dict)
            and data.get("ok") is True
            and isinstance(data.get("user"), dict)
            and isinstance(data.get("ai"), dict)
        )

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        # A match summary that cannot be produced must fail the switch instead
        # of putting an unverifiable board on air (fail-closed skip).
        self.play_match_payload()
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
                    and self._readiness_ok(data)
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
                raise ReadinessTimeoutError("puyo view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("puyo view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_puyo_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"puyo program view is not registered for {spec.game}")
    return PuyoProgramViewAdapter(g, spec)
