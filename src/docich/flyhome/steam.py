"""Steam のインストール先・ライブラリ・ゲーム導入状況の検出と起動。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Valve KeyValues (libraryfolders.vdf / appmanifest_*.acf) の最小パーサ。"""
    tokens = [(m.group(1), m.group(2)) for m in _TOKEN.finditer(text)]
    pos = 0

    def parse_obj() -> dict:
        nonlocal pos
        obj: dict = {}
        while pos < len(tokens):
            s, brace = tokens[pos]
            if brace == "}":
                pos += 1
                return obj
            key = s.replace("\\\\", "\\")
            pos += 1
            if pos >= len(tokens):
                break
            s2, brace2 = tokens[pos]
            if brace2 == "{":
                pos += 1
                obj[key] = parse_obj()
            else:
                obj[key] = (s2 or "").replace("\\\\", "\\")
                pos += 1
        return obj

    return parse_obj()


def steam_root() -> Path | None:
    if sys.platform == "win32":  # pragma: no cover - Windows のみ
        import winreg

        for hive, sub, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    p = Path(winreg.QueryValueEx(k, name)[0])
                    if p.is_dir():
                        return p
            except OSError:
                continue
        default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
        return default if default.is_dir() else None
    for p in (Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"):
        if p.is_dir():
            return p
    return None


def library_dirs(root: Path) -> list[Path]:
    vdf = root / "steamapps" / "libraryfolders.vdf"
    dirs = [root]
    if vdf.is_file():
        data = parse_vdf(vdf.read_text(encoding="utf-8", errors="replace"))
        folders = data.get("libraryfolders", data.get("LibraryFolders", {}))
        for v in folders.values():
            path = v.get("path") if isinstance(v, dict) else v
            if path and Path(path) not in dirs:
                dirs.append(Path(path))
    return dirs


@dataclass
class AppInstall:
    app_id: int
    name: str
    install_dir: Path
    state_flags: int
    build_id: str
    exes: list[Path]

    @property
    def fully_installed(self) -> bool:
        return bool(self.state_flags & 4)


def find_app(app_id: int, root: Path | None = None) -> AppInstall | None:
    root = root or steam_root()
    if root is None:
        return None
    for lib in library_dirs(root):
        acf = lib / "steamapps" / f"appmanifest_{app_id}.acf"
        if not acf.is_file():
            continue
        st = parse_vdf(acf.read_text(encoding="utf-8", errors="replace")).get("AppState", {})
        inst = lib / "steamapps" / "common" / st.get("installdir", "")
        exes = sorted(inst.glob("*.exe")) if inst.is_dir() else []
        return AppInstall(app_id, st.get("name", ""), inst, int(st.get("StateFlags", "0") or 0), st.get("buildid", ""), exes)
    return None


def open_url(url: str) -> None:
    """steam:// URL を開く (Steam クライアントが処理する)。"""
    if sys.platform == "win32":  # pragma: no cover
        os.startfile(url)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])


def launch(app_id: int) -> None:
    open_url(f"steam://rungameid/{app_id}")


def install(app_id: int) -> None:
    open_url(f"steam://install/{app_id}")
