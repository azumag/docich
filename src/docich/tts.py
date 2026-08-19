"""Referencing-run wrapper for submodule TTS scripts (docs/common_parts_tts.md).

docich does not copy or own the soviet_now TTS implementation yet.  This
module resolves an allowlisted script under ``games/<name>/`` and builds the
argv/env/cwd contract used to reference it.  The referenced script keeps its
own queue, temp files, retries, and playback.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from shlex import quote
import sys
import tempfile

from .captions import DEFAULT_SOCKET_PATH
from .config import ConfigError, GlobalConfig, load_game
from .procs import run
from .stream import caption_socket_ready


ALLOWED_TTS_SCRIPTS = {
    "say_enqueue.sh",
}


class TtsError(RuntimeError):
    """User-facing TTS reference error."""


@dataclass(frozen=True)
class TtsInvocation:
    script_path: Path
    cwd: Path
    argv: list[str]
    env: dict[str, str]
    rcs_note: str = ""
    cc_note: str = ""

    def repro(self) -> str:
        parts = [f"cwd={self.cwd}"]
        for key, value in sorted(self.env.items()):
            parts.append(f"{key}={value}")
        if self.cc_note:
            parts.append(f"# {self.cc_note}")
        parts.append("argv=" + " ".join(quote(str(part)) for part in self.argv))
        return " ".join(parts)


def game_submodule(g: GlobalConfig, game_name: str) -> Path:
    """Return the resolved submodule root for a game definition.

    A config may set ``[game] submodule = "games/soviet_now"``.  The path is
    resolved against the repo root, must stay inside the repo tree, must exist
    as a directory, and the TTS entrypoint must be present.  This keeps
    ``games/<name>/...`` references deterministic without allowing arbitrary
    paths or command execution.
    """

    game = load_game(g, game_name)
    configured = game.submodule or f"games/{game.name}"
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = g.repo_root / candidate
    candidate = candidate.resolve()

    repo_root = g.repo_root.resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise TtsError(
            f"ゲーム '{game.name}' の submodule パスはリポジトリ内に限定されます: {configured}"
        ) from exc
    if not candidate.is_dir():
        raise TtsError(
            f"ゲーム '{game.name}' の submodule が見つかりません: {candidate} "
            "(`git submodule update --init` を実行してください)"
        )
    return candidate


def resolve_script(g: GlobalConfig, game_name: str) -> tuple[Path, Path]:
    """Validate and return (script_path, submodule_root)."""

    root = game_submodule(g, game_name)
    script = root / "say_enqueue.sh"
    if script.name not in ALLOWED_TTS_SCRIPTS:
        raise TtsError(f"TTS スクリプトは allowlist にありません: {script.name}")
    if not script.is_file() or not os.access(script, os.X_OK):
        raise TtsError(f"TTS スクリプトが実行可能でありません: {script}")
    return script, root


def resolve_cc_socket(g: GlobalConfig, override: str | None) -> tuple[str, str]:
    """Resolve the caption socket and return (path, reason).

    reason is empty when the socket is ready.  A non-empty reason means the
    reference run continues without captions (fail-open, docs/common_parts_tts.md
    §4.1).
    """

    socket_path = override or g.captions.socket_path or DEFAULT_SOCKET_PATH
    if not socket_path:
        return "", "captions が設定されていません"
    ready, reason = caption_socket_ready(socket_path)
    if not ready:
        return socket_path, f"caption socket未準備のため字幕なしで実行します: {reason}"
    return socket_path, ""


def _env_for(
    g: GlobalConfig,
    game_name: str,
    queue_dir: Path,
    *,
    cc_enabled: bool = False,
    cc_socket: str | None = None,
) -> tuple[dict[str, str], str]:
    env = {
        "SAY_CONTEXT_LABEL": "docich",
        "SAY_CC_TEXT": "",
        "DOCICH_CC_ENABLED": "0",
        # The referenced script sources lib/outbound_queue.sh.  Redirect its
        # queue so a reference run can never publish chat to Twitch.
        "OUTBOUND_CHAT_QUEUE_DIR": str(queue_dir),
    }
    # PulseAudio null-sink isolation is Linux-only.  On macOS the referenced
    # script plays through `say -a <device>` / afplay, and forcing a Linux sink
    # name here makes every real-playback attempt fail device resolution.
    if g.audio.enabled and sys.platform.startswith("linux"):
        env["PULSE_SINK"] = g.audio.sink_name
        env["SAY_AUDIO_DEVICE"] = g.audio.sink_name
    cc_note = ""
    if cc_enabled:
        socket_path, reason = resolve_cc_socket(g, cc_socket)
        if reason:
            cc_note = reason
        else:
            env["DOCICH_CC_ENABLED"] = "1"
            env["DOCICH_CC_SOCKET"] = socket_path
    return env, cc_note


def build_invocation(
    g: GlobalConfig,
    *,
    game_name: str,
    text_file: Path,
    rate: int,
    pre_delay: int,
    render_only: bool = False,
    render_output: Path | None = None,
    wav_playlist: Path | None = None,
    caption_chunks: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    cc_enabled: bool = False,
    cc_socket: str | None = None,
) -> TtsInvocation:
    if rate < 1:
        raise TtsError("rate は 1 以上である必要があります")
    if pre_delay < 0:
        raise TtsError("pre-delay は 0 以上である必要があります")
    if render_only and (wav_playlist or caption_chunks):
        raise TtsError("--render-only と --wav-playlist/--caption-chunks は併用できません")
    if bool(wav_playlist) != bool(caption_chunks):
        raise TtsError("--wav-playlist と --caption-chunks は常にセットで指定してください")
    if cc_enabled and render_only:
        raise TtsError("--cc と --render-only は併用できません (字幕は実再生時にのみ有効)")

    script, root = resolve_script(g, game_name)

    content = Path(text_file)
    if not content.is_absolute():
        content = g.repo_root / content
    content = content.resolve()
    if not content.is_file():
        raise TtsError(f"テキストファイルが見つかりません: {content}")

    # Copy into a private temp file so the referenced script can copy/delete
    # freely without touching the caller's file.
    tmp_dir = tempfile.mkdtemp(prefix="docich-tts-")
    content_copy = Path(tmp_dir) / "content.txt"
    try:
        shutil.copy2(content, content_copy)
    except OSError as exc:
        raise TtsError(f"テキストファイルを一時コピーできません: {exc}") from exc

    argv = [str(script), str(content_copy), str(rate), str(pre_delay)]
    if render_only:
        if render_output is None:
            raise TtsError("--render-only には -o 出力先が必要です")
        argv[1:1] = ["--render-only", str(render_output)]
    if wav_playlist is not None:
        argv[1:1] = [
            "--wav-playlist",
            str(wav_playlist),
            "--caption-chunks",
            str(caption_chunks),
        ]

    env, cc_note = _env_for(
        g,
        game_name,
        queue_dir=Path(tmp_dir) / "outbound",
        cc_enabled=cc_enabled,
        cc_socket=cc_socket,
    )
    if env_overrides:
        env.update(env_overrides)

    return TtsInvocation(
        script_path=script,
        cwd=root,
        argv=argv,
        env=env,
        rcs_note="rc 0=再生完了/1=失敗/2=引数エラー/75=render保留",
        cc_note=cc_note,
    )


def run_tts(
    g: GlobalConfig,
    *,
    game_name: str,
    text_file: Path,
    rate: int,
    pre_delay: int,
    render_only: bool = False,
    render_output: Path | None = None,
    wav_playlist: Path | None = None,
    caption_chunks: Path | None = None,
    dry_run: bool = False,
    timeout: float | None = None,
    cc_enabled: bool = False,
    cc_socket: str | None = None,
    warn: callable | None = None,
) -> tuple[int, str]:
    """Build and optionally execute the reference invocation.

    Real playback (no ``--render-only``) requires ``DOCICH_ALLOW_REAL_PLAYBACK=1``.
    The referenced ``say_enqueue.sh`` owns ``tmp/.say_queue/`` under the submodule
    root, so playback is only permitted in a checkout that is not the production
    soviet_now tree (docs/common_parts_tts.md §4.2).

    Returns (returncode, stderr_text) for tests without spawning audio.  The
    caller prints a dry-run preview and docich errors as needed.
    """

    inv = build_invocation(
        g,
        game_name=game_name,
        text_file=text_file,
        rate=rate,
        pre_delay=pre_delay,
        render_only=render_only,
        render_output=render_output,
        wav_playlist=wav_playlist,
        caption_chunks=caption_chunks,
        cc_enabled=cc_enabled,
        cc_socket=cc_socket,
    )
    if dry_run:
        return 0, inv.repro()

    if inv.cc_note and warn is not None:
        warn(inv.cc_note)

    if not render_only and os.environ.get("DOCICH_ALLOW_REAL_PLAYBACK") != "1":
        raise TtsError(
            "実再生は既定で無効です。合成のみの場合は --render-only を指定するか、"
            "DOCICH_ALLOW_REAL_PLAYBACK=1 で明示許可してください "
            "(キュー分離: docs/common_parts_tts.md §4.2)"
        )

    result = run(
        inv.argv,
        cwd=str(inv.cwd),
        env_extra=inv.env,
        timeout=timeout,
        capture=True,
    )
    return result.returncode, (result.stderr or "")


def cli_say(args) -> int:
    """Execute the argparse result for ``docich say``."""

    from .cli import _load_global

    g = _load_global(args)
    text_parts = list(getattr(args, "text", None) or [])
    if args.file and text_parts:
        raise TtsError("-f と直接テキスト指定は併用できません")
    if not args.file and not text_parts:
        raise TtsError("読み上げるテキストを指定してください (-f または引数)")

    text_file = args.file
    temp_dir = None
    if text_parts:
        import tempfile

        temp_dir = tempfile.TemporaryDirectory(prefix="docich-say-text-")
        text_path = Path(temp_dir.name) / "content.txt"
        text_path.write_text(" ".join(text_parts), encoding="utf-8")
        text_file = text_path
    try:
        rc, detail = run_tts(
            g,
            game_name=args.game,
            text_file=text_file,
            rate=args.rate,
            pre_delay=args.pre_delay,
            render_only=args.render_only,
            render_output=args.output,
            wav_playlist=args.wav_playlist,
            caption_chunks=args.caption_chunks,
            dry_run=args.dry_run,
            cc_enabled=getattr(args, "cc", False),
            cc_socket=getattr(args, "cc_socket", None),
            warn=lambda m: print(f"docich: 警告: {m}", file=sys.stderr),
        )
    except (ConfigError, TtsError, OSError) as exc:
        raise TtsError(str(exc)) from exc
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
    if args.dry_run:
        print(f"docich: dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: say 参照実行がエラー終了しました (rc={rc})", flush=True)
        return rc
    print("docich: say 完了")
    return 0
