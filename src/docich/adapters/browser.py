"""Browser adapter: launches a browser (kiosk) and projects it on the shared
X11 display (architecture.md §4.3). The real soren game automation lives in
the soren repo; docich here only provides "a place to render" (launch + generic
key/mouse input)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .. import procs
from ..actions import Action
from .base import Adapter, AdapterError, Observation

BROWSER_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
DEFAULT_WINDOW_PATTERN = "Chrom"


class BrowserAdapter(Adapter):
    name = "browser"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._binary: str | None = None

    def _browser_raw(self) -> dict:
        raw = self.ctx.game.raw.get("browser", {})
        if not isinstance(raw, dict):
            raise AdapterError("[browser] はテーブルである必要があります")
        return raw

    def _profile_dir(self) -> Path:
        return self.ctx.state.state_dir / "browser-profile"

    def _resolve_binary(self) -> str:
        binary = self._browser_raw().get("binary", "auto") or "auto"
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

    # --- Adapter contract ------------------------------------------------

    def prepare(self) -> None:
        raw = self._browser_raw()
        launch_command = raw.get("launch_command")
        if not launch_command:
            self._binary = self._resolve_binary()
        self._profile_dir().mkdir(parents=True, exist_ok=True)

    def command(self) -> list[str]:
        raw = self._browser_raw()
        launch_command = raw.get("launch_command")
        if launch_command:
            if not isinstance(launch_command, list) or not launch_command:
                raise AdapterError("[browser] launch_command は空でないリストである必要があります")
            return [str(c) for c in launch_command]

        binary = self._binary or self._resolve_binary()
        url = raw.get("url")
        if not url:
            raise AdapterError("[browser] に url が設定されていません (launch_command 未使用時は必須)")
        kiosk = raw.get("kiosk", True)
        extra_args = raw.get("extra_args", []) or []
        if not isinstance(extra_args, list):
            raise AdapterError("[browser] extra_args はリストである必要があります")

        d = self.ctx.g.display
        cmd = [
            binary,
            f"--window-size={d.width},{d.height}",
            "--window-position=0,0",
            "--no-first-run",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--autoplay-policy=no-user-gesture-required",
            f"--user-data-dir={self._profile_dir()}",
            *[str(a) for a in extra_args],
        ]
        if kiosk:
            cmd.append("--kiosk")
        cmd.append(url)
        return cmd

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
