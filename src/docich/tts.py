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
import tempfile

from .config import ConfigError, GlobalConfig, load_game
from .procs import run


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

    def repro(self) -> str:
        parts = [f"cwd={self.cwd}"]
        for key, value in sorted(self.env.items()):
            parts.append(f"{key}={value}")
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


def _env_for(g: GlobalConfig, game_name: str, queue_dir: Path) -> dict[str, str]:
    env = {
        "SAY_CONTEXT_LABEL": "docich",
        "SAY_CC_TEXT": "",
        "DOCICH_CC_ENABLED": "0",
        # The referenced script sources lib/outbound_queue.sh.  Redirect its
        # queue so a reference run can never publish chat to Twitch.
        "OUTBOUND_CHAT_QUEUE_DIR": str(queue_dir),
    }
    if g.audio.enabled:
        env["PULSE_SINK"] = g.audio.sink_name
        env["SAY_AUDIO_DEVICE"] = g.audio.sink_name
    return env


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
) -> TtsInvocation:
    if rate < 1:
        raise TtsError("rate は 1 以上である必要があります")
    if pre_delay < 0:
        raise TtsError("pre-delay は 0 以上である必要があります")
    if render_only and (wav_playlist or caption_chunks):
        raise TtsError("--render-only と --wav-playlist/--caption-chunks は併用できません")
    if bool(wav_playlist) != bool(caption_chunks):
        raise TtsError("--wav-playlist と --caption-chunks は常にセットで指定してください")

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

    env = _env_for(g, game_name, queue_dir=Path(tmp_dir) / "outbound")
    if env_overrides:
        env.update(env_overrides)

    return TtsInvocation(
        script_path=script,
        cwd=root,
        argv=argv,
        env=env,
        rcs_note="rc 0=再生完了/1=失敗/2=引数エラー/75=render保留",
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
) -> tuple[int, str]:
    """Build and optionally execute the reference invocation.

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
    )
    if dry_run:
        return 0, inv.repro()

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
    try:
        rc, detail = run_tts(
            g,
            game_name=args.game,
            text_file=args.file,
            rate=args.rate,
            pre_delay=args.pre_delay,
            render_only=args.render_only,
            render_output=args.output,
            wav_playlist=args.wav_playlist,
            caption_chunks=args.caption_chunks,
            dry_run=args.dry_run,
        )
    except (ConfigError, TtsError, OSError) as exc:
        raise TtsError(str(exc)) from exc
    if args.dry_run:
        print(f"docich: dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: say 参照実行がエラー終了しました (rc={rc})", flush=True)
        return rc
    print("docich: say 完了")
    return 0
