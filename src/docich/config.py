"""Dataclasses and TOML loader for docich configuration (architecture.md §8)."""
from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .naming import NameValidationError, ensure_contained, validate_game_name

STREAM_MODES = ("null", "rtmp", "file")


class ConfigError(Exception):
    """Raised for invalid or missing docich configuration."""


@dataclass
class DisplayConfig:
    # 既定 :98。soren が :99 を使用中のため衝突を避ける (architecture.md §0)。
    number: int = 98
    width: int = 1280
    height: int = 720
    color_depth: int = 24
    # false は既存のX displayへ接続する。docichは起動・停止しない。
    managed: bool = True
    # 外部合成レイアウト内でゲームwindowだけを置く矩形。0は未指定。
    viewport_x: int = 0
    viewport_y: int = 0
    viewport_width: int = 0
    viewport_height: int = 0

    @property
    def name(self) -> str:
        return f":{self.number}"


@dataclass
class AudioConfig:
    enabled: bool = True
    sink_name: str = "docich_sink"
    # true にしない限り set-default-sink はしない (soren 共存。architecture.md §0)。
    set_default: bool = False


@dataclass
class StreamConfig:
    ffmpeg_bin: str = "ffmpeg"
    mode: str = "null"
    rtmp_url: str = "rtmp://live.twitch.tv/app"
    stream_key_env: str = "DOCICH_STREAM_KEY"
    file_path: str = "run/out.flv"
    framerate: int = 30
    video_bitrate: str = "4500k"
    maxrate: str = "5000k"
    bufsize: str = "9000k"
    preset: str = "veryfast"
    audio_bitrate: str = "160k"
    gop_seconds: int = 2
    # --- C3 overlay: drawtext フック (空なら無効) ---
    overlay_text_file: str = ""
    overlay_font: str = "sans"
    overlay_fontsize: int = 24
    overlay_x: str = "20"
    overlay_y: str = "20"


@dataclass
class CaptionConfig:
    enabled: bool = False
    socket_path: str = ""


@dataclass
class AgentDefaults:
    default_interval_ms: int = 2000
    brain_timeout_s: int = 120


@dataclass
class WatchdogConfig:
    # 既定は無効 (Phase 3 の付加機能)。フリーズ検知の条件は watchdog.py の
    # run_watchdog / docstring を参照 (agent window 稼働中のみ判定する)。
    enabled: bool = False
    interval_s: int = 60  # 点検周期 (秒)
    freeze_cycles: int = 5  # 連続同一スクリーンショットでフリーズ判定する回数
    recover_windows: bool = True  # display/audio/stream window 消失時に up 相当で再生成する


@dataclass
class RotationConfig:
    # `docich rotate` が巡回するゲーム名の順序 (例: ["nethack", "hanjuku-hero"])。
    # 空のままだと `docich rotate` は ConfigError ではなく CliError で止まる (cli.py)。
    games: list = field(default_factory=list)


@dataclass
class WebUIConfig:
    # Tailscale 経由の管理UI。既定は 127.0.0.1 バインドで tailscale serve で公開する。
    # 起動は手動 (`docich webui`) または systemd (scripts/systemd/docich-webui.service)。
    bind: str = "127.0.0.1"
    port: int = 8787
    soren_root: str = ""
    token: str = ""
    token_env: str = "DOCICH_WEBUI_TOKEN"
    allow_cors: bool = False
    read_only: bool = False


@dataclass
class GlobalConfig:
    repo_root: Path
    config_path: Path
    display: DisplayConfig
    audio: AudioConfig
    stream: StreamConfig
    captions: CaptionConfig
    agent: AgentDefaults
    watchdog: WatchdogConfig
    rotation: RotationConfig
    webui: WebUIConfig
    state_dir: Path
    games_dir: Path
    roms_dir: Path


@dataclass
class GameAgentConfig:
    enabled: bool = False
    brain: str = "command"
    command: str | list[str] = ""
    interval_ms: int | None = None


@dataclass
class GameConfig:
    name: str
    title: str
    adapter: str
    submodule: str = ""
    raw: dict = field(default_factory=dict)
    agent: GameAgentConfig = field(default_factory=GameAgentConfig)
    path: Path = field(default_factory=Path)


