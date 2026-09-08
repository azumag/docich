"""docich CLI: argparse サブコマンド (architecture.md §7)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping

from . import (
    ai_generate,
    captions,
    chat,
    model_output_guard,
    overlay,
    procs,
    speech,
    tts,
)
from .actions import Action, ActionError, parse_actions
from .adapters import AdapterError, make_adapter, make_coordinator_adapter
from .adapters.cli_game import GAME_SESSION, RUNTIME_GAME_SESSION_ENV
from .adapters.retroarch import NETWORK_CMD_PORT, retroarch_network_port
from .config import ConfigError, GlobalConfig, list_games, load_game, load_global
from .agent.fence import shared_section
from .game_switch import (
    ERROR_ALREADY_ACTIVE,
    GameSwitchBusyError,
    GameSwitchCoordinator,
    GameSwitchError,
    GameSwitchStore,
    StateCorruptError,
    SwitchResult,
    new_request_id,
    validate_request_id,
)
from .naming import NameValidationError
from .netcmd import send_ra_cmd
from .trading import cli as trading_cli
from .state import State
from .stream import (
    CaptionSocketDirectoryError,
    StreamRuntime,
    StreamKeyError,
    build_ffmpeg_cmd,
    caption_capability,
    caption_socket_ready,
    ensure_caption_socket_parent,
    redact_stream_command,
    resolve_runtime,
)
from .supervise import run_callable_loop, run_loop
from .tmux import Tmux, TmuxError
from .watchdog import next_rotation_game
from .xkit import XKit

CORE_BINARIES = ["tmux", "Xvfb", "xdpyinfo", "xdotool"]
RETROARCH_CORE_CANDIDATES = ("snes9x", "bsnes_mercury_performance", "bsnes_mercury_balanced")
BROWSER_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
AUDIO_SINK_POLL_S = 30
PACTL_READY_TIMEOUT_S = 15


class CliError(Exception):
    """User-facing CLI error that is not a configuration problem per se."""


USER_ERRORS = (
    ai_generate.AiError,
    ConfigError,
    ActionError,
    AdapterError,
    StreamKeyError,
    captions.CaptionError,
    CliError,
    trading_cli.TradingCliError,
    tts.TtsError,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _docich_bin() -> str:
    return str(_repo_root() / "bin" / "docich")


def _run_argv(g: GlobalConfig, *parts: str) -> list[str]:
    # Supervisors may outlive the invoking shell. Pin the exact configuration
    # path so a tmux respawn cannot silently fall back to another config file.
    return [_docich_bin(), "--config", str(g.config_path), "run", *parts]


def _stream_window_env(g: GlobalConfig) -> dict[str, str]:
    """Freeze caption settings for a tmux server with an older environment."""
    env = {
        "DOCICH_FFMPEG_BIN": g.stream.ffmpeg_bin,
        "DOCICH_CC_ENABLED": "1" if g.captions.enabled else "0",
        "DOCICH_CC_SOCKET": g.captions.socket_path,
    }
    key_value = os.environ.get(g.stream.stream_key_env)
    if key_value:
        env[g.stream.stream_key_env] = key_value
    return env


def _load_global(args: argparse.Namespace) -> GlobalConfig:
    config_path = Path(args.config) if getattr(args, "config", None) else None
    return load_global(_repo_root(), config_path)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docich", description="docich: マルチゲーム AI 配信基盤の CLI"
    )
    parser.add_argument(
        "--config", metavar="PATH", help="設定ファイルのパス (既定: $DOCICH_CONFIG または config/docich.toml)"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="依存コマンド・環境を点検する")
    sub.add_parser("games", help="config/games/ の一覧を表示する")
    sub.add_parser("up", help="基盤 (display/audio/stream) を起動する")

    p_start = sub.add_parser("start", help="ゲームを起動する")
    p_start.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")
    p_start.add_argument("--request-id", metavar="UUID", help="再送用の request_id")
    p_start.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    p_down = sub.add_parser("down", help="全コンポーネントを停止する")
    p_down.add_argument("--request-id", metavar="UUID", help="再送用の request_id")
    p_down.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    p_stop = sub.add_parser("stop", help="現在のゲームを停止する")
    p_stop.add_argument("--request-id", metavar="UUID", help="再送用の request_id")
    p_stop.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    sub.add_parser(
        "migrate-legacy",
        help="移行用: coordinator 以前の旧 runtime (固定 window/session) を停止して互換 mirror を消去する",
    )

    p_switch = sub.add_parser("switch", help="ゲームを切り替える (transactional switch)")
    p_switch.add_argument("game", help="切り替え先のゲーム名")
    p_switch.add_argument("--request-id", metavar="UUID", help="再送用の request_id")
    p_switch.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    p_restart = sub.add_parser("restart", help="現在のゲームを再起動する (watchdog 復旧用)")
    p_restart.add_argument("--request-id", metavar="UUID", help="再送用の request_id")
    p_restart.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    p_recover = sub.add_parser("recover", help="中断した切替を復旧する (crash/failed 後の再開)")
    p_recover.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    p_rotate = sub.add_parser("rotate", help="[rotation] games を順に切り替える (時間割ローテーション)")
    p_rotate.add_argument(
        "--dry-run", action="store_true", help="切り替えを実行せず、切替先のゲーム名を表示するだけにする"
    )
    p_rotate.add_argument("--request-id", metavar="UUID", help="再送用の request_id")
    p_rotate.add_argument("--timeout", type=float, metavar="SEC", help="request 全体の deadline (秒)")

    p_status = sub.add_parser("status", help="各コンポーネントの状態を表示する")
    p_status.add_argument("--json", action="store_true", help="安定 schema の JSON で出力する (自動監視用)")
    p_status.add_argument(
        "--legacy", action="store_true",
        help="移行対象の旧 runtime 痕跡だけを出力する (migration 用)",
    )

    p_snap = sub.add_parser("snap", help="手動スクリーンショットを撮る")
    p_snap.add_argument("-o", "--output", metavar="PATH", help="出力先 (既定: run/screenshots/manual.png)")

    p_obs = sub.add_parser("obs", help="観測 JSON を出力する (brain 開発用)")
    p_obs.add_argument("game", nargs="?", help="ゲーム名 (省略時は現在のゲーム)")

    p_send = sub.add_parser("send", help="行動を単発注入する (デバッグ用)")
    p_send.add_argument("game", help="ゲーム名 (現在のゲームなら '-')")
    p_send.add_argument("json", help="行動 JSON ('{\"actions\":[...]}' 等)")

    p_ra = sub.add_parser("ra-cmd", help="RetroArch へ UDP コマンドを送る")
    p_ra.add_argument("cmd", nargs="+", help="コマンド (例: SAVE_STATE)")

    p_caption = sub.add_parser("caption", help="英語字幕計画とFFmpeg字幕IPCを操作する")
    captions.configure_parser(p_caption)

    p_say = sub.add_parser("say", help="ゲーム対応のTTSを参照実行する (soviet_now say_enqueue)")
    p_say.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")
    p_say.add_argument(
        "-f", "--file", dest="file", metavar="PATH",
        help="読み上げるテキストファイル (docichが一時コピーして渡す)",
    )
    p_say.add_argument("text", nargs="*", help="読み上げるテキスト (ファイルと併用不可)")
    p_say.add_argument("--rate", type=int, default=120, metavar="N", help="読み上げ速度 (既定120)")
    p_say.add_argument(
        "--pre-delay", dest="pre_delay", type=int, default=60, metavar="SEC",
        help="再生前の待ち時間 (既定60)",
    )
    p_say.add_argument(
        "--render-only", action="store_true",
        help="WAV生成のみで再生しない",
    )
    p_say.add_argument("-o", "--output", metavar="WAV", help="render結果のWAV出力先")
    p_say.add_argument(
        "--wav-playlist", metavar="PATH",
        help="再生するWAV playlist (--caption-chunksとセット)",
    )
    p_say.add_argument(
        "--caption-chunks", metavar="PATH",
        help="playlistに対応する字幕チャンク (--wav-playlistとセット)",
    )
    p_say.add_argument(
        "--cc", action="store_true",
        help="FFmpeg caption socket が準備済みなら字幕を有効化する (fail-open)",
    )
    p_say.add_argument(
        "--cc-socket", metavar="PATH",
        help="caption socket の上書き (既定: config の socket_path / XDG_RUNTIME_DIR)",
    )
    p_say.add_argument("--dry-run", action="store_true", help="実行せずargv/env/cwdを表示する")

    p_chat = sub.add_parser(
        "chat", help="コメント応答を参照実行する (soviet_now broadcast/comment.sh)"
    )
    p_chat.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")
    p_chat.add_argument(
        "--source", choices=sorted(chat.ALLOWED_CHAT_SOURCES), default="twitch",
        help="チャット source (既定 twitch)",
    )
    p_chat.add_argument("--dry-run", action="store_true", help="実行せずargv/env/cwdを表示する")

    p_radio = sub.add_parser(
        "radio", help="ラジオ生成を参照実行する (soviet_now broadcast/radio_engine.sh)"
    )
    p_radio.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")
    p_radio.add_argument(
        "--prompt-file", metavar="PATH", help="ラジオ生成プロンプトファイル"
    )
    p_radio.add_argument("--topic", metavar="TEXT", help="トピック直指定 (一時プロンプトへ書き出し)")
    p_radio.add_argument("--corner", default="main", metavar="NAME", help="コーナー名 (既定 main)")
    p_radio.add_argument("--game-num", type=int, default=0, metavar="N", help="ゲーム番号 (既定 0)")
    p_radio.add_argument("--score", default="", metavar="SCORE", help="スコア (省略可)")
    p_radio.add_argument("--dry-run", action="store_true", help="実行せずargv/env/cwdを表示する")

    p_ai = sub.add_parser(
        "ai", help="AI ディスパッチを参照実行する (soviet_now lib/ai_generate.sh, C-S1)"
    )
    p_ai.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")
    p_ai.add_argument("--label", required=True, help="ラベル (COMMENT または RADIO で始まる識別子)")
    p_ai.add_argument("--agents", required=True, metavar="AGENTS", help="カンマ区切りエージェントリスト (優先度順)")
    p_ai.add_argument("--prompt-file", required=True, metavar="PROMPT_FILE", help="生成プロンプトファイル")
    p_ai.add_argument("--timeout", type=int, default=None, metavar="SEC", help="codex タイムアウト (省略時はラベル既定)")
    p_ai.add_argument("--run-timeout", type=float, default=None, metavar="SEC", help="プロセス全体のタイムアウト")
    p_ai.add_argument("--dry-run", action="store_true", help="実行せずargv/env/cwdを表示する")

    speech.configure_parser(sub)

    p_overlay = sub.add_parser(
        "overlay", help="soviet_now のオーバーレイ生成を参照実行する (C3)"
    )
    p_overlay.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")
    p_overlay.add_argument(
        "kind", choices=sorted(overlay.ALLOWED_OVERLAY_KINDS),
        help="オーバーレイ種別",
    )
    p_overlay.add_argument(
        "--output", metavar="DIR", help="HTML 出力先ディレクトリ (既定: 一時ディレクトリ)"
    )
    p_overlay.add_argument("--dry-run", action="store_true", help="実行せずargv/env/cwdを表示する")

    p_ai_guard = sub.add_parser(
        "ai-guard", help="AI 出力ガード (思考漏れ・tool protocol 除去)。stdin を読み stdout へ出す (C4)"
    )
    p_ai_guard.set_defaults(func=lambda args: model_output_guard.main())

    p_trading = sub.add_parser(
        "trading", help="暗号資産の paper trading 基盤を操作する"
    )
    trading_cli.configure_parser(p_trading)

    p_webui = sub.add_parser("webui", help="モデルチェーン / バックオフ管理 Web UI を起動する (Tailscale経由)")
    p_webui.add_argument("--bind", metavar="ADDR", help="バインドアドレス (既定 127.0.0.1)")
    p_webui.add_argument("--port", type=int, metavar="PORT", help="ポート (既定 8787)")
    p_webui.add_argument("--soren-root", metavar="PATH", help="Soren ルート (既定 games/soviet_now または g.webui.soren_root)")
    p_webui.add_argument("--read-only", action="store_true", help="読み取り専用で起動する")
    p_webui.add_argument("--dry-run", action="store_true", help="起動せず設定解決結果だけ表示する")

    p_run = sub.add_parser("run", help="(内部用) tmux window 内で監督ループを実行する")
    p_run.add_argument(
        "component", choices=["display", "audio", "stream", "game", "agent", "watchdog", "trading"]
    )
    p_run.add_argument("name", nargs="?", help="game/agent の場合のゲーム名")
    p_run.add_argument("--runtime-id", metavar="ID", help="agent の runtime 束縛 (P3 fence)")
    p_run.add_argument("--generation", type=int, metavar="N", help="agent の generation 束縛 (P3 fence)")
    p_run.add_argument("--lease-id", metavar="UUID", help="agent の lease 束縛 (P3 fence)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except USER_ERRORS as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    g = _load_global(args)
    command = args.command
    if command == "doctor":
        return cmd_doctor(g)
    if command == "games":
        return cmd_games(g)
    if command == "up":
        return cmd_up(g)
    if command == "down":
        return cmd_down(g, request_id=args.request_id, timeout_s=args.timeout)
    if command == "start":
        return cmd_start(g, args.game, request_id=args.request_id, timeout_s=args.timeout)
    if command == "stop":
        return cmd_stop(g, request_id=args.request_id, timeout_s=args.timeout)
    if command == "migrate-legacy":
        return cmd_migrate_legacy(g)
    if command == "switch":
        return cmd_switch(g, args.game, request_id=args.request_id, timeout_s=args.timeout)
    if command == "restart":
        return cmd_restart(g, request_id=args.request_id, timeout_s=args.timeout)
    if command == "recover":
        return cmd_recover(g, timeout_s=args.timeout)
    if command == "rotate":
        return cmd_rotate(g, args.dry_run, request_id=args.request_id, timeout_s=args.timeout)
    if command == "status":
        if args.legacy:
            return cmd_status_legacy(g, json_output=args.json)
        return cmd_status(g, json_output=args.json)
    if command == "snap":
        return cmd_snap(g, args.output)
    if command == "obs":
        return cmd_obs(g, args.game)
    if command == "send":
        return cmd_send(g, args.game, args.json)
    if command == "ra-cmd":
        return cmd_ra_cmd(g, args.cmd)
    if command == "caption":
        return captions.run_args(args, default_socket=g.captions.socket_path)
    if command == "say":
        return tts.cli_say(args)
    if command == "chat":
        return chat.cli_chat(args)
    if command == "radio":
        return chat.cli_radio(args)
    if command == "ai":
        return ai_generate.cli_ai(args)
    if command == "voicevox":
        return speech.run_args(args)
    if command == "overlay":
        return overlay.cli_overlay(args)
    if command == "ai-guard":
        return model_output_guard.main()
    if command == "trading":
        return trading_cli.run_args(args, repo_root=_repo_root())
    if command == "webui":
        from .webui import run_webui

        return run_webui(
            g,
            bind=getattr(args, "bind", None),
            port=getattr(args, "port", None),
            soren_root=getattr(args, "soren_root", None),
            read_only=True if getattr(args, "read_only", False) else None,
            dry_run=getattr(args, "dry_run", False),
        )
    if command == "run":
        return cmd_run(g, args)
    raise CliError(f"未知のコマンドです: {command}")


# ---------------------------------------------------------------------------
# doctor / games
# ---------------------------------------------------------------------------


def _print_row(ok: bool, label: str, detail: str = "") -> None:
    mark = "OK" if ok else "NG"
    if detail:
        print(f"  [{mark}] {label}: {detail}")
    else:
        print(f"  [{mark}] {label}")


def _find_retroarch_core() -> str | None:
    for name in RETROARCH_CORE_CANDIDATES:
        matches = sorted(glob.glob(f"/usr/lib/*/libretro/{name}_libretro.so"))
        if matches:
            return matches[0]
    return None


def _find_browser() -> str | None:
    for name in BROWSER_CANDIDATES:
        found = procs.which(name)
        if found:
            return found
    return None


def cmd_doctor(g: GlobalConfig) -> int:
    print("docich doctor")
    core_ok = True

    print("\n[コア]")
    py_ok = sys.version_info >= (3, 11)
    _print_row(py_ok, "python", f"{sys.version.split()[0]} (>=3.11 必須)")
    core_ok = core_ok and py_ok
    for name in CORE_BINARIES:
        path = procs.which(name)
        ok = path is not None
        _print_row(ok, name, path or "見つかりません")
        core_ok = core_ok and ok

    ffmpeg_path = (
        g.stream.ffmpeg_bin
        if (
            "/" in g.stream.ffmpeg_bin
            and Path(g.stream.ffmpeg_bin).is_file()
            and os.access(g.stream.ffmpeg_bin, os.X_OK)
        )
        else procs.which(g.stream.ffmpeg_bin)
    )
    _print_row(ffmpeg_path is not None, "ffmpeg", ffmpeg_path or g.stream.ffmpeg_bin)
    core_ok = core_ok and ffmpeg_path is not None

    print("\n[captions]")
    if g.captions.enabled:
        caption_ok, caption_detail = caption_capability(g.stream.ffmpeg_bin)
        _print_row(caption_ok, "native CC", caption_detail)
        core_ok = core_ok and caption_ok
        _print_row(True, "fail-open", "字幕障害時も映像・音声を継続")
        _print_row(True, "socket", g.captions.socket_path)
    else:
        _print_row(True, "native CC", "無効 (DOCICH_CC_ENABLED=1で有効化)")

    if g.audio.enabled:
        print("\n[audio]")
        pactl = procs.which("pactl")
        pulseaudio = procs.which("pulseaudio")
        ok = pactl is not None or pulseaudio is not None
        _print_row(ok, "pactl/pulseaudio", pactl or pulseaudio or "見つかりません")

    print("\n[adapter:retroarch]")
    ra_path = procs.which("retroarch")
    _print_row(ra_path is not None, "retroarch", ra_path or "見つかりません")
    core_path = _find_retroarch_core()
    _print_row(
        core_path is not None,
        "libretro core",
        core_path or f"見つかりません ({'/'.join(RETROARCH_CORE_CANDIDATES)})",
    )

    print("\n[adapter:cli]")
    xterm_path = procs.which("xterm")
    _print_row(xterm_path is not None, "xterm", xterm_path or "見つかりません")

    print("\n[adapter:browser]")
    browser_path = _find_browser()
    _print_row(
        browser_path is not None,
        "chromium",
        browser_path or f"見つかりません ({'/'.join(BROWSER_CANDIDATES)})",
    )

    print("\n[games]")
    game_paths = sorted(g.games_dir.glob("*.toml")) if g.games_dir.is_dir() else []
    if not game_paths:
        print("  (config/games に *.toml がありません)")
    for path in game_paths:
        try:
            game = load_game(g, path.stem)
            _print_row(True, game.name, f"adapter={game.adapter}")
        except ConfigError as exc:
            _print_row(False, path.stem, f"パース失敗: {exc}")

    print()
    if core_ok:
        print("結果: OK (コアの必須項目はすべて揃っています)")
        return 0
    print("結果: NG (コアの必須項目が不足しています)", file=sys.stderr)
    return 1


def cmd_games(g: GlobalConfig) -> int:
    games = list_games(g)
    if not games:
        print("docich: ゲーム定義がありません (config/games/*.toml を作成してください)")
        return 0
    name_w = max(len(x.name) for x in games)
    adapter_w = max(len(x.adapter) for x in games)
    for game in games:
        agent_state = "有効" if game.agent.enabled else "無効"
        print(f"{game.name:<{name_w}}  {game.adapter:<{adapter_w}}  {game.title}  (agent: {agent_state})")
    return 0


# ---------------------------------------------------------------------------
# up / down / start / stop / switch / rotate / status
# ---------------------------------------------------------------------------


def cmd_up(g: GlobalConfig) -> int:
    state = State(g)
    state.ensure()
    tmux = Tmux()
    tmux.ensure_session()

    if not g.display.managed:
        xkit = XKit(g.display.name)
        if not xkit.display_ready():
            raise CliError(f"外部所有ディスプレイ {g.display.name} が利用できません")
        print(f"docich: 外部所有ディスプレイ {g.display.name} へ接続します")
    elif not tmux.has_window("display"):
        tmux.new_window("display", _run_argv(g, "display"))
        print("docich: display window を起動しました")
    else:
        print("docich: display window は既に起動しています")

    if g.audio.enabled:
        if not tmux.has_window("audio"):
            tmux.new_window("audio", _run_argv(g, "audio"))
            print("docich: audio window を起動しました")
        else:
            print("docich: audio window は既に起動しています")
    else:
        print("docich: audio.enabled が false のため audio window は起動しません")

    if g.stream.mode != "null":
        if not tmux.has_window("stream"):
            tmux.new_window(
                "stream", _run_argv(g, "stream"), env=_stream_window_env(g)
            )
            print("docich: stream window を起動しました")
        else:
            print("docich: stream window は既に起動しています")
    else:
        print("docich: stream.mode が null のため stream window は起動しません")

    if g.watchdog.enabled:
        if not tmux.has_window("watchdog"):
            tmux.new_window("watchdog", _run_argv(g, "watchdog"))
            print("docich: watchdog window を起動しました")
        else:
            print("docich: watchdog window は既に起動しています")
    # watchdog.enabled が false の場合は既定無効の付加機能なので何も表示しない

    if g.trading.paper_worker_enabled:
        if not tmux.has_window("trading"):
            tmux.new_window("trading", _run_argv(g, "trading"))
            print("docich: trading paper worker window を起動しました")
        else:
            print("docich: trading paper worker window は既に起動しています")
    # (audio/stream と異なり、無効メッセージを毎回出すほどの情報価値がない)。

    xkit = XKit(g.display.name)
    if xkit.wait_display():
        print(f"docich: ディスプレイ {g.display.name} の準備ができました")
    else:
        print(f"docich: ディスプレイ {g.display.name} の準備を確認できませんでした (タイムアウト)", file=sys.stderr)
    return 0


def cmd_down(g: GlobalConfig, *, request_id: str | None = None, timeout_s: float | None = None) -> int:
    # Design v2: stop が成功 (idle no-op を含む) した場合だけ共有 session
    # 停止へ進む。busy / rolled_back / failed では session を kill しない。
    _require_no_legacy_runtime(g)
    result = _coordinator(g).stop(
        request_id=_checked_request_id(request_id),
        timeout_s=_checked_timeout(timeout_s),
    )
    if result.status != "succeeded":
        _print_switch_result("全コンポーネントを停止", result)
        return _result_exit_code(result)
    tmux = Tmux()
    tmux.kill_session()
    State(g).clear_current_game()
    print("docich: 停止しました")
    return 0


# ---------------------------------------------------------------------------
# start / stop / switch / rotate via the GameSwitchCoordinator (design v2 §8)
# ---------------------------------------------------------------------------


def _coordinator(g: GlobalConfig) -> GameSwitchCoordinator:
    store = GameSwitchStore(g.state_dir)
    return GameSwitchCoordinator(store, lambda spec: make_coordinator_adapter(g, spec))


def _legacy_footprint(g: GlobalConfig) -> dict:
    """pre-coordinator runtime の痕跡 (Design v2 の移行対象) を列挙する。

    ``{"footprint": [...], "unreadable": [...]}`` を返す。最終評価は
    `docich status --legacy --json` が機械可読で行う。共有の `docich`
    session 自体 (display/audio/stream) は対象外。
    """
    from .status import legacy_footprint

    return legacy_footprint(g)


def _require_no_legacy_runtime(g: GlobalConfig, *, tmux: Tmux | None = None) -> None:
    """canonical 未作成かつ legacy 痕跡ありなら fail-closed に止める。

    tmux 確認不能 (unreadable) は必ずブロックする。canonical が既に存在
    する世界では coordinator が唯一の正本であり、legacy 痕跡は operator
    の責任範囲 (coordinator は所有外に触れない)。
    """
    from .status import legacy_footprint

    probe = legacy_footprint(g, tmux=tmux or Tmux())
    if probe["unreadable"]:
        raise CliError(
            f"旧 runtime の有無を確認できませんでした ({', '.join(probe['unreadable'])})。"
            f"tmux の状態を確認してから再実行してください。"
        )
    footprint = probe["footprint"]
    if not footprint:
        return
    store = GameSwitchStore(g.state_dir)
    try:
        _, needs_write = store.canonical.load()
    except GameSwitchError:
        return  # corrupt canonical は coordinator 側の復旧フローが扱う
    if not needs_write:
        return
    raise CliError(
        f"旧 runtime の痕跡を検出しました ({', '.join(footprint)})。"
        f"`docich migrate-legacy` で旧 runtime を停止してから再実行してください。"
    )


def _checked_request_id(request_id: str | None) -> str:
    if request_id is None:
        return new_request_id()
    try:
        return validate_request_id(request_id)
    except NameValidationError as exc:
        raise CliError(f"--request-id が不正です: {exc}") from exc


def _checked_timeout(timeout_s: float | None) -> float | None:
    if timeout_s is None:
        return None
    if timeout_s <= 0:
        raise CliError("--timeout は正の秒数で指定してください")
    return float(timeout_s)


def _read_active_runtime(g: GlobalConfig) -> Mapping[str, object] | None:
    """canonical active runtime (設計の正本) を返す。

    canonical がまだ作成されていない移行前だけ None を返し (legacy 互換)、
    canonical が存在する場合は active (または None) を正とする。壊れている
    場合は StateCorruptError を投げ、呼び出し側で CliError に変換する
    (fail-closed)。P3 の fence/action lock までは action 実行自体は legacy。
    """
    store = GameSwitchStore(g.state_dir)
    state, needs_write = store.canonical.load()
    if needs_write:
        return None
    active = state.get("active")
    if not isinstance(active, dict):
        return None
    game = active.get("game")
    if not isinstance(game, str) or not game:
        return None
    return dict(active)


def _read_active_game(g: GlobalConfig) -> str | None:
    """canonical があれば active を正とし、未作成時だけ互換 mirror を読む。

    canonical が存在して active=None (idle) の場合、stale mirror は使わない。
    壊れている場合は CliError (fail-closed)。
    """
    store = GameSwitchStore(g.state_dir)
    try:
        state, needs_write = store.canonical.load()
    except GameSwitchError as exc:
        raise CliError(f"canonical state が読み込めません: {exc}") from exc
    if needs_write:
        return State(g).current_game()
    active = state.get("active")
    if isinstance(active, dict):
        game = active.get("game")
        if isinstance(game, str) and game:
            return game
    return None


def _result_exit_code(result: SwitchResult) -> int:
    if result.status == "succeeded":
        return 0
    if result.status in {"in_progress", "busy"}:
        return 1
    if result.status == "rolled_back":
        return 1
    return 2


def _print_switch_result(verb: str, result: SwitchResult) -> None:
    direction = ""
    if result.from_game and result.to_game:
        direction = f" ({result.from_game} -> {result.to_game})"
    elif result.to_game:
        direction = f" ({result.to_game})"
    elif result.from_game:
        direction = f" ({result.from_game})"
    detail = f" [request_id={result.request_id}]"
    if result.status == "succeeded":
        print(f"docich: {verb}しました{direction} (generation={result.generation}){detail}")
    elif result.status == "rolled_back":
        print(f"docich: {verb}に失敗したため旧ゲームを復元しました{direction}{detail}", file=sys.stderr)
    elif result.status == "in_progress":
        print(f"docich: {verb}は既に進行中です{direction}{detail}", file=sys.stderr)
    elif result.status == "busy":
        print(f"docich: 別のゲーム切替が進行中のため{verb}できません{direction}{detail}", file=sys.stderr)
    elif result.status == "request_conflict":
        print(f"docich: 同じ request_id が異なる内容で使われています{detail}", file=sys.stderr)
    else:
        why = result.error_code or "unknown"
        extra = f": {result.detail}" if result.detail else ""
        print(f"docich: {verb}に失敗しました ({why}{extra}){detail}", file=sys.stderr)


def cmd_start(g: GlobalConfig, name: str, *, request_id: str | None = None, timeout_s: float | None = None) -> int:
    tmux = Tmux()
    if g.display.managed and not tmux.has_window("display"):
        raise CliError("display window がありません。先に `docich up` を実行してください")
    if not g.display.managed and not XKit(g.display.name).display_ready():
        raise CliError(f"外部所有ディスプレイ {g.display.name} が利用できません")
    _require_no_legacy_runtime(g)
    # request_id は一度だけ解決し、switch fallback でも再利用する。
    resolved_request_id = _checked_request_id(request_id)
    result = _coordinator(g).start(
        name,
        request_id=resolved_request_id,
        timeout_s=_checked_timeout(timeout_s),
    )
    if result.status == "failed" and result.error_code == ERROR_ALREADY_ACTIVE:
        # 別 game が active の場合、設計どおり switch を要求する。
        return cmd_switch(g, name, request_id=resolved_request_id, timeout_s=timeout_s)
    _print_switch_result(f"ゲームを起動", result)
    return _result_exit_code(result)


def cmd_stop(g: GlobalConfig, *, request_id: str | None = None, timeout_s: float | None = None) -> int:
    _require_no_legacy_runtime(g)
    result = _coordinator(g).stop(
        request_id=_checked_request_id(request_id),
        timeout_s=_checked_timeout(timeout_s),
    )
    _print_switch_result("ゲームを停止", result)
    return _result_exit_code(result)


def cmd_migrate_legacy(g: GlobalConfig) -> int:
    """One-time migration from the pre-coordinator runtime.

    Stops the fixed windows/session (`game` / `agent` / `docich-game`) and,
    only before canonical exists, runs best-effort legacy adapter cleanup
    for the mirrored game and clears the compat mirror.  After migration,
    coordinator operations see a clean slate.  A healthy post-migration
    compat mirror is never touched: `legacy_footprint()` no longer counts
    it, and neither does this command.  Safe no-op when no legacy footprint
    remains.
    """
    store = GameSwitchStore(g.state_dir)
    try:
        _, needs_write = store.canonical.load()
    except GameSwitchError:
        # Corrupt canonical is handled by the coordinator recovery flow;
        # migration must not rewrite state it cannot trust.
        raise CliError(
            "canonical state が壊れているため移行できません。"
            "先に coordinator の復旧フローを確認してください。"
        )
    tmux = Tmux()
    attempted = _legacy_footprint(g)["footprint"]
    for window in ("agent", "game"):
        if tmux.has_window(window):
            tmux.kill_window(window)
    if tmux.has_session_named(GAME_SESSION):
        tmux.kill_session_named(GAME_SESSION)

    # kill helper は失敗を黙って無視する。停止できたことを再確認するまで
    # mirror 消去・canonical 初期化へ進まない (fail-closed)。再確認は strict
    # existence API (接続失敗を「不在」とせず TmuxError) で行う。
    try:
        remaining = [
            f"window:{window}"
            for window in ("agent", "game")
            if tmux.window_target_exists(f"docich:{window}", strict=True)
        ]
        if tmux.session_target_exists(GAME_SESSION, strict=True):
            remaining.append(f"session:{GAME_SESSION}")
    except TmuxError as exc:
        raise CliError(
            f"旧 runtime の停止を確認できませんでした (tmux 確認エラー: {exc})。"
            f"tmux の状態を確認してから `docich migrate-legacy` を再実行してください。"
        ) from exc
    if remaining:
        raise CliError(
            f"旧 runtime の停止を確認できませんでした ({', '.join(remaining)})。"
            f"tmux の状態を確認してから `docich migrate-legacy` を再実行してください。"
        )

    state = State(g)
    mirrored = state.current_game() if needs_write else None
    if mirrored is not None:
        try:
            game = load_game(g, mirrored)
            xkit = XKit(g.display.name)
            adapter = make_adapter(g, game, state=state, tmux=tmux, xkit=xkit)
            adapter.cleanup()
        except Exception as exc:
            print(f"docich: 警告: {mirrored} の cleanup に失敗しました: {exc}", file=sys.stderr)
        state.clear_current_game()

    if needs_write:
        store.initialize()
    if attempted or mirrored is not None:
        print(f"docich: 旧 runtime を移行しました ({', '.join(attempted) if attempted else 'mirror のみ'})")
    else:
        print("docich: 移行対象の旧 runtime はありませんでした")
    return 0


def cmd_switch(g: GlobalConfig, name: str, *, request_id: str | None = None, timeout_s: float | None = None) -> int:
    _require_no_legacy_runtime(g)
    result = _coordinator(g).switch(
        name,
        request_id=_checked_request_id(request_id),
        timeout_s=_checked_timeout(timeout_s),
    )
    _print_switch_result("ゲームを切り替え", result)
    return _result_exit_code(result)


def cmd_restart(g: GlobalConfig, *, request_id: str | None = None, timeout_s: float | None = None) -> int:
    _require_no_legacy_runtime(g)
    result = _coordinator(g).restart(
        request_id=_checked_request_id(request_id),
        timeout_s=_checked_timeout(timeout_s),
    )
    _print_switch_result("ゲームを再起動", result)
    return _result_exit_code(result)


def cmd_recover(g: GlobalConfig, *, timeout_s: float | None = None) -> int:
    _require_no_legacy_runtime(g)
    try:
        result = _coordinator(g).recover(
            timeout_s=_checked_timeout(timeout_s),
        )
    except GameSwitchError as exc:
        raise CliError(f"復旧できませんでした: {exc}") from exc
    if result.status == "succeeded":
        print(f"docich: 復旧しました ({result.detail or 'recovery は不要でした'})")
    else:
        _print_switch_result("復旧", result)
    return _result_exit_code(result)


def cmd_rotate(g: GlobalConfig, dry_run: bool, *, request_id: str | None = None, timeout_s: float | None = None) -> int:
    if not g.rotation.games:
        raise CliError("[rotation] games を設定してください (config/docich.toml)")
    if dry_run:
        target = next_rotation_game(g.rotation.games, _read_active_game(g))
        print(f"docich: rotate 切替先 = {target} (dry-run のため切り替えません)")
        return 0
    _require_no_legacy_runtime(g)
    result = _coordinator(g).rotate(
        list(g.rotation.games),
        request_id=_checked_request_id(request_id),
        timeout_s=_checked_timeout(timeout_s),
    )
    print(f"docich: rotate 切替先 = {result.to_game or '(なし)'}")
    _print_switch_result("ゲームを切り替え", result)
    return _result_exit_code(result)


def cmd_status(g: GlobalConfig, *, json_output: bool = False) -> int:
    from .status import collect_status

    tmux = Tmux()
    xkit = XKit(g.display.name)
    data = collect_status(g, tmux=tmux, xkit=xkit)
    if json_output:
        print(json.dumps(data, ensure_ascii=False))
        return 0

    state = State(g)

    print("docich status")
    session_alive = tmux.has_session()
    print(f"  session: {'起動中' if session_alive else '停止中'}")

    window_states: dict[str, bool] = {}
    for w in ("display", "audio", "stream", "game", "agent", "watchdog"):
        window_states[w] = tmux.has_window(w) if session_alive else False
        print(f"  window[{w}]: {'起動中' if window_states[w] else '停止中'}")

    print(f"  display_ready: {'はい' if xkit.display_ready() else 'いいえ'}")
    print(f"  current_game: {state.current_game() or '(なし)'}")
    print(f"  stream.mode: {g.stream.mode}")
    print(f"  captions.requested: {'はい' if g.captions.enabled else 'いいえ'}")

    if g.stream.mode == "null":
        print("  ffmpeg: (配信しない設定です)")
        print("  captions.active: いいえ (stream.mode=null)")
    else:
        try:
            runtime = resolve_runtime(g, mask_key=True)
            active = False
            detail = runtime.caption_detail
            if runtime.captions_active and not window_states["stream"]:
                detail = f"{detail}; stream windowは停止中です"
            elif runtime.captions_active:
                active, socket_detail = caption_socket_ready(g.captions.socket_path)
                detail = f"{detail}; {socket_detail}"
            print(f"  captions.active: {'はい' if active else 'いいえ'}")
            print(f"  captions.detail: {detail}")
            print(f"  ffmpeg: {shlex.join(runtime.command)}")
        except StreamKeyError as exc:
            print(f"  ffmpeg: 構築できません ({exc})")

    print("  switch:")
    _print_switch_status(data)
    return 0


def cmd_status_legacy(g: GlobalConfig, *, json_output: bool = False) -> int:
    """Report pre-coordinator runtime traces for migration (P4).

    Machine-readable via --json ({"schema_version", "footprint",
    "unreadable"}), human readable otherwise.  Read-only: nothing is
    stopped or rewritten.
    """
    from .status import STATUS_SCHEMA_VERSION, legacy_footprint

    probe = legacy_footprint(g, tmux=Tmux())
    if json_output:
        print(json.dumps(
            {
                "schema_version": STATUS_SCHEMA_VERSION,
                "footprint": probe["footprint"],
                "unreadable": probe["unreadable"],
            },
            ensure_ascii=False,
        ))
        return 0
    print("docich status --legacy")
    for item in probe["footprint"]:
        print(f"  legacy: {item}")
    for item in probe["unreadable"]:
        print(f"  legacy: {item} (確認不能)")
    if not probe["footprint"] and not probe["unreadable"]:
        print("  legacy: (なし)")
    return 0


def _format_actual_runtime(label: str, runtime: dict | None) -> list[str]:
    lines = []
    if runtime is None:
        lines.append(f"    {label}: (なし)")
        return lines
    lines.append(
        f"    {label}: {runtime.get('game')} "
        f"(adapter={runtime.get('adapter')}, generation={runtime.get('generation')}, "
        f"runtime_id={runtime.get('runtime_id')})"
    )
    for key in ("game_window", "agent_window", "adapter_session"):
        probe = runtime.get(key) or {}
        if "applicable" in probe and not probe["applicable"]:
            lines.append(f"      {key}: (対象外: {probe.get('name')})")
            continue
        exists = probe.get("exists")
        panes = probe.get("panes")
        if panes == "dead":
            exists_text = "停止中 (pane dead)"
        else:
            exists_text = "起動中" if exists is True else ("不明" if exists is None else "停止中")
        panes_text = f", panes={panes}" if panes is not None else ""
        lines.append(f"      {key}[{probe.get('name')}]: {exists_text} (ownership={probe.get('ownership')}{panes_text})")
    return lines


def _print_switch_status(data: dict) -> None:
    canonical = data.get("canonical") or {}
    mirror = data.get("mirror") or {}
    actual = data.get("actual") or {}
    fence = data.get("agent_fence") or {}
    if canonical.get("corrupt"):
        print(f"    canonical: 破損 ({canonical.get('error')})")
    elif not canonical.get("present"):
        print("    canonical: (なし)")
    else:
        print(f"    canonical: phase={canonical.get('phase')} operation={canonical.get('operation') or '-'}")
        print(f"      next_generation={canonical.get('next_generation')}")
        if canonical.get("last_result") is not None:
            print(f"      last_result: {canonical.get('last_result')}")
        if canonical.get("last_error") is not None:
            print(f"      last_error: {canonical.get('last_error')}")
    match = mirror.get("matches_canonical")
    match_text = "(canonical 不在のため比較なし)" if match is None else ("一致" if match else "不一致")
    print(f"    mirror[current_game]: {mirror.get('game') or '(なし)'} ({match_text})")
    for line in _format_actual_runtime("active", actual.get("active")):
        print(line)
    for line in _format_actual_runtime("candidate", actual.get("candidate")):
        print(line)
    for line in _format_actual_runtime("previous", actual.get("previous")):
        print(line)
    retiring_actual = actual.get("retiring") or []
    if retiring_actual:
        for probed in retiring_actual:
            for line in _format_actual_runtime("retiring", probed):
                print(line)
    else:
        print("    retiring: (なし)")
    fence_tuple = fence.get("tuple")
    if fence_tuple is None:
        print("    agent_fence: (なし)")
    else:
        present = fence.get("agent_window_present")
        panes = None
        if isinstance(actual.get("active"), dict):
            panes = actual["active"]["agent_window"].get("panes")
        if present is True:
            if panes == "dead":
                present_text = "存在 (pane dead)"
            elif panes == "unreadable":
                present_text = "存在 (pane不明)"
            else:
                present_text = "存在 (起動中)"
        elif present is None:
            present_text = "不明"
        else:
            present_text = "不存在"
        print(
            f"    agent_fence: game={fence_tuple.get('game')} "
            f"runtime_id={fence_tuple.get('runtime_id')} "
            f"generation={fence_tuple.get('generation')} "
            f"lease_id={fence_tuple.get('lease_id')} "
            f"(agent_window={present_text})"
        )
    print(f"    cleanup_pending: {'はい' if data.get('cleanup_pending') else 'いいえ'}")


# ---------------------------------------------------------------------------
# snap / obs / send / ra-cmd
# ---------------------------------------------------------------------------


def cmd_snap(g: GlobalConfig, output: str | None) -> int:
    state = State(g)
    state.ensure()
    xkit = XKit(g.display.name)
    out_path = Path(output) if output else state.screenshots_dir / "manual.png"
    try:
        result = xkit.screenshot(out_path, g.display.width, g.display.height)
    except subprocess.CalledProcessError as exc:
        raise CliError(
            "スクリーンショットに失敗しました。ディスプレイは起動していますか? (`docich up`)"
        ) from exc
    print(str(result))
    return 0


def _resolve_game_name(g: GlobalConfig, state: State, name: str | None, *, dash_means_current: bool) -> str:
    if dash_means_current and name == "-":
        name = None
    if name:
        return name
    store = GameSwitchStore(g.state_dir)
    try:
        canonical, needs_write = store.canonical.load()
    except GameSwitchError as exc:
        raise CliError(f"canonical state が読み込めません: {exc}") from exc
    if not needs_write:
        active = canonical.get("active")
        if isinstance(active, dict):
            game = active.get("game")
            if isinstance(game, str) and game:
                return game
        raise CliError("現在実行中のゲームがありません。ゲーム名を指定してください")
    legacy = state.current_game()
    if not legacy:
        raise CliError("現在実行中のゲームがありません。ゲーム名を指定してください")
    return legacy


def _active_fence_or_none(g: GlobalConfig, resolved: str):
    """canonical があれば ready な active 一致を必須にして fence を返す。

    canonical 未作成時 (needs_write) だけ None を返し legacy 互換を許す。
    active 不在・phase 非 ready・game 不一致は CliError (fail-closed)。
    lock 内での再確認用。
    """
    from .agent.fence import AgentFence

    store = GameSwitchStore(g.state_dir)
    canonical, needs_write = store.canonical.load()
    if needs_write:
        return None
    if canonical.get("phase") not in {"ready", "draining"}:
        raise CliError(
            "ゲーム切替の実行中のため観測・入力できません "
            "(phase が ready または draining ではありません)"
        )
    active = canonical.get("active")
    if not isinstance(active, dict) or active.get("game") != resolved:
        current = active.get("game") if isinstance(active, dict) else None
        hint = f" (現在のゲーム: {current})" if current else " (実行中のゲームはありません)"
        raise CliError(f"{resolved} は現在起動していません{hint}。ゲーム名を確認してください")
    return AgentFence(
        game=str(active["game"]),
        runtime_id=str(active["runtime_id"]),
        generation=int(active["generation"]),
        lease_id=active.get("lease_id"),
    )


def cmd_obs(g: GlobalConfig, name: str | None) -> int:
    state = State(g)
    try:
        resolved = _resolve_game_name(g, state, name, dash_means_current=False)
        game = load_game(g, resolved)

        def _do():
            fence = _active_fence_or_none(g, resolved)
            _bind_active_cli_session(g, resolved)
            adapter = make_adapter(
                g, game, state=state, tmux=Tmux(), xkit=XKit(g.display.name), fence=fence
            )
            return adapter.observe()

        obs = shared_section(g.state_dir, _do)
    except StateCorruptError as exc:
        raise CliError(f"canonical state が読み込めません: {exc}") from exc
    except GameSwitchBusyError as exc:
        raise CliError(f"ゲーム切替が進行中のため観測できません: {exc}") from exc
    print(obs.to_json())
    return 0


def cmd_send(g: GlobalConfig, game_arg: str, json_text: str) -> int:
    state = State(g)
    try:
        resolved = _resolve_game_name(g, state, game_arg, dash_means_current=True)
        game = load_game(g, resolved)
        # wait-only の send でも対象ゲームの照合は必須 (lock 不要な読み取り)。
        _active_fence_or_none(g, resolved)
    except StateCorruptError as exc:
        raise CliError(f"canonical state が読み込めません: {exc}") from exc
    actions: list[Action] = parse_actions(json_text)
    try:
        for action in actions:
            if action.type == "wait":
                # wait 中は shared lock を保持しない (design v2 §6)。
                time.sleep(max(action.ms, 0) / 1000)
                continue

            def _do(single=action):
                fence = _active_fence_or_none(g, resolved)
                _bind_active_cli_session(g, resolved)
                adapter = make_adapter(
                    g, game, state=state, tmux=Tmux(), xkit=XKit(g.display.name), fence=fence
                )
                adapter.act(single)

            shared_section(g.state_dir, _do)
    except StateCorruptError as exc:
        raise CliError(f"canonical state が読み込めません: {exc}") from exc
    except GameSwitchBusyError as exc:
        raise CliError(f"ゲーム切替が進行中のため入力できません: {exc}") from exc
    print(f"docich: {len(actions)} 件のアクションを送信しました ({resolved})")
    return 0


def _bind_active_cli_session(g: GlobalConfig, resolved: str) -> None:
    """要求ゲームが canonical active と一致し、CLI adapter なら、legacy
    adapter の観測/入力先を世代別 session に束縛する (互換層)。

    RUNTIME_GAME_SESSION_ENV は legacy CliGameAdapter が参照する。
    一致しない場合は継承済みの stale binding を消して fail-closed にする
    (generation-scoped agent 等の subprocess から呼ばれた場合に旧 session
    へ誤注入しない)。
    """
    active = _read_active_runtime(g)
    if (
        active is not None
        and active.get("game") == resolved
        and active.get("adapter") == "cli"
    ):
        session = active.get("adapter_session")
        if isinstance(session, str) and session:
            os.environ[RUNTIME_GAME_SESSION_ENV] = session
            return
    os.environ.pop(RUNTIME_GAME_SESSION_ENV, None)


def cmd_ra_cmd(g: GlobalConfig, cmd_parts: list[str]) -> int:
    cmd_text = " ".join(cmd_parts)
    try:
        port = _read_ra_port(g)
    except StateCorruptError as exc:
        raise CliError(f"canonical state が読み込めません: {exc}") from exc
    if port is None:
        raise CliError("送信対象の RetroArch runtime がありません (RetroArch ゲームを起動してください)")
    reply = send_ra_cmd(cmd_text, port=port)
    if reply is not None:
        print(reply)
    else:
        print("docich: 返信がありませんでした (タイムアウト)", file=sys.stderr)
    return 0


def _read_ra_port(g: GlobalConfig) -> int | None:
    """active が RetroArch runtime なら世代別 port、それ以外は None。

    canonical が存在する状態では fixed port へフォールバックしない
    (残存 legacy への誤送信を防ぐ)。canonical 未作成時だけ fixed port。
    """
    store = GameSwitchStore(g.state_dir)
    canonical, needs_write = store.canonical.load()
    if needs_write:
        return NETWORK_CMD_PORT
    active = canonical.get("active")
    if (
        isinstance(active, dict)
        and active.get("adapter") == "retroarch"
        and isinstance(active.get("generation"), int)
    ):
        return retroarch_network_port(active["generation"])
    return None


# ---------------------------------------------------------------------------
# run <component> [name]  (internal, invoked from inside tmux windows)
# ---------------------------------------------------------------------------


def cmd_run(g: GlobalConfig, args) -> int:
    component = args.component
    name = args.name
    runtime_kwargs = {
        "runtime_id": getattr(args, "runtime_id", None),
        "generation": getattr(args, "generation", None),
        "lease_id": getattr(args, "lease_id", None),
    }
    if component == "display":
        return _run_display(g)
    if component == "audio":
        return _run_audio(g)
    if component == "stream":
        return _run_stream(g)
    if component == "game":
        if not name:
            raise CliError("`docich run game <name>` にはゲーム名が必要です")
        return _run_game(g, name)
    if component == "agent":
        if not name:
            raise CliError("`docich run agent <name>` にはゲーム名が必要です")
        if any(v is not None for v in runtime_kwargs.values()) and not all(
            v is not None for v in runtime_kwargs.values()
        ):
            raise CliError(
                "--runtime-id / --generation / --lease-id はすべて指定してください"
            )
        return _run_agent(g, name, **runtime_kwargs)
    if component == "watchdog":
        return _run_watchdog(g)
    if component == "trading":
        return _run_trading(g)
    raise CliError(f"未知のコンポーネントです: {component}")


def _run_display(g: GlobalConfig) -> int:
    d = g.display

    # An external display (for example Soren's :99) is owned by its existing
    # runtime.  Keep the internal ``run display`` entry point harmless even if
    # it is invoked directly from an old tmux window or stale command.
    if not d.managed:
        print(f"docich: 外部所有ディスプレイ {d.name} のため Xvfb は起動しません", flush=True)
        return 0

    def build():
        cmd = ["Xvfb", d.name, "-screen", "0", f"{d.width}x{d.height}x{d.color_depth}", "-nolisten", "tcp"]
        return cmd, {}

    run_loop("display", g, build)
    return 0


# --- audio: soren と同居するため、既存デーモンの有無で経路を分ける (architecture.md §0) ---


def _pactl_alive() -> bool:
    r = procs.run(["pactl", "info"])
    return r.returncode == 0


def _sink_exists(sink_name: str) -> bool:
    r = procs.run(["pactl", "list", "short", "sinks"])
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == sink_name:
            return True
    return False


def _load_null_sink(sink_name: str) -> None:
    # 既にロード済みの場合はエラーになるが無視してよい
    procs.run(
        [
            "pactl", "load-module", "module-null-sink",
            f"sink_name={sink_name}",
            f"sink_properties=device.description={sink_name}",
        ]
    )


def _apply_default_sink(g: GlobalConfig) -> None:
    # set_default=true にしない限り set-default-sink はしない (soren 共存。§0)
    if g.audio.set_default:
        procs.run(["pactl", "set-default-sink", g.audio.sink_name])


def _wait_pactl_ready(timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _pactl_alive():
            return True
        time.sleep(0.5)
    return _pactl_alive()


def _run_audio(g: GlobalConfig) -> int:
    if _pactl_alive():
        print("docich: 既存の PulseAudio デーモンを検出しました。再利用します (二重起動しません)")
        return _run_audio_reuse(g)
    print("docich: PulseAudio デーモンが見つからないため新規に起動します")
    return _run_audio_spawn(g)


def _run_audio_reuse(g: GlobalConfig) -> int:
    a = g.audio

    def fn() -> None:
        if not _sink_exists(a.sink_name):
            _load_null_sink(a.sink_name)
        _apply_default_sink(g)
        while True:
            time.sleep(AUDIO_SINK_POLL_S)
            if not _pactl_alive():
                # 既存デーモンが消えた。backoff 後に再度 pactl info から判定し直す
                return
            if not _sink_exists(a.sink_name):
                _load_null_sink(a.sink_name)
                _apply_default_sink(g)

    run_callable_loop("audio", g, fn)
    return 0


def _run_audio_spawn(g: GlobalConfig) -> int:
    a = g.audio

    def build():
        return ["pulseaudio", "--daemonize=no", "--exit-idle-time=-1"], {}

    def post_start(_p):
        if _wait_pactl_ready(PACTL_READY_TIMEOUT_S):
            if not _sink_exists(a.sink_name):
                _load_null_sink(a.sink_name)
            _apply_default_sink(g)

    run_loop("audio", g, build, post_start=post_start)
    return 0


def _run_stream(g: GlobalConfig) -> int:
    xkit = XKit(g.display.name)

    def pre():
        xkit.wait_display()

    def build():
        runtime = resolve_runtime(g, mask_key=False)
        if runtime.captions_active:
            try:
                ensure_caption_socket_parent(g.captions.socket_path)
            except CaptionSocketDirectoryError as exc:
                runtime = StreamRuntime(
                    command=build_ffmpeg_cmd(
                        g,
                        mask_key=False,
                        captions_enabled=False,
                        ffmpeg_bin=runtime.command[0],
                    ),
                    captions_active=False,
                    caption_detail=f"fail-open: {exc}",
                )
        if g.captions.enabled and not runtime.captions_active:
            print(f"docich: 警告: 字幕を無効化して配信を継続します ({runtime.caption_detail})")
        cmd = runtime.command
        env_extra = {}
        if g.audio.enabled:
            env_extra["PULSE_SINK"] = g.audio.sink_name
        return cmd, env_extra

    run_loop(
        "stream",
        g,
        build,
        pre=pre,
        log_command=lambda command: redact_stream_command(command, g),
    )
    return 0


def _run_game(g: GlobalConfig, name: str) -> int:
    game = load_game(g, name)
    state = State(g)
    tmux = Tmux()
    xkit = XKit(g.display.name)

    def build():
        # respawn 毎に adapter を作り直すことで、adapter 未実装 (AdapterError) 等も
        # supervise の backoff リトライに乗せて優雅に扱う。
        adapter = make_adapter(g, game, state=state, tmux=tmux, xkit=xkit)
        adapter.prepare()
        return adapter.command(), adapter.env()

    run_loop("game", g, build)
    return 0


def _run_agent(
    g: GlobalConfig,
    name: str,
    *,
    runtime_id: str | None = None,
    generation: int | None = None,
    lease_id: str | None = None,
) -> int:
    load_game(g, name)  # 早期検証: ゲーム名が不正なら即エラーにする

    def fn() -> None:
        # agent/loop.py は後続エージェントが実装する。未実装の間は ImportError が
        # run_callable_loop に捕捉され、ログ+backoff で待機し続ける。
        from .agent.loop import run_agent

        run_agent(g, name, runtime_id=runtime_id, generation=generation, lease_id=lease_id)

    run_callable_loop("agent", g, fn)
    return 0


def _run_watchdog(g: GlobalConfig) -> int:
    def fn() -> None:
        from .watchdog import run_watchdog

        run_watchdog(g)

    run_callable_loop("watchdog", g, fn)
    return 0


def _run_trading(g: GlobalConfig) -> int:
    def fn() -> None:
        # Lazy import keeps the optional CCXT dependency out of normal docich startup.
        from .trading.worker import run_paper_worker

        run_paper_worker(g)

    run_callable_loop("trading", g, fn)
    return 0
