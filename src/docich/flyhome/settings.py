"""Fly Me To The Home! ランナーの設定。

既定値は Steam ストアのスクリーンショット (1728x1080 中の 16:9 領域) を実測して決めた。
Windows 実機で違いがあれば ``config/windows/fly-me-to-the-home.toml`` で上書きする。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from . import APP_ID, DEMO_APP_ID

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "config" / "windows" / "fly-me-to-the-home.toml"

# Rect は「ゲーム描画領域を 0..1 に正規化した座標」(x0, y0, x1, y1)。
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Keys:
    """キーボードのスキャンコード (Set 1)。矢印キーは拡張キー (E0 プレフィクス)。

    ゲーム内の案内表示から確認済み: ←/→ = 左右ジェット、Enter = リトライ/決定、
    Esc = マップへ戻る、Space = クリア画面の追加操作 (内容は実機で要確認)。
    """

    left: int = 0x4B
    right: int = 0x4D
    up: int = 0x48
    down: int = 0x50
    enter: int = 0x1C
    escape: int = 0x01
    space: int = 0x39
    extended: tuple[str, ...] = ("left", "right", "up", "down")


@dataclass(frozen=True)
class Hud:
    level_text: Rect = (0.03, 0.0, 0.20, 0.07)
    timer: Rect = (0.73, 0.0, 1.0, 0.09)
    arrow_left: Rect = (0.862, 0.875, 0.918, 0.975)
    arrow_right: Rect = (0.928, 0.875, 0.984, 0.975)
    # 死亡/クリア時に左上へ吊り下がる木の看板 (Enter/ESC/SPACE の案内)。
    sign: Rect = (0.0, 0.05, 0.21, 0.38)


@dataclass(frozen=True)
class Settings:
    app_id: int = APP_ID
    demo_app_id: int = DEMO_APP_ID
    use_demo: bool = False
    window_titles: tuple[str, ...] = ("Fly Me To The Home",)
    exe_names: tuple[str, ...] = ()
    native_width: int = 320
    native_height: int = 180
    aspect: tuple[int, int] = (16, 9)
    tick_hz: float = 30.0
    capture_mode: str = "screen"  # "screen" (BitBlt) | "printwindow"
    input_mode: str = "sendinput"  # "sendinput" | "postmessage"
    abort_vk: int = 0x7B  # F12: 押すと即座に全キーを離して停止する
    keys: Keys = field(default_factory=Keys)
    hud: Hud = field(default_factory=Hud)
    run_dir: Path = REPO_ROOT / "run" / "flyhome"
    # 死亡/クリア後に Enter を送るまでの待ち (演出が終わる前の入力は無視されやすい)。
    retry_delay_s: float = 0.8
    max_attempts: int = 0  # 0 = 無制限
    # 物理パラメータ (calibrate で上書きする)。単位はネイティブ px と秒。
    physics_file: Path = REPO_ROOT / "run" / "flyhome" / "physics.json"

    @property
    def active_app_id(self) -> int:
        return self.demo_app_id if self.use_demo else self.app_id


def _coerce(cls, raw: dict):
    kwargs = {}
    names = {f.name: f for f in fields(cls)}
    for key, value in raw.items():
        if key not in names:
            raise ValueError(f"unknown setting: {cls.__name__}.{key}")
        if isinstance(value, list):
            value = tuple(value)
        kwargs[key] = value
    return kwargs


def load(path: Path | None = None) -> Settings:
    """TOML があれば読み込んで既定値を上書きする。無ければ既定値。"""
    cfg_path = path or DEFAULT_CONFIG
    base = Settings()
    if not cfg_path.is_file():
        if path is not None:
            raise FileNotFoundError(cfg_path)
        return base
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    keys = replace(base.keys, **_coerce(Keys, raw.pop("keys", {})))
    hud = replace(base.hud, **_coerce(Hud, raw.pop("hud", {})))
    top = _coerce(Settings, raw)
    for name in ("run_dir", "physics_file"):
        if name in top:
            p = Path(top[name])
            top[name] = p if p.is_absolute() else REPO_ROOT / p
    return replace(base, keys=keys, hud=hud, **top)
