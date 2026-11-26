"""GameSwitch-owned read-only view for ``new_corner_runtime`` corners (#5584).

One data-driven program adapter serves every corner registered in
``CORNER_SPECS``: ``spec.game`` is ``<corner_id>-view`` and selects the spec.
The adapter produces the corner record fail-closed through
:mod:`docich.new_corner_production` — theme selection, verified Web material
collection and the bounded ``RADIO:*`` direct-API draft all happen here,
before the switch commits — then hands the validated record to the view
process as a transient runtime file. It never talks to Twitch and never
touches the stream encoder; the corner manager owns when this runtime exists.

The production path is gated twice: the corner's ``DOCICH_NEW_CORNER_ENABLED``
flag and the global ``DOCICH_ALLOW_REAL_AI`` billing gate must both be set in
the service environment, and ``[new_corner] draft_agents`` must name an
explicit bounded direct-API chain. Any missing gate fails the switch closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep
import tomllib
from urllib.error import URLError
from urllib.request import urlopen

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..new_corner_production import produce_record, validate_record
from ..new_corner_runtime import CORNER_SPECS, CornerRuntimeError
from ..new_corner_view import port_for, view_name_for
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter
from .program import (
    ProgramViewAdapter,
    broadcast_browser_command,
    broadcast_view_session_env,
)

READY_PATH = "/api/new-corner"
PAYLOAD_FILENAME = "new_corner_record.json"
MAX_PAYLOAD_BYTES = 256 * 1024
#: One read of the [new_corner] config table per preflight.
MAX_CONFIG_BYTES = 1024 * 1024


def _corner_id_for(game: str) -> str:
    if not isinstance(game, str) or not game.endswith("-view"):
        raise AdapterError(f"new-corner program view is not registered for {game}")
    corner_id = game.removesuffix("-view")
    if corner_id not in CORNER_SPECS or view_name_for(corner_id) != game:
        raise AdapterError(f"new-corner program view is not registered for {game}")
    return corner_id


def new_corner_view_game_config(corner_id: str) -> GameConfig:
    """Return the synthetic view definition; it is not a playable game."""

    return GameConfig(
        name=view_name_for(corner_id),
        title=corner_id,
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


def _draft_agents(g) -> str:
    """The reviewed ``[new_corner] draft_agents`` direct-API chain."""
    path = getattr(g, "config_path", None)
    if path is None or not Path(path).is_file():
        raise AdapterError("new-corner draft agents are not configured")
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise AdapterError("new-corner draft agents are not configured") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise AdapterError("new-corner draft agents are not configured")
    try:
        section = tomllib.loads(raw.decode("utf-8")).get("new_corner", {})
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise AdapterError("new-corner draft agents are not configured") from None
    agents = section.get("draft_agents") if isinstance(section, dict) else None
    if not isinstance(agents, str) or not agents.strip():
        raise AdapterError("new-corner draft agents are not configured")
    return agents.strip()


class NewCornerProgramViewAdapter(ProgramViewAdapter):
    """A generation-bound local server showing one produced corner record."""

    def __init__(self, g, spec: RuntimeSpec):
        self.corner_id = _corner_id_for(spec.game)
        super().__init__(g, new_corner_view_game_config(self.corner_id), spec)
        self.dashboard_kind = "html"
        self.dashboard_port = port_for(self.corner_id)

    @property
    def payload_path(self) -> Path:
        # Transient runtime file: regenerated on every start and removed with
        # the runtime directory. It is never promoted into persistent state.
        return self.spec.runtime_dir / PAYLOAD_FILENAME

    def _game_command(self) -> list[str]:
        return [
            sys.executable, "-m", "docich.new_corner_view",
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
            profile_dir=self.spec.runtime_dir / "new-corner-browser-profile",
            label=f"{self.corner_id} HTML view",
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
        """Produce and contract-validate this generation's corner record."""
        agents = _draft_agents(self.g)
        result = produce_record(
            self.corner_id,
            env=dict(os.environ),
            agents=agents,
            generation=self.spec.generation,
            root=self.g.repo_root,
        )
        if not result.ok:
            raise AdapterError(f"new-corner record unavailable ({result.status})")
        record = dict(result.record)
        try:
            validate_record(record, self.corner_id)
        except (CornerRuntimeError, TypeError, ValueError, KeyError):
            raise AdapterError("new-corner record failed the runtime contract") from None
        return {"corner": self.corner_id, "record": record}

    def write_session_payload(self) -> dict:
        try:
            payload = self.build_session_payload()
            self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(body) > MAX_PAYLOAD_BYTES:
                raise AdapterError("new-corner record is too large")
            self.payload_path.write_bytes(body)
        except AdapterError:
            raise
        except (OSError, ValueError):
            raise AdapterError("new-corner record cannot be stored") from None
        return payload

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        # A missing or contract-breaking record fails the switch instead of
        # putting unvalidated content on air (same fail-closed skip contract
        # as the other view corners).
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
                    and data.get("corner") == self.corner_id
                    and data.get("runtime_id") == self.spec.runtime_id
                    and type(data.get("generation")) is int
                    and data.get("generation") == self.spec.generation
                    and data.get("lease_id") == self.spec.lease_id
                    and isinstance(data.get("frames"), list)
                    and isinstance(data.get("statusEntry"), dict)
                ):
                    return
            except (OSError, ValueError, URLError):
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("new-corner view is not ready")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("new-corner view readiness was cancelled")
            sleep(min(0.25, remaining))


def make_new_corner_view_adapter(g, spec: RuntimeSpec):
    _corner_id_for(spec.game)
    return NewCornerProgramViewAdapter(g, spec)
