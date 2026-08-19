"""docich CLI: argparse サブコマンド (architecture.md §7)."""
from __future__ import annotations

import argparse
import glob
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

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
from .adapters import AdapterError, make_adapter
from .config import ConfigError, GlobalConfig, list_games, load_game, load_global
from .netcmd import send_ra_cmd
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
from .tmux import Tmux
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
    sub.add_parser("down", help="全コンポーネントを停止する")

    p_start = sub.add_parser("start", help="ゲームを起動する")
    p_start.add_argument("game", help="ゲーム名 (config/games/<name>.toml)")

    sub.add_parser("stop", help="現在のゲームを停止する")

    p_switch = sub.add_parser("switch", help="ゲームを切り替える (stop + start)")
    p_switch.add_argument("game", help="切り替え先のゲーム名")

    p_rotate = sub.add_parser("rotate", help="[rotation] games を順に切り替える (時間割ローテーション)")
    p_rotate.add_argument(
        "--dry-run", action="store_true", help="切り替えを実行せず、切替先のゲーム名を表示するだけにする"
    )

    sub.add_parser("status", help="各コンポーネントの状態を表示する")

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

    p_webui = sub.add_parser("webui", help="モデルチェーン / バックオフ管理 Web UI を起動する (Tailscale経由)")
    p_webui.add_argument("--bind", metavar="ADDR", help="バインドアドレス (既定 127.0.0.1)")
    p_webui.add_argument("--port", type=int, metavar="PORT", help="ポート (既定 8787)")
    p_webui.add_argument("--soren-root", metavar="PATH", help="Soren ルート (既定 games/soviet_now または g.webui.soren_root)")
    p_webui.add_argument("--read-only", action="store_true", help="読み取り専用で起動する")
    p_webui.add_argument("--dry-run", action="store_true", help="起動せず設定解決結果だけ表示する")

    p_run = sub.add_parser("run", help="(内部用) tmux window 内で監督ループを実行する")
    p_run.add_argument(
        "component", choices=["display", "audio", "stream", "game", "agent", "watchdog"]
    )
    p_run.add_argument("name", nargs="?", help="game/agent の場合のゲーム名")

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
        return cmd_down(g)
    if command == "start":
        return cmd_start(g, args.game)
    if command == "stop":
        return cmd_stop(g)
    if command == "switch":
        return cmd_switch(g, args.game)
    if command == "rotate":
        return cmd_rotate(g, args.dry_run)
    if command == "status":
        return cmd_status(g)
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
        return cmd_run(g, args.component, args.name)
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

    if not tmux.has_window("display"):
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
    # (audio/stream と異なり、無効メッセージを毎回出すほどの情報価値がない)。

    xkit = XKit(g.display.name)
    if xkit.wait_display():
        print(f"docich: ディスプレイ {g.display.name} の準備ができました")
    else:
        print(f"docich: ディスプレイ {g.display.name} の準備を確認できませんでした (タイムアウト)", file=sys.stderr)
    return 0


def cmd_down(g: GlobalConfig) -> int:
    state = State(g)
    if state.current_game() is not None:
        cmd_stop(g)
    tmux = Tmux()
    tmux.kill_session()
    state.clear_current_game()
    print("docich: 停止しました")
    return 0


def cmd_start(g: GlobalConfig, name: str) -> int:
    tmux = Tmux()
    if not tmux.has_window("display"):
        raise CliError("display window がありません。先に `docich up` を実行してください")

    game = load_game(g, name)
    state = State(g)
    xkit = XKit(g.display.name)
    # import/構築の検証のみ (実際の起動は `docich run game <name>` 側で行う)
    make_adapter(g, game, state=state, tmux=tmux, xkit=xkit)

    if tmux.has_window("game"):
        print("docich: game window は既に起動しています (先に `docich stop` してください)", file=sys.stderr)
    else:
        tmux.new_window("game", _run_argv(g, "game", game.name))
        print(f"docich: game window を起動しました ({game.name})")

    if game.agent.enabled:
        if tmux.has_window("agent"):
            print("docich: agent window は既に起動しています", file=sys.stderr)
        else:
            tmux.new_window("agent", _run_argv(g, "agent", game.name))
            print(f"docich: agent window を起動しました ({game.name})")

    state.set_current_game(game.name)
    print(f"docich: 現在のゲーム = {game.name}")
    return 0


