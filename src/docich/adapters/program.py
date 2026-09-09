"""Program-view coordinator adapter for the PAPER trading corner (P0-3).

A program view is NOT a game: it has no matches, no score, no agent, and no
round boundary. It reuses the CLI presentation path (owned tmux session +
xterm viewer through presentation.py) so placement, ownership, quiesce and
rollback behave exactly like a CLI game switch, while ``make_coordinator_
adapter`` never resolves it from the games catalog.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter

#: Reserved canonical name for the PAPER dashboard view.
PAPER_VIEW_NAME = "paper-view"

#: Marker the dashboard renderer always prints; readiness requires it.
VIEW_READY_MARKER = "PAPER 暗号資産コーナー"


def paper_view_game_config() -> GameConfig:
    """Synthetic game config for the dashboard view (never read from toml)."""
    return GameConfig(
        name=PAPER_VIEW_NAME,
        title="PAPER 暗号資産コーナー",
        adapter="program",
        raw={"cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 18}},
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


def _docich_bin() -> str:
    return str(Path(__file__).resolve().parents[3] / "bin" / "docich")


class ProgramViewAdapter(CliCoordinatorAdapter):
    """Coordinator adapter showing the read-only trading dashboard."""

    name = "program"

    def __init__(self, g, game, spec):
        super().__init__(g, game, spec)
        # A view never plays: no agent loop, no round-boundary wait. Both
        # directions switch through the immediate-quiesce path.
        self.agent_enabled = False
        self.requires_round_boundary = False
        self.request_round_boundary = None
        self.cancel_round_boundary = None

    def _game_command(self) -> list[str]:
        # State resolution stays explicit: a wrong profile must fail here,
        # not render another state_dir's numbers.
        return [
            _docich_bin(), "--config", str(self.g.config_path),
            "trading", "--state-dir", str(self.g.state_dir / "trading"),
            "dashboard-watch",
        ]

    def start_agent(self, deadline: float, cancel) -> None:
        raise AdapterError("program view has no agent")

    def stop_agent(self, deadline: float, cancel) -> None:
        return None

    def readiness(self, deadline: float, cancel) -> None:
        super().readiness(deadline, cancel)
        try:
            text = self.tmux.capture_pane_checked(self._game_window_target())
        except Exception as exc:
            from .base import AdapterError as _AdapterError

            raise _AdapterError(f"program view の pane を取得できません: {exc}") from exc
        if VIEW_READY_MARKER not in (text or ""):
            from .cli_game import ReadinessTimeoutError

            raise ReadinessTimeoutError("program view にダッシュボードが表示されません")


def make_program_view_adapter(g, spec):
    """Build the PAPER view adapter. Only the reserved view name resolves."""
    if spec.game != PAPER_VIEW_NAME:
        raise AdapterError(f"program view ではありません: {spec.game}")
    return ProgramViewAdapter(g, paper_view_game_config(), spec)
