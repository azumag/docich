"""GameSwitch-owned read-only 共産主義法律シミュレーター view (#697).

The adapter builds the authored session for this generation, validates it
fail-closed through the deterministic engine and hands the result to the view
process as a transient runtime file. It never fetches anything, never talks to
Twitch and never touches the stream encoder; the corner manager owns when this
runtime exists.

There is no LLM brain yet: the session comes from the fixed bank in
``lawsim_sessions`` (rotated one step per switch generation). Model calls and
billing stay behind a separate owner-approved step, exactly like the design
doc's follow-up cards.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..communist_law_simulator import CORNER_ID, LawSimError, session_trace
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..lawsim_sessions import select_index, session_for_index
from ..lawsim_view import DEFAULT_PORT, VIEW_NAME, LawSimViewError
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/lawsim"
PAYLOAD_FILENAME = "lawsim_session.json"
MAX_PAYLOAD_BYTES = 256 * 1024


def lawsim_view_game_config() -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""

    return GameConfig(
        name=VIEW_NAME,
        title="共産主義法律シミュレーター",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


class LawSimProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing one authored law session."""

    def __init__(self, g, spec: RuntimeSpec):
        super().__init__(g, lawsim_view_game_config(), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = DEFAULT_PORT

    @property
    def payload_path(self) -> Path:
        # Transient runtime file: regenerated on every start and removed with
        # the runtime directory. It is never promoted into persistent state.
        return self.spec.runtime_dir / PAYLOAD_FILENAME

    def _game_command(self) -> list[str]:
        return [
            sys.executable, "-m", "docich.lawsim_view",
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
            profile_dir=self.spec.runtime_dir / "lawsim-browser-profile",
            label="lawsim HTML view",
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

    def build_session_payload(self) -> dict:
        """Pick this generation's session and engine-validate it fail-closed."""

        try:
            index = select_index(self.spec.generation)
            data = session_for_index(index)
            trace = session_trace(data)
        except (LawSimError, IndexError, ValueError):
            raise AdapterError("法律シミュレーターの会議記録が不正です") from None
        if trace["finalStatus"] not in {
            "plan_achieved", "plan_incomplete",
            "collapse_public_unrest", "collapse_party_split",
            "collapse_trade_default", "collapse_black_market",
        }:
            raise AdapterError("法律シミュレーターの会議記録が不正です")
        return {"corner": CORNER_ID, "session_index": index, "session": data["session"]}

    def write_session_payload(self) -> dict:
        try:
            payload = self.build_session_payload()
            self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(body) > MAX_PAYLOAD_BYTES:
                raise AdapterError("法律シミュレーターの会議記録が大きすぎます")
            self.payload_path.write_bytes(body)
        except AdapterError:
            raise
        except (OSError, ValueError):
            raise AdapterError("法律シミュレーターの会議記録を保存できません") from None
        return payload

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        # A broken session must fail the switch instead of putting an
        # unvalidated board on air (same fail-closed skip contract as doushi).
        self.write_session_payload()
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
                    and data.get("corner") == CORNER_ID
                    and data.get("runtime_id") == self.spec.runtime_id
                    and type(data.get("generation")) is int
                    and data.get("generation") == self.spec.generation
                    and data.get("lease_id") == self.spec.lease_id
                    and isinstance(data.get("frames"), list)
                    and isinstance(data.get("turns"), list)
                    and isinstance(data.get("finalStatus"), str)
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("lawsim view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("lawsim view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_lawsim_view_adapter(g, spec: RuntimeSpec):
    if spec.game != VIEW_NAME:
        raise AdapterError(f"lawsim program view is not registered for {spec.game}")
    return LawSimProgramViewAdapter(g, spec)
