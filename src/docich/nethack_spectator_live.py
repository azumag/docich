"""Live, viewer-only NetHack spectator sidecar.

The sidecar reads the canonical GameSwitchStore to discover the currently
committed NetHack runtime, verifies the generation-owned tmux game window, and
captures that window read-only. It never sends input and never participates in
save/resume or agent observation.

The rendered HTML is atomically replaced so an OBS Browser Source never reads a
partial document. The document reloads itself periodically, which makes a local
file Browser Source update without requiring another HTTP service.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adapters.cli_game import cli_cols, cli_rows
from .config import ConfigError, load_game, load_global
from .game_switch import GameSwitchError, GameSwitchStore, atomic_write_json
from .nethack_spectator import VISUAL_MODES, blank_frame, parse_tty, render_html
from .tmux import Tmux, TmuxError, TmuxOwnership

GAME_NAME = "nethack"
DEFAULT_INTERVAL_MS = 500
DEFAULT_REFRESH_MS = 750
DEFAULT_VISUAL_MODE = "tiles"


class NethackSpectatorLiveError(RuntimeError):
    """The spectator could not safely identify or capture the active runtime."""


@dataclass(frozen=True)
class ActiveRuntime:
    runtime_id: str
    generation: int
    adapter_session: str
    game_window: str

    @property
    def target(self) -> str:
        return f"{self.adapter_session}:{self.game_window}"

    @property
    def ownership(self) -> TmuxOwnership:
        return TmuxOwnership(
            runtime_id=self.runtime_id,
            generation=self.generation,
            role="game",
        )


def active_nethack_runtime(state: dict[str, object]) -> ActiveRuntime | None:
    """Return only a committed NetHack runtime.

    Transitional coordinator phases are deliberately rendered as standby. This
    keeps the spectator from following candidate/previous/retiring windows and
    makes presentation obey the same canonical commit boundary as gameplay.
    """
    if state.get("phase") != "ready":
        return None
    active = state.get("active")
    if not isinstance(active, dict) or active.get("game") != GAME_NAME:
        return None

    runtime_id = active.get("runtime_id")
    generation = active.get("generation")
    adapter_session = active.get("adapter_session")
    game_window = active.get("game_window")
    if (
        not isinstance(runtime_id, str)
        or not runtime_id
        or type(generation) is not int
        or generation < 1
        or not isinstance(adapter_session, str)
        or not adapter_session
        or not isinstance(game_window, str)
        or not game_window
    ):
        raise NethackSpectatorLiveError("active NetHack runtime identity is incomplete")
    return ActiveRuntime(
        runtime_id=runtime_id,
        generation=generation,
        adapter_session=adapter_session,
        game_window=game_window,
    )


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a public presentation file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        dir_fd = os.open(path.parent, flags)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _error_detail(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:240]


class LiveNethackSpectator:
    """Continuously mirror the committed NetHack TTY into spectator HTML."""

    def __init__(
        self,
        *,
        state_dir: Path,
        output: Path,
        cols: int = 80,
        rows: int = 24,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        refresh_ms: int = DEFAULT_REFRESH_MS,
        visual_mode: str = DEFAULT_VISUAL_MODE,
        state_loader: Callable[[], dict[str, object]] | None = None,
        tmux_factory: Callable[[str], object] = Tmux,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        if type(cols) is not int or cols < 1:
            raise NethackSpectatorLiveError("cols must be a positive integer")
        if type(rows) is not int or rows < 3:
            raise NethackSpectatorLiveError("rows must be an integer >= 3")
        if type(interval_ms) is not int or not 100 <= interval_ms <= 60_000:
            raise NethackSpectatorLiveError("interval_ms must be between 100 and 60000")
        if type(refresh_ms) is not int or not 100 <= refresh_ms <= 60_000:
            raise NethackSpectatorLiveError("refresh_ms must be between 100 and 60000")
        if visual_mode not in VISUAL_MODES:
            raise NethackSpectatorLiveError(
                f"visual_mode must be one of {sorted(VISUAL_MODES)}"
            )

        self.state_dir = Path(state_dir)
        self.output = Path(output)
        self.status_path = self.output.parent / "status.json"
        self.cols = cols
        self.rows = rows
        self.interval_ms = interval_ms
        self.refresh_ms = refresh_ms
        self.visual_mode = visual_mode
        self._tmux_factory = tmux_factory
        self._sleep = sleep
        self._now = now
        if state_loader is None:
            store = GameSwitchStore(self.state_dir)
            self._state_loader = lambda: store.canonical.load()[0]
        else:
            self._state_loader = state_loader

    def _standby_html(self, message: str = "NetHackコーナー待機中です。") -> str:
        return render_html(
            blank_frame(message, cols=self.cols, rows=self.rows),
            title="NetHack",
            auto_refresh_ms=self.refresh_ms,
            visual_mode=self.visual_mode,
        )

    def _write_status(
        self,
        status: str,
        *,
        runtime: ActiveRuntime | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": status,
            "visual_mode": self.visual_mode,
            "updated_at": self._now(),
        }
        if runtime is not None:
            payload.update(
                {
                    "runtime_id": runtime.runtime_id,
                    "generation": runtime.generation,
                }
            )
        if error:
            payload["error"] = error
        atomic_write_json(self.status_path, payload)

    def _capture(self, runtime: ActiveRuntime) -> str:
        tmux = self._tmux_factory(runtime.adapter_session)
        actual = tmux.read_window_ownership(runtime.target)
        if actual != runtime.ownership:
            raise NethackSpectatorLiveError(
                "active NetHack game window ownership does not match canonical runtime"
            )
        return tmux.capture_pane_checked(runtime.target)

    def render_once(self) -> str:
        """Render one snapshot and return active/idle/degraded."""
        runtime: ActiveRuntime | None = None
        try:
            state = self._state_loader()
            runtime = active_nethack_runtime(state)
            if runtime is None:
                atomic_write_text(self.output, self._standby_html())
                self._write_status("idle")
                return "idle"

            text = self._capture(runtime)
            frame = parse_tty(text, cols=self.cols, rows=self.rows)
            atomic_write_text(
                self.output,
                render_html(
                    frame,
                    title="NetHack — AI、ダンジョンに潜る",
                    auto_refresh_ms=self.refresh_ms,
                    visual_mode=self.visual_mode,
                ),
            )
            self._write_status("active", runtime=runtime)
            return "active"
        except (GameSwitchError, TmuxError, NethackSpectatorLiveError, OSError, ValueError) as exc:
            detail = _error_detail(exc)
            print(f"docich-nethack-spectator: {detail}", file=sys.stderr, flush=True)
            if not self.output.exists():
                try:
                    atomic_write_text(
                        self.output,
                        self._standby_html("NetHack画面を準備しています。"),
                    )
                except OSError:
                    pass
            try:
                self._write_status("degraded", runtime=runtime, error=detail)
            except OSError:
                pass
            return "degraded"

    def run_forever(self) -> None:
        while True:
            self.render_once()
            self._sleep(self.interval_ms / 1000.0)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-spectator-live")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--output", metavar="PATH")
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    parser.add_argument("--refresh-ms", type=int, default=DEFAULT_REFRESH_MS)
    parser.add_argument("--visual-mode", choices=sorted(VISUAL_MODES), default=DEFAULT_VISUAL_MODE)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        game = load_game(g, GAME_NAME)
        output = (
            Path(args.output)
            if args.output
            else Path(g.state_dir) / "nethack" / "spectator" / "index.html"
        )
        spectator = LiveNethackSpectator(
            state_dir=Path(g.state_dir),
            output=output,
            cols=cli_cols(game),
            rows=cli_rows(game),
            interval_ms=args.interval_ms,
            refresh_ms=args.refresh_ms,
            visual_mode=args.visual_mode,
        )
        if args.once:
            return 0 if spectator.render_once() != "degraded" else 1
        spectator.run_forever()
        return 0
    except (ConfigError, NethackSpectatorLiveError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