def cmd_stop(g: GlobalConfig) -> int:
    state = State(g)
    tmux = Tmux()
    current = state.current_game()

    tmux.kill_window("agent")
    tmux.kill_window("game")

    if current is not None:
        try:
            game = load_game(g, current)
            xkit = XKit(g.display.name)
            adapter = make_adapter(g, game, state=state, tmux=tmux, xkit=xkit)
            adapter.cleanup()
        except Exception as exc:
            # cleanup 失敗は警告ログのみで継続する (spec: 失敗はログのみ)
            print(f"docich: 警告: {current} の cleanup に失敗しました: {exc}", file=sys.stderr)

    state.clear_current_game()
    if current is not None:
        print(f"docich: ゲームを停止しました ({current})")
    else:
        print("docich: 実行中のゲームはありませんでした")
    return 0


def cmd_switch(g: GlobalConfig, name: str) -> int:
    state = State(g)
    if state.current_game() is not None:
        cmd_stop(g)
    return cmd_start(g, name)


def cmd_rotate(g: GlobalConfig, dry_run: bool) -> int:
    if not g.rotation.games:
        raise CliError("[rotation] games を設定してください (config/docich.toml)")
    state = State(g)
    target = next_rotation_game(g.rotation.games, state.current_game())
    if dry_run:
        print(f"docich: rotate 切替先 = {target} (dry-run のため切り替えません)")
        return 0
    print(f"docich: rotate 切替先 = {target}")
    return cmd_switch(g, target)


def cmd_status(g: GlobalConfig) -> int:
    tmux = Tmux()
    state = State(g)
    xkit = XKit(g.display.name)

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
    return 0


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


def _resolve_game_name(state: State, name: str | None, *, dash_means_current: bool) -> str:
    if dash_means_current and name == "-":
        name = None
    if name:
        return name
    current = state.current_game()
    if not current:
        raise CliError("現在実行中のゲームがありません。ゲーム名を指定してください")
    return current


def cmd_obs(g: GlobalConfig, name: str | None) -> int:
    state = State(g)
    resolved = _resolve_game_name(state, name, dash_means_current=False)
    game = load_game(g, resolved)
    tmux = Tmux()
    xkit = XKit(g.display.name)
    adapter = make_adapter(g, game, state=state, tmux=tmux, xkit=xkit)
    obs = adapter.observe()
    print(obs.to_json())
    return 0


def cmd_send(g: GlobalConfig, game_arg: str, json_text: str) -> int:
    state = State(g)
    resolved = _resolve_game_name(state, game_arg, dash_means_current=True)
    game = load_game(g, resolved)
    tmux = Tmux()
    xkit = XKit(g.display.name)
    adapter = make_adapter(g, game, state=state, tmux=tmux, xkit=xkit)

    actions: list[Action] = parse_actions(json_text)
    for action in actions:
        if action.type == "wait":
            time.sleep(max(action.ms, 0) / 1000)
        else:
            adapter.act(action)
    print(f"docich: {len(actions)} 件のアクションを送信しました ({resolved})")
    return 0


def cmd_ra_cmd(g: GlobalConfig, cmd_parts: list[str]) -> int:
    cmd_text = " ".join(cmd_parts)
    reply = send_ra_cmd(cmd_text)
    if reply is not None:
        print(reply)
    else:
        print("docich: 返信がありませんでした (タイムアウト)", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# run <component> [name]  (internal, invoked from inside tmux windows)
# ---------------------------------------------------------------------------


def cmd_run(g: GlobalConfig, component: str, name: str | None) -> int:
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
        return _run_agent(g, name)
    if component == "watchdog":
        return _run_watchdog(g)
    raise CliError(f"未知のコンポーネントです: {component}")


def _run_display(g: GlobalConfig) -> int:
    d = g.display

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


def _run_agent(g: GlobalConfig, name: str) -> int:
    load_game(g, name)  # 早期検証: ゲーム名が不正なら即エラーにする

    def fn() -> None:
        # agent/loop.py は後続エージェントが実装する。未実装の間は ImportError が
        # run_callable_loop に捕捉され、ログ+backoff で待機し続ける。
        from .agent.loop import run_agent

        run_agent(g, name)

    run_callable_loop("agent", g, fn)
    return 0


def _run_watchdog(g: GlobalConfig) -> int:
    def fn() -> None:
        from .watchdog import run_watchdog

        run_watchdog(g)

    run_callable_loop("watchdog", g, fn)
    return 0
