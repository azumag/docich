"""Program-view coordinator adapter for the PAPER trading corner (P0-3).

A program view is NOT a game: it has no matches, no score, no agent, and no
round boundary. It reuses the CLI presentation path (owned tmux session +
xterm viewer through presentation.py) so placement, ownership, quiesce and
rollback behave exactly like a CLI game switch, while ``make_coordinator_
adapter`` never resolves it from the games catalog.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError
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

    def _dashboard_window_target(self) -> str:
        # The session birth window runs dashboard-watch; the game window only
        # shows the xterm viewer. Name-based helpers cannot address it by
        # index, so resolve the one session window that is not the game
        # window. Exactly one must exist (agent is disabled for views).
        from .base import AdapterError as _AdapterError

        try:
            names = self.tmux.list_windows()
        except Exception as exc:
            raise _AdapterError(f"program view の window 一覧を取得できません: {exc}") from exc
        candidates = [name for name in names if name != self.spec.game_window]
        if len(candidates) != 1:
            from .cli_game import ReadinessTimeoutError

            raise ReadinessTimeoutError(
                f"program view の dashboard window を特定できません: {candidates}")
        return f"{self.spec.adapter_session}:{candidates[0]}"

    def readiness(self, deadline: float, cancel) -> None:
        super().readiness(deadline, cancel)
        # The dashboard process starts with the session and needs a moment
        # for interpreter startup: poll for the marker instead of checking
        # once, mirroring the presenter poll above.
        target = self._dashboard_window_target()
        while True:
            try:
                if not self.tmux.window_target_exists(target):
                    raise ReadinessTimeoutError("program view の dashboard window がありません")
                text = self.tmux.capture_pane_checked(target)
            except ReadinessTimeoutError:
                raise
            except Exception as exc:
                raise AdapterError(f"program view の pane を取得できません: {exc}") from exc
            if VIEW_READY_MARKER in (text or ""):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("program view にダッシュボードが表示されません")
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("adapter call はcancelされました")
            time.sleep(min(2.0, remaining))


def make_program_view_adapter(g, spec):
    """Build the PAPER view adapter. Only the reserved view name resolves."""
    if spec.game != PAPER_VIEW_NAME:
        raise AdapterError(f"program view ではありません: {spec.game}")
    return ProgramViewAdapter(g, paper_view_game_config(), spec)