def _filtered(cls, raw, table_name: str) -> dict:
    """Keep only keys that are known dataclass fields of cls (forward-compatible)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"[{table_name}] はテーブルである必要があります")
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in valid}


def _config_search_path(repo_root: Path, config_path: Path | None) -> Path:
    if config_path is not None:
        return Path(config_path)
    env_value = os.environ.get("DOCICH_CONFIG")
    if env_value:
        return Path(env_value)
    return repo_root / "config" / "docich.toml"


def _load_toml_file(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML の構文解析に失敗しました: {path} ({exc})") from exc
    except OSError as exc:
        raise ConfigError(f"設定ファイルを読み込めません: {path} ({exc})") from exc


def _abs_path(repo_root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (repo_root / p)


def _env_bool(name: str, fallback: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"環境変数 {name} は 0/1 または true/false で指定してください")


def _default_caption_socket_path() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.geteuid()}"
    return str(Path(runtime_dir) / "docich" / "ffmpeg-cc.sock")


def _validate_display(display: DisplayConfig) -> None:
    """Validate the optional presentation viewport before it reaches runtime.

    An all-zero viewport means that the normal display-owned layout is in use.
    Once any viewport dimension is configured, all four fields must describe a
    positive rectangle contained by the configured display.  Keeping this
    check at config load time makes both the CLI and coordinator fail closed,
    instead of launching an incorrectly-sized viewer or a second X server.
    """

    if type(display.managed) is not bool:
        raise ConfigError("display.managed は true または false である必要があります")

    fields_to_check = (
        "viewport_x",
        "viewport_y",
        "viewport_width",
        "viewport_height",
    )
    values: dict[str, int] = {}
    for field_name in fields_to_check:
        value = getattr(display, field_name)
        if type(value) is not int:
            raise ConfigError(f"display.{field_name} は整数である必要があります")
        if value < 0:
            raise ConfigError(f"display.{field_name} は0以上である必要があります")
        values[field_name] = value

    x = values["viewport_x"]
    y = values["viewport_y"]
    width = values["viewport_width"]
    height = values["viewport_height"]
    if width == 0 and height == 0:
        if x != 0 or y != 0:
            raise ConfigError(
                "display.viewport_x/y はviewport_width/heightと同時に指定してください"
            )
        return
    if width == 0 or height == 0:
        raise ConfigError(
            "display.viewport_width と viewport_height は両方とも正の値が必要です"
        )

    # The base display dimensions are existing config fields, so retain their
    # established behavior while turning malformed values into ConfigError
    # rather than leaking a TypeError from the containment comparison.
    if type(display.width) is not int or display.width <= 0:
        raise ConfigError("display.width は正の整数である必要があります")
    if type(display.height) is not int or display.height <= 0:
        raise ConfigError("display.height は正の整数である必要があります")
    if x + width > display.width or y + height > display.height:
        raise ConfigError(
            "display.viewport はdisplay.width/heightの内側に収まる必要があります"
        )


def load_global(repo_root: Path, config_path: Path | None = None) -> GlobalConfig:
    """探索順: config_path 引数 > $DOCICH_CONFIG > repo_root/config/docich.toml。
    ファイルが無ければ既定値で動く。
    """
    repo_root = Path(repo_root)
    path = _config_search_path(repo_root, config_path).expanduser().resolve()

    data: dict = {}
    if path.is_file():
        data = _load_toml_file(path)

    display = DisplayConfig(**_filtered(DisplayConfig, data.get("display", {}), "display"))
    _validate_display(display)
    audio = AudioConfig(**_filtered(AudioConfig, data.get("audio", {}), "audio"))
    stream = StreamConfig(**_filtered(StreamConfig, data.get("stream", {}), "stream"))
    captions = CaptionConfig(
        **_filtered(CaptionConfig, data.get("captions", {}), "captions")
    )
    agent = AgentDefaults(**_filtered(AgentDefaults, data.get("agent", {}), "agent"))
    watchdog = WatchdogConfig(
        **_filtered(WatchdogConfig, data.get("watchdog", {}), "watchdog")
    )
    rotation = RotationConfig(
        **_filtered(RotationConfig, data.get("rotation", {}), "rotation")
    )
    webui = WebUIConfig(**_filtered(WebUIConfig, data.get("webui", {}), "webui"))

    stream.ffmpeg_bin = os.environ.get("DOCICH_FFMPEG_BIN", stream.ffmpeg_bin).strip()
    captions.enabled = _env_bool("DOCICH_CC_ENABLED", captions.enabled)
    captions.socket_path = os.environ.get(
        "DOCICH_CC_SOCKET", captions.socket_path or _default_caption_socket_path()
    ).strip()

    if stream.mode not in STREAM_MODES:
        raise ConfigError(
            "stream.mode は "
            + "/".join(STREAM_MODES)
            + f" のいずれかである必要があります (現在値: {stream.mode!r})"
        )
    if not stream.ffmpeg_bin or "\x00" in stream.ffmpeg_bin:
        raise ConfigError("stream.ffmpeg_bin は空でない実行ファイル名またはパスである必要があります")
    if not isinstance(captions.enabled, bool):
        raise ConfigError("captions.enabled は true または false である必要があります")
    if (
        not captions.socket_path.startswith("/")
        or len(os.fsencode(captions.socket_path)) >= 104
        or re.fullmatch(r"/[A-Za-z0-9._/-]+", captions.socket_path) is None
    ):
        raise ConfigError(
            "captions.socket_path は104バイト未満の安全な絶対Unix socketパスである必要があります"
        )
    if watchdog.interval_s < 5:
        raise ConfigError(
            f"watchdog.interval_s は5以上である必要があります (現在値: {watchdog.interval_s!r})"
        )
    if watchdog.freeze_cycles < 2:
        raise ConfigError(
            f"watchdog.freeze_cycles は2以上である必要があります (現在値: {watchdog.freeze_cycles!r})"
        )
    if not isinstance(rotation.games, list) or not all(
        isinstance(x, str) for x in rotation.games
    ):
        raise ConfigError("rotation.games は文字列のリストである必要があります")
    try:
        rotation.games = [validate_game_name(name) for name in rotation.games]
    except NameValidationError as exc:
        raise ConfigError(f"rotation.games に不正なゲーム名があります: {exc}") from exc

    # webui validation
    if not isinstance(webui.bind, str) or not webui.bind.strip():
        raise ConfigError("webui.bind は空でない文字列である必要があります")
    # bind は IP リテラルか hostname を許容するが、危険な 0.0.0.0 は警告付きで許可する
    if webui.port < 1024 or webui.port > 65535:
        raise ConfigError(f"webui.port は1024-65535である必要があります (現在値: {webui.port!r})")
    if webui.token and len(webui.token.strip()) < 8:
        raise ConfigError("webui.token は8文字以上である必要があります (空なら無効)")
    if not isinstance(webui.token_env, str) or not webui.token_env.strip():
        raise ConfigError("webui.token_env は空でない文字列である必要があります")

    paths_raw = data.get("paths", {})
    if not isinstance(paths_raw, dict):
        raise ConfigError("[paths] はテーブルである必要があります")
    state_dir = _abs_path(repo_root, paths_raw.get("state_dir", "run"))
    games_dir = _abs_path(repo_root, paths_raw.get("games_dir", "config/games"))
    roms_dir = _abs_path(repo_root, paths_raw.get("roms_dir", "games/roms"))

    return GlobalConfig(
        repo_root=repo_root,
        config_path=path,
        display=display,
        audio=audio,
        stream=stream,
        captions=captions,
        agent=agent,
        watchdog=watchdog,
        rotation=rotation,
        webui=webui,
        state_dir=state_dir,
        games_dir=games_dir,
        roms_dir=roms_dir,
    )


def _parse_game(name: str, path: Path, data: dict) -> GameConfig:
    try:
        requested_name = validate_game_name(name)
    except NameValidationError as exc:
        raise ConfigError(f"ゲーム定義ファイル名が不正です: {path} ({exc})") from exc
    game_raw = data.get("game", {})
    if not isinstance(game_raw, dict):
        raise ConfigError(f"[game] はテーブルである必要があります: {path}")
    game_name = game_raw.get("name") or requested_name
    try:
        game_name = validate_game_name(game_name)
    except NameValidationError as exc:
        raise ConfigError(f"[game].name が不正です: {path} ({exc})") from exc
    if game_name != requested_name:
        raise ConfigError(
            f"ゲーム名がファイル名と一致しません: {path} "
            f"([game].name={game_name!r}, filename={requested_name!r})"
        )
    adapter = game_raw.get("adapter")
    if not adapter:
        raise ConfigError(f"[game].adapter は必須です: {path}")
    title = game_raw.get("title") or game_name
    submodule = game_raw.get("submodule") or ""
    if not isinstance(submodule, str):
        raise ConfigError(f"[game].submodule は文字列である必要があります: {path}")

    agent_raw = data.get("agent", {})
    agent = GameAgentConfig(**_filtered(GameAgentConfig, agent_raw, "agent"))

    return GameConfig(
        name=game_name,
        title=title,
        adapter=adapter,
        submodule=submodule,
        raw=data,
        agent=agent,
        path=path,
    )


def load_game(g: GlobalConfig, name: str) -> GameConfig:
    try:
        name = validate_game_name(name)
        path = ensure_contained(g.games_dir, g.games_dir / f"{name}.toml")
    except NameValidationError as exc:
        raise ConfigError(f"ゲーム名または定義パスが不正です: {exc}") from exc
    if not path.is_file():
        raise ConfigError(
            f"ゲーム定義が見つかりません: {name} ({path})。`docich games` で一覧を確認してください"
        )
    data = _load_toml_file(path)
    return _parse_game(name, path, data)


def list_games(g: GlobalConfig) -> list[GameConfig]:
    games: list[GameConfig] = []
    if not g.games_dir.is_dir():
        return games
    for p in sorted(g.games_dir.glob("*.toml")):
        try:
            safe_path = ensure_contained(g.games_dir, p)
            data = _load_toml_file(safe_path)
            games.append(_parse_game(p.stem, safe_path, data))
        except (ConfigError, NameValidationError) as exc:
            print(f"docich: 警告: {p} の読み込みに失敗しました: {exc}", file=sys.stderr)
    return games
