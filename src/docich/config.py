"""Dataclasses and TOML loader for docich configuration (architecture.md SS8)."""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

STREAM_MODES = ("null", "rtmp", "file")


class ConfigError(Exception):
    """Raised for invalid or missing docich configuration."""


@dataclass
class DisplayConfig:
    # 既定 :98。soren が :99 を使用中のため衝突を避ける (architecture.md SS0)。
    number: int = 98
    width: int = 1280
    height: int = 720
    color_depth: int = 24

    @property
    def name(self) -> str:
        return f":{self.number}"


@dataclass
class AudioConfig:
    enabled: bool = True
    sink_name: str = "docich_sink"
    # true にしない限り set-default-sink はしない (soren 共存。architecture.md SS0)。
    set_default: bool = False


@dataclass
class StreamConfig:
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


@dataclass
class AgentDefaults:
    default_interval_ms: int = 2000
    brain_timeout_s: int = 120


@dataclass
class GlobalConfig:
    repo_root: Path
    display: DisplayConfig
    audio: AudioConfig
    stream: StreamConfig
    agent: AgentDefaults
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
    raw: dict
    agent: GameAgentConfig
    path: Path


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


def load_global(repo_root: Path, config_path: Path | None = None) -> GlobalConfig:
    """探索順: config_path 引数 > $DOCICH_CONFIG > repo_root/config/docich.toml。
    ファイルが無ければ既定値で動く。
    """
    repo_root = Path(repo_root)
    path = _config_search_path(repo_root, config_path)

    data: dict = {}
    if path.is_file():
        data = _load_toml_file(path)

    display = DisplayConfig(**_filtered(DisplayConfig, data.get("display", {}), "display"))
    audio = AudioConfig(**_filtered(AudioConfig, data.get("audio", {}), "audio"))
    stream = StreamConfig(**_filtered(StreamConfig, data.get("stream", {}), "stream"))
    agent = AgentDefaults(**_filtered(AgentDefaults, data.get("agent", {}), "agent"))

    if stream.mode not in STREAM_MODES:
        raise ConfigError(
            "stream.mode は "
            + "/".join(STREAM_MODES)
            + f" のいずれかである必要があります (現在値: {stream.mode!r})"
        )

    paths_raw = data.get("paths", {})
    if not isinstance(paths_raw, dict):
        raise ConfigError("[paths] はテーブルである必要があります")
    state_dir = _abs_path(repo_root, paths_raw.get("state_dir", "run"))
    games_dir = _abs_path(repo_root, paths_raw.get("games_dir", "config/games"))
    roms_dir = _abs_path(repo_root, paths_raw.get("roms_dir", "games/roms"))

    return GlobalConfig(
        repo_root=repo_root,
        display=display,
        audio=audio,
        stream=stream,
        agent=agent,
        state_dir=state_dir,
        games_dir=games_dir,
        roms_dir=roms_dir,
    )


def _parse_game(name: str, path: Path, data: dict) -> GameConfig:
    game_raw = data.get("game", {})
    if not isinstance(game_raw, dict):
        raise ConfigError(f"[game] はテーブルである必要があります: {path}")
    game_name = game_raw.get("name") or name
    adapter = game_raw.get("adapter")
    if not adapter:
        raise ConfigError(f"[game].adapter は必須です: {path}")
    title = game_raw.get("title") or game_name

    agent_raw = data.get("agent", {})
    agent = GameAgentConfig(**_filtered(GameAgentConfig, agent_raw, "agent"))

    return GameConfig(
        name=game_name,
        title=title,
        adapter=adapter,
        raw=data,
        agent=agent,
        path=path,
    )


def load_game(g: GlobalConfig, name: str) -> GameConfig:
    path = g.games_dir / f"{name}.toml"
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
            data = _load_toml_file(p)
            games.append(_parse_game(p.stem, p, data))
        except ConfigError as exc:
            print(f"docich: 警告: {p} の読み込みに失敗しました: {exc}", file=sys.stderr)
    return games
