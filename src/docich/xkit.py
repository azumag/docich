"""X11 input/output helpers: xdotool input injection and ffmpeg screenshots."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from . import procs


class XKit:
    def __init__(self, display: str):
        self.display = display

    def _env(self) -> dict:
        return {"DISPLAY": self.display}

    def display_ready(self) -> bool:
        r = procs.run(["xdpyinfo", "-display", self.display], env_extra=self._env())
        return r.returncode == 0

    def wait_display(self, timeout_s: float = 15) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.display_ready():
                return True
            time.sleep(0.5)
        return self.display_ready()

    def screenshot(self, out_path: Path, width: int, height: int) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(out_path.parent), prefix=f".{out_path.name}.", suffix=".tmp"
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            procs.run(
                [
                    "ffmpeg", "-loglevel", "error", "-y",
                    # -draw_mouse 0 が無いとマウスポインタが画面中央に映り込む (architecture.md §5)
                    "-f", "x11grab", "-draw_mouse", "0",
                    "-video_size", f"{width}x{height}", "-i", self.display,
                    "-frames:v", "1",
                    # 出力先は拡張子の無い一時ファイル名なので、ffmpeg のフォーマット
                    # 自動判定 (ファイル名の拡張子依存) に頼らず明示指定する。
                    "-c:v", "png", "-f", "image2",
                    str(tmp_path),
                ],
                env_extra=self._env(),
                check=True,
            )
            os.replace(tmp_path, out_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return out_path

    def find_window(self, pattern: str) -> str | None:
        r = procs.run(
            ["xdotool", "search", "--onlyvisible", "--name", pattern], env_extra=self._env()
        )
        if r.returncode != 0:
            return None
        lines = [line for line in r.stdout.splitlines() if line.strip()]
        return lines[0] if lines else None

    def focus(self, window_id: str) -> None:
        # WM の無い Xvfb では windowactivate (EWMH 依存) が機能しないため、
        # XSetInputFocus を直接叩く windowfocus --sync を主とする (architecture.md §9.2)。
        r = procs.run(
            ["xdotool", "windowfocus", "--sync", window_id], env_extra=self._env()
        )
        if r.returncode == 0:
            return
        r2 = procs.run(
            ["xdotool", "windowactivate", "--sync", window_id], env_extra=self._env()
        )
        if r2.returncode != 0:
            print(f"docich: ウィンドウのフォーカス取得に失敗しました (id={window_id})", file=sys.stderr)

    def keydown(self, keys: list[str]) -> None:
        procs.run(["xdotool", "keydown", "--delay", "0", *keys], env_extra=self._env())

    def keyup(self, keys: list[str]) -> None:
        procs.run(["xdotool", "keyup", "--delay", "0", *keys], env_extra=self._env())

    def tap(self, keys: list[str], hold_ms: int = 100) -> None:
        self.keydown(keys)
        time.sleep(max(hold_ms, 0) / 1000)
        self.keyup(list(reversed(keys)))

    def type_text(self, text: str) -> None:
        procs.run(["xdotool", "type", "--delay", "50", text], env_extra=self._env())

    def mouse_click(self, x: int, y: int, button: int = 1) -> None:
        procs.run(
            ["xdotool", "mousemove", str(x), str(y), "click", str(button)],
            env_extra=self._env(),
        )
