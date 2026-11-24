"""Program-view coordinator adapter for the PAPER trading corner (P0-3/P0-4).

A program view is NOT a game: it has no matches, no score, no agent, and no
round boundary. It reuses the CLI presentation path (owned tmux session +
private Xvfb + contained ffplay through presentation.py) so placement,
ownership, quiesce and rollback behave exactly like a CLI game switch, while
the adapter registry never resolves it from the games catalog.

The default dashboard is the read-only HTML/canvas page served on loopback and
shown in a chromium app window. Set ``[paper_corner] dashboard = "text"`` to
fall back to the 80x24 text dashboard in xterm without a code change.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..broadcast_view import (
    BROADCAST_TRANSLATE_ARGS,
    JAPANESE_LANG_ARG,
)
from ..config import GameAgentConfig, GameConfig, GameLifecycleConfig
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError
from .base import AdapterError
from .cli_game import CliCoordinatorAdapter

#: Reserved canonical name for the PAPER dashboard view.
PAPER_VIEW_NAME = "paper-view"

#: Marker the text dashboard renderer always prints; text readiness requires it.
VIEW_READY_MARKER = "PAPER 暗号資産コーナー"

#: Loopback port for the HTML dashboard server.
DEFAULT_DASHBOARD_PORT = 8799

#: ``src/`` of this checkout.  tmux builds a new session's environment from the
#: *server* and never inherits the operator's ``PYTHONPATH``, so any view whose
#: command imports ``docich`` must carry the import path through
#: ``_game_session_env`` (Issue #1992).
DOCICH_SRC = Path(__file__).resolve().parents[2]


def broadcast_browser_command(
    url: str,
    *,
    window_size: tuple[int, int],
    window_position: tuple[int, int],
    profile_dir: str | Path,
    japanese: bool = True,
    label: str = "docich broadcast view",
) -> list[str]:
    """Standard chromium command for one docich broadcast page.

    Every docich-opened broadcast window disables browser translation here
    instead of repeating the switch list in each view adapter (repository
    standard).  A view that serves a Japanese page passes ``japanese=True``
    (the default) so the UI language is pinned as well.
    """
    browser = _browser_bin()
    if not browser:
        raise AdapterError(f"chromium が見つかりません ({label})")
    command = [
        browser,
        f"--app={url}",
        f"--window-size={int(window_size[0])},{int(window_size[1])}",
        f"--window-position={int(window_position[0])},{int(window_position[1])}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    if japanese:
        command.append(JAPANESE_LANG_ARG)
    command.extend(BROADCAST_TRANSLATE_ARGS)
    command.append(f"--user-data-dir={profile_dir}")
    return command


def broadcast_view_session_env(*, capability_keys: tuple[str, ...] = ()) -> dict[str, str]:
    """Environment a generation-owned broadcast view needs in its own session.

    tmux builds a session environment from the server, so both the import path
    and any capability the view's own process must call have to be carried
    explicitly.  Keys absent from this process are omitted, never invented.
    """
    env = {"PYTHONPATH": str(DOCICH_SRC)}
    for key in capability_keys:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


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


def _paper_corner_section(g) -> dict:
    config_path = getattr(g, "config_path", None)
    if not config_path:
        return {}
    try:
        import tomllib

        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    section = data.get("paper_corner") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def _dashboard_kind(g) -> str:
    kind = str(_paper_corner_section(g).get("dashboard", "html")).strip().lower()
    return kind if kind in ("html", "text") else "html"


def _dashboard_port(g) -> int:
    value = _paper_corner_section(g).get("dashboard_port", DEFAULT_DASHBOARD_PORT)
    if type(value) is int and 1024 <= value <= 65535:
        return value
    return DEFAULT_DASHBOARD_PORT


def _browser_bin() -> str | None:
    # Ubuntu ships Chromium as a snap on many hosts. The owner-only VM gateway
    # intentionally runs with a narrow fixed PATH that excludes /snap/bin, so
    # check that one reviewed system path explicitly rather than widening PATH
    # or accepting a path from config/environment.
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "/snap/bin/chromium",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


def _browser_command(port: int) -> list[str]:
    return broadcast_browser_command(
        f"http://127.0.0.1:{port}/",
        window_size=(960, 540),
        window_position=(0, 0),
        profile_dir=f"/tmp/docich-paper-view-{port}",
        label="PAPER HTML dashboard",
    )


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
        self.dashboard_kind = _dashboard_kind(g)
        self.dashboard_port = _dashboard_port(g)

    def _game_command(self) -> list[str]:
        # State resolution stays explicit: a wrong profile must fail here,
        # not render another state_dir's numbers.
        base = [
            _docich_bin(), "--config", str(self.g.config_path),
            "trading", "--state-dir", str(self.g.state_dir / "trading"),
        ]
        if self.dashboard_kind == "html":
            return base + ["dashboard-server", "--host", "127.0.0.1", "--port", str(self.dashboard_port)]
        return base + ["dashboard-watch"]

    def _viewer_command(self) -> list[str]:
        browser = _browser_command(self.dashboard_port)
        d = self.g.display
        if d.viewport_width > 0 and d.viewport_height > 0:
            return [
                sys.executable, str(Path(__file__).resolve().parents[1] / "presentation.py"),
                "--display", d.name, "--title", f"docich-present-{self.spec.runtime_id}",
                "--x", str(d.viewport_x), "--y", str(d.viewport_y),
                "--width", str(d.viewport_width), "--height", str(d.viewport_height),
                "--", *browser,
            ]
        return browser

    def _xterm_command(self) -> list[str]:
        # The "game window" is the viewer shown on the stream; for the HTML
        # dashboard that is a browser, not xterm.
        if self.dashboard_kind == "html":
            return self._viewer_command()
        return super()._xterm_command()

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        self._game_command()
        if self.dashboard_kind == "html":
            if not _browser_bin():
                raise AdapterError(
                    "chromium が見つかりません "
                    '(PAPER HTML dashboard; [paper_corner] dashboard="text" で切替可)'
                )
        else:
            self._xterm_bin()
        if self.g.display.viewport_width > 0:
            for binary in ("Xvfb", "ffplay", "xdotool"):
                if not shutil.which(binary):
                    raise AdapterError(f"{binary} が見つかりません")
        self._check_active(deadline, cancel)

    def start_agent(self, deadline: float, cancel) -> None:
        raise AdapterError("program view has no agent")

    def stop_agent(self, deadline: float, cancel) -> None:
        return None

    def _dashboard_window_target(self) -> str:
        # The session birth window runs the dashboard; the game window only
        # shows the viewer. Name-based helpers cannot address it by index, so
        # resolve the one session window that is not the game window. Exactly
        # one must exist (agent is disabled for views).
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

    def _wait_server(self, deadline: float, cancel) -> None:
        url = f"http://127.0.0.1:{self.dashboard_port}/api/trading/dashboard"
        while True:
            self._check_active(deadline, cancel)
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if 200 <= response.status < 300:
                        return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("PAPER HTML dashboard server が応答しません")
            time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))

    def readiness(self, deadline: float, cancel) -> None:
        super().readiness(deadline, cancel)
        if self.dashboard_kind == "html":
            # The presenter window is up; make sure the local JSON server is
            # actually answering before committing to the view.
            self._wait_server(deadline, cancel)
            return
        # Text dashboard: the renderer process starts with the session and
        # needs a moment for interpreter startup, so poll for the marker.
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
