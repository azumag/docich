"""Browser adapters.

Legacy :class:`BrowserAdapter` renders in the fixed ``run/browser-profile`` for
the current CLI lifecycle.  :class:`BrowserCoordinatorAdapter` is the P2
runtime-aware adapter: a runtime-specific profile and DevTools debugging port
inside the runtime directory, and readiness confirmed via DevTools against the
expected URL (or an explicit probe for ``launch_command``) instead of a generic
window/screenshot check (design v2 §4.2).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import procs
from ..actions import Action
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..tmux import SESSION, Tmux, TmuxOwnership
from ..xkit import XKit
from .base import Adapter, AdapterError, Observation

BROWSER_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
DEFAULT_WINDOW_PATTERN = "Chrom"
DEVTOOLS_BASE_PORT = 9222
DEVTOOLS_PORT_RANGE = 1000
READY_POLL_S = 0.5


# --- shared [browser] helpers ----------------------------------------------


def browser_raw(game) -> dict:
    raw = game.raw.get("browser", {})
    if not isinstance(raw, dict):
        raise AdapterError("[browser] はテーブルである必要があります")
    return raw


def resolve_browser_binary(game) -> str:
    binary = browser_raw(game).get("binary", "auto") or "auto"
    if binary != "auto":
        return binary
    for name in BROWSER_CANDIDATES:
        found = procs.which(name)
        if found:
            return found
    raise AdapterError(
        "chromium が見つかりません。`sudo snap install chromium` を実行するか、"
        "soren の playwright chromium (~/.cache/ms-playwright/.../chrome-linux/chrome) の"
        "パスを [browser] binary に指定してください"
    )


def browser_launch_command(
    g,
    game,
    profile_dir: Path,
    *,
    debug_port: int | None = None,
    binary: str | None = None,
) -> list[str]:
    raw = browser_raw(game)
    launch_command = raw.get("launch_command")
    if launch_command:
        if not isinstance(launch_command, list) or not launch_command:
            raise AdapterError("[browser] launch_command は空でないリストである必要があります")
        return [str(c) for c in launch_command]

    if binary is None:
        binary = resolve_browser_binary(game)
    url = raw.get("url")
    if not url:
        raise AdapterError("[browser] に url が設定されていません (launch_command 未使用時は必須)")
    kiosk = raw.get("kiosk", True)
    extra_args = raw.get("extra_args", []) or []
    if not isinstance(extra_args, list):
        raise AdapterError("[browser] extra_args はリストである必要があります")

    d = g.display
    cmd = [
        binary,
        f"--window-size={d.width},{d.height}",
        "--window-position=0,0",
        "--no-first-run",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--autoplay-policy=no-user-gesture-required",
        f"--user-data-dir={profile_dir}",
        *[str(a) for a in extra_args],
    ]
    if debug_port is not None:
        cmd.append(f"--remote-debugging-port={debug_port}")
    if kiosk:
        cmd.append("--kiosk")
    cmd.append(url)
    return cmd


def browser_devtools_port(generation: int) -> int:
    """Generation-derived DevTools debugging port (no fixed-port collisions
    across runtimes)."""
    return DEVTOOLS_BASE_PORT + (generation % DEVTOOLS_PORT_RANGE)


def _docich_bin() -> str:
    return str(Path(__file__).resolve().parents[3] / "bin" / "docich")


class BrowserAdapter(Adapter):
    name = "browser"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._binary: str | None = None

    def _browser_raw(self) -> dict:
        return browser_raw(self.ctx.game)

    def _profile_dir(self) -> Path:
        return self.ctx.state.state_dir / "browser-profile"

    def _resolve_binary(self) -> str:
        if self._binary is not None:
            return self._binary
        self._binary = resolve_browser_binary(self.ctx.game)
        return self._binary

    # --- Adapter contract ------------------------------------------------

    def prepare(self) -> None:
        raw = self._browser_raw()
        if not raw.get("launch_command"):
            self._resolve_binary()
        self._profile_dir().mkdir(parents=True, exist_ok=True)

    def command(self) -> list[str]:
        return browser_launch_command(
            self.ctx.g, self.ctx.game, self._profile_dir(), binary=self._binary
        )

    def observe(self) -> Observation:
        d = self.ctx.g.display
        out_path = self.ctx.state.screenshots_dir / "latest.png"
        result = self.ctx.xkit.screenshot(out_path, d.width, d.height)
        return Observation(
            game=self.ctx.game.name,
            title=self.ctx.game.title,
            adapter=self.name,
            ts=time.time(),
            kind="screenshot",
            screenshot=str(result),
        )

    def _focus(self) -> None:
        pattern = self._browser_raw().get("window_pattern", DEFAULT_WINDOW_PATTERN)
        window_id = self.ctx.xkit.find_window(pattern)
        if window_id is None:
            print(
                f"docich: 警告: '{pattern}' ウィンドウが見つかりません (フォーカスをスキップします)",
                file=sys.stderr,
            )
            return
        self.ctx.xkit.focus(window_id)

    def act(self, action: Action) -> None:
        if action.type == "key":
            self._focus()
            self.ctx.xkit.tap(action.keys, action.hold_ms)
            return
        if action.type == "text":
            self._focus()
            self.ctx.xkit.type_text(action.text)
            return
        if action.type == "mouse":
            self.ctx.xkit.mouse_click(action.x, action.y, action.button)
            return
        if action.type == "wait":
            return
        raise AdapterError(f"browser アダプタは action type '{action.type}' に対応していません")


class BrowserCoordinatorAdapter:
    """P2 runtime-aware browser adapter (design v2 §4.2).

    One instance is bound to exactly one runtime.  It launches the browser in
    the runtime's game window with a runtime-specific profile and DevTools
    debugging port, and confirms readiness against the expected URL (DevTools)
    or an explicit probe for ``launch_command``, never by a generic Chromium
    window or screenshot.
    """

    name = "browser"

    def __init__(self, g, game, spec: RuntimeSpec, *, xkit=None):
        self.g = g
        self.game = game
        self.spec = spec
        self.tmux = Tmux()
        self.xkit = xkit if xkit is not None else XKit(g.display.name)
        self.agent_enabled = game.agent.enabled

    def _ownership(self, role: str) -> TmuxOwnership:
        return TmuxOwnership(
            runtime_id=self.spec.runtime_id,
            generation=self.spec.generation,
            role=role,
        )

    def _check_active(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            raise DeadlineExceededError("adapter call はcancelされました")
        if time.monotonic() >= deadline:
            raise DeadlineExceededError("adapter call のdeadlineを超過しました")

    def _game_window_target(self) -> str:
        return f"{SESSION}:{self.spec.game_window}"

    def _agent_window_target(self) -> str:
        return f"{SESSION}:{self.spec.agent_window}"

    def _profile_dir(self) -> Path:
        return self.spec.runtime_dir / "browser-profile"

    def _debug_port(self) -> int:
        return browser_devtools_port(self.spec.generation)

    def _verify_window_ownership(self, target: str, role: str) -> None:
        expected = self._ownership(role)
        actual = self.tmux.read_window_ownership(target)
        if actual != expected:
            raise AdapterError(
                f"window ownershipが一致しません (expected={expected}, actual={actual})"
            )

    def _uses_launch_command(self) -> bool:
        return bool(browser_raw(self.game).get("launch_command"))

    def _agent_command(self) -> list[str]:
        return [
            _docich_bin(), "--config", str(self.g.config_path),
            "run", "agent", self.spec.game,
        ]

    # --- CoordinatorAdapter contract -------------------------------------

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        raw = browser_raw(self.game)
        if not raw.get("launch_command"):
            resolve_browser_binary(self.game)
            if not raw.get("url"):
                raise AdapterError("[browser] に url が設定されていません (launch_command 未使用時は必須)")
        if self.agent_enabled and not Path(_docich_bin()).is_file():
            raise AdapterError(f"docich executable が見つかりません: {_docich_bin()}")
        self._check_active(deadline, cancel)

    def materialize_runtime(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        self._profile_dir().mkdir(parents=True, exist_ok=True)
        self._check_active(deadline, cancel)

        target = self._game_window_target()
        if self.tmux.window_target_exists(target):
            self._verify_window_ownership(target, "game")
            return
        self._check_active(deadline, cancel)
        debug_port = None if self._uses_launch_command() else self._debug_port()
        command = browser_launch_command(self.g, self.game, self._profile_dir(), debug_port=debug_port)
        self.tmux.create_window_owned(self.spec.game_window, command, self._ownership("game"))
        self._check_active(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._game_window_target()
        if not self.tmux.window_target_exists(target):
            raise ReadinessTimeoutError("game windowがありません")
        self._verify_window_ownership(target, "game")
        states = self.tmux.pane_states_checked(target)
        if any(pane.dead for pane in states):
            raise ReadinessTimeoutError("game paneがdeadです")
        if self._uses_launch_command():
            self._probe_launch_command(deadline, cancel)
        else:
            self._probe_expected_url(deadline, cancel)

    def _devtools_pages(self) -> list[dict]:
        url = f"http://127.0.0.1:{self._debug_port()}/json"
        with urllib.request.urlopen(url, timeout=1.0) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return data if isinstance(data, list) else []

    def _probe_expected_url(self, deadline: float, cancel) -> None:
        expected = str(browser_raw(self.game).get("url") or "")
        while True:
            if cancel is not None and cancel.is_set():
                raise ReadinessTimeoutError("adapter call はcancelされました")
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("DevTools で期待URLのページを確認できません")
            try:
                pages = self._devtools_pages()
            except (urllib.error.URLError, OSError, ValueError):
                pages = []
            if any(expected in str(page.get("url", "")) for page in pages):
                return
            time.sleep(READY_POLL_S)

    def _probe_launch_command(self, deadline: float, cancel) -> None:
        probe = browser_raw(self.game).get("readiness_probe") or {"type": "process"}
        if not isinstance(probe, dict):
            raise AdapterError("[browser] readiness_probe はテーブルである必要があります")
        probe_type = probe.get("type", "process")
        if probe_type == "process":
            return  # 既に pane 生存を確認済み
        if probe_type == "window":
            pattern = str(probe.get("window_pattern") or DEFAULT_WINDOW_PATTERN)
            while True:
                if cancel is not None and cancel.is_set():
                    raise ReadinessTimeoutError("adapter call はcancelされました")
                if time.monotonic() >= deadline:
                    raise ReadinessTimeoutError("期待するwindowが見つかりません")
                if self.xkit.find_window(pattern) is not None:
                    return
                time.sleep(READY_POLL_S)
        if probe_type == "http":
            target = str(probe.get("url") or "")
            if not target:
                raise AdapterError("[browser] readiness_probe.http に url が必要です")
            while True:
                if cancel is not None and cancel.is_set():
                    raise ReadinessTimeoutError("adapter call はcancelされました")
                if time.monotonic() >= deadline:
                    raise ReadinessTimeoutError(f"http probe が応答しません: {target}")
                try:
                    with urllib.request.urlopen(target, timeout=1.0) as response:
                        if 200 <= response.status < 300:
                            return
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(READY_POLL_S)
        if probe_type == "argv":
            argv = probe.get("command")
            if not isinstance(argv, list) or not argv:
                raise AdapterError("[browser] readiness_probe.argv に command リストが必要です")
            while True:
                if cancel is not None and cancel.is_set():
                    raise ReadinessTimeoutError("adapter call はcancelされました")
                if time.monotonic() >= deadline:
                    raise ReadinessTimeoutError("argv probe が成功しません")
                result = procs.run([str(c) for c in argv])
                if result.returncode == 0:
                    return
                time.sleep(READY_POLL_S)
        raise AdapterError(f"[browser] readiness_probe.type が不正です: {probe_type}")

    def alive(self, deadline: float, cancel) -> bool:
        self._check_active(deadline, cancel)
        target = self._game_window_target()
        if not self.tmux.window_target_exists(target):
            return False
        self._verify_window_ownership(target, "game")
        states = self.tmux.pane_states_checked(target)
        return not any(pane.dead for pane in states)

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        for name, role in (
            (self.spec.agent_window, "agent"),
            (self.spec.game_window, "game"),
        ):
            self._check_active(deadline, cancel)
            target = f"{SESSION}:{name}"
            if self.tmux.window_target_exists(target):
                self._check_active(deadline, cancel)
                self.tmux.kill_window_owned(target, self._ownership(role))

    def start_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._verify_window_ownership(target, "agent")
            return
        self._check_active(deadline, cancel)
        self.tmux.create_window_owned(
            self.spec.agent_window,
            self._agent_command(),
            self._ownership("agent"),
        )
        self._check_active(deadline, cancel)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._check_active(deadline, cancel)
            self.tmux.kill_window_owned(target, self._ownership("agent"))
