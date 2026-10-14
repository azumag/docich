"""Generation-owned presenter for a previously decoded off-air OBS source."""
from __future__ import annotations

from pathlib import Path
import sys

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..external_video_receiver import RELAY_PORT, ExternalVideoError, read_json, read_receiver
from ..game_switch import GameSwitchStore
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter

VIEW_NAME = "external-video-view"


class ExternalVideoProgramAdapter(CliCoordinatorAdapter):
    name = "program"

    def __init__(self, g, spec):
        game = GameConfig(name=VIEW_NAME, title="Fly Me To The Home!",
                          adapter="program", raw={"cli": {"command": ["sleep", "infinity"]}},
                          agent=GameAgentConfig(enabled=False),
                          lifecycle=GameLifecycleConfig(require_round_boundary=False))
        super().__init__(g, game, spec)

    def _receiver(self):
        try:
            owner = read_json(Path(self.g.state_dir) / "external_video_corner.json")
            if owner.get("status") not in {"starting", "active", "restoring"}:
                raise ExternalVideoError("corner has no live ownership")
            state = read_receiver(self.g, expected=owner.get("receiver_id"), fresh=True)
            request = GameSwitchStore(self.g.state_dir).receipts.load(owner.get("start_request_id"))
            if (not request or request.get("target") != VIEW_NAME
                    or request.get("generation") != self.spec.generation):
                raise ExternalVideoError("presenter request does not own this generation")
            return state
        except ExternalVideoError as exc:
            raise AdapterError(str(exc)) from None

    def _xterm_command(self):
        d = self.g.display
        return [sys.executable, str(Path(__file__).resolve().parents[1] / "presentation.py"),
                "--display", d.name, "--title", f"docich-present-{self.spec.runtime_id}",
                "--x", str(d.viewport_x), "--y", str(d.viewport_y),
                "--width", str(d.viewport_width), "--height", str(d.viewport_height),
                "--viewer-wait-sec", "20", "--framerate", "30", "--audio-sink", "soren_null",
                # 2026-10-11 実測: 既定の SDL(OpenGL=llvmpipe) だと表示 ffplay 2 本で
                # 約 1 コア。CPU レンダラにして VM の CPU 飽和(PSI 60-80%)を下げる。
                "--sdl-software-render",
                "--runtime-state", str(self.spec.runtime_dir / "presentation.json"),
                "--", "ffplay", "-hide_banner", "-loglevel", "error", "-autoexit",
                "-window_title", "Fly Me To The Home!", "-i",
                f"udp://127.0.0.1:{RELAY_PORT}?fifo_size=4096&overrun_nonfatal=1"]

    def preflight(self, deadline, cancel):
        self._receiver()
        d = self.g.display
        if (d.viewport_x, d.viewport_y, d.viewport_width, d.viewport_height) != (0, 90, 960, 540):
            raise AdapterError("external video requires the standard game viewport")
        super().preflight(deadline, cancel)

    def readiness(self, deadline, cancel):
        super().readiness(deadline, cancel)
        self._receiver()

    def alive(self, deadline, cancel):
        if not super().alive(deadline, cancel):
            return False
        try:
            self._receiver()
        except AdapterError:
            return False
        return True


def make_external_video_adapter(g, spec):
    if spec.game != VIEW_NAME:
        raise AdapterError("external video view identity is invalid")
    return ExternalVideoProgramAdapter(g, spec)
