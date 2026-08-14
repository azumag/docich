"""run/ state directory management (current game, logs, screenshots, ...)."""
from __future__ import annotations

from pathlib import Path

from .config import GlobalConfig

CURRENT_GAME_FILE = "current_game"


class State:
    def __init__(self, g: GlobalConfig):
        self.g = g

    @property
    def state_dir(self) -> Path:
        return self.g.state_dir

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def screenshots_dir(self) -> Path:
        return self.state_dir / "screenshots"

    @property
    def retroarch_dir(self) -> Path:
        return self.state_dir / "retroarch"

    def ensure(self) -> None:
        for d in (self.state_dir, self.logs_dir, self.screenshots_dir, self.retroarch_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _current_game_path(self) -> Path:
        return self.state_dir / CURRENT_GAME_FILE

    def current_game(self) -> str | None:
        path = self._current_game_path()
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def set_current_game(self, name: str) -> None:
        self.ensure()
        self._current_game_path().write_text(f"{name}\n", encoding="utf-8")

    def clear_current_game(self) -> None:
        path = self._current_game_path()
        if path.is_file():
            path.unlink()
