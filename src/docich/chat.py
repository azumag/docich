"""Referencing-run wrapper for soviet_now broadcast functions (common_parts_chat.md).

docich does not copy or own the comment/radio implementation.  This module
builds a fixed bash wrapper that sources ``eloop_lib.sh`` (the same source
order as production) and calls one allowlisted function with allowlisted
arguments.  Chat publishing is disabled by redirecting ``OUTBOUND_CHAT_QUEUE_DIR``
to a private temp dir; AI execution only happens when the user explicitly
allows a real run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
from shlex import quote
import tempfile

from .config import ConfigError, GlobalConfig
from .procs import run
from .tts import TtsError, game_submodule


ALLOWED_CHAT_SOURCES = {"twitch", "youtube"}
ALLOWED_BROADCAST_FUNCTIONS = {
    "comment": "generate_comment_response",
    "radio": "_radio_generate_and_play",
}
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Fixed wrapper.  No user text or shell metacharacters ever enter this file;
# the function name and arguments are validated by docich before execution.
WRAPPER = """\
#!/usr/bin/env bash
# docich reference-run wrapper for soviet_now broadcast functions.
set -uo pipefail
ROOT="$1"; shift
cd "$ROOT" || exit 2
export ELOOP_LIB_DIR="$ROOT"
export EXPLORE_MODE="${EXPLORE_MODE:-0}"
[ -f "eloop_lib.sh" ] || exit 2
source ./eloop_lib.sh
"$@"
"""


class ChatError(TtsError):
    """User-facing broadcast reference error."""


@dataclass(frozen=True)
class ChatInvocation:
    script_path: Path
    cwd: Path
    argv: list[str]
    env: dict[str, str]
    fn_name: str
    rcs_note: str = ""

    def repro(self) -> str:
        parts = [f"cwd={self.cwd}"]
        for key, value in sorted(self.env.items()):
            parts.append(f"{key}={value}")
        parts.append(f"function={self.fn_name}")
        parts.append("argv=" + " ".join(quote(str(part)) for part in self.argv))
        return " ".join(parts)


def _safe_token(value: str, what: str) -> str:
    if not SAFE_TOKEN_RE.match(value):
        raise ChatError(f"{what} は安全な値に限定されます: {value!r}")
    return value


def _broadcast_root(g: GlobalConfig, game_name: str) -> Path:
    root = game_submodule(g, game_name)
    if not (root / "broadcast").is_dir():
        raise ChatError(
            f"ゲーム '{game_name}' に broadcast/ が見つかりません: {root / 'broadcast'}"
        )
    if not (root / "eloop_lib.sh").is_file():
        raise ChatError(f"eloop_lib.sh が見つかりません: {root / 'eloop_lib.sh'}")
    return root


def _env_for(g: GlobalConfig, queue_dir: Path) -> dict[str, str]:
    env = {
        # The referenced script sources lib/outbound_queue.sh.  Redirect its
        # queue so a reference run can never publish chat to Twitch.
        "OUTBOUND_CHAT_QUEUE_DIR": str(queue_dir),
        "SAY_CONTEXT_LABEL": "docich",
        "SAY_CC_TEXT": "",
        "DOCICH_CC_ENABLED": "0",
    }
    if g.audio.enabled:
        env["PULSE_SINK"] = g.audio.sink_name
        env["SAY_AUDIO_DEVICE"] = g.audio.sink_name
    return env


def _write_wrapper(tmp_dir: Path) -> Path:
    wrapper = tmp_dir / "broadcast_ref.sh"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    wrapper.chmod(0o700)
    return wrapper


def build_comment_invocation(
    g: GlobalConfig,
    *,
    game_name: str,
    source: str = "twitch",
) -> ChatInvocation:
    source = _safe_token(source, "チャット source")
    if source not in ALLOWED_CHAT_SOURCES:
        raise ChatError(
            f"チャット source は {sorted(ALLOWED_CHAT_SOURCES)} に限定されます: {source}"
        )
    root = _broadcast_root(g, game_name)
    script = root / "broadcast" / "comment.sh"
    if not script.is_file():
        raise ChatError(f"broadcast/comment.sh が見つかりません: {script}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="docich-chat-"))
    wrapper = _write_wrapper(tmp_dir)
    fn = ALLOWED_BROADCAST_FUNCTIONS["comment"]
    argv = ["bash", str(wrapper), str(root), fn, source]
    env = _env_for(g, tmp_dir / "outbound")
    return ChatInvocation(
        script_path=script,
        cwd=root,
        argv=argv,
        env=env,
        fn_name=fn,
        rcs_note="rc 0=成功/1=生成失敗/2=引数エラー",
    )


def build_radio_invocation(
    g: GlobalConfig,
    *,
    game_name: str,
    prompt_file: Path | None = None,
    topic: str | None = None,
    corner: str = "main",
    game_num: int = 0,
    score: str = "",
) -> ChatInvocation:
    if bool(prompt_file) == bool(topic):
        raise ChatError("--prompt-file と --topic はどちらか一方だけ指定してください")
    if game_num < 0:
        raise ChatError("--game-num は 0 以上である必要があります")
    corner = _safe_token(corner, "コーナー名")
    if score:
        _safe_token(score, "score")

    root = _broadcast_root(g, game_name)
    script = root / "broadcast" / "radio_engine.sh"
    if not script.is_file():
        raise ChatError(f"broadcast/radio_engine.sh が見つかりません: {script}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="docich-radio-"))
    if topic is not None:
        prompt_path = tmp_dir / "prompt.txt"
        prompt_path.write_text(topic + "\n", encoding="utf-8")
    else:
        prompt_path = Path(prompt_file).resolve()
        if not prompt_path.is_file():
            raise ChatError(f"プロンプトファイルが見つかりません: {prompt_path}")

    wrapper = _write_wrapper(tmp_dir)
    fn = ALLOWED_BROADCAST_FUNCTIONS["radio"]
    argv = [
        "bash",
        str(wrapper),
        str(root),
        fn,
        str(prompt_path),
        str(game_num),
        score,
        corner,
    ]
    env = _env_for(g, tmp_dir / "outbound")
    return ChatInvocation(
        script_path=script,
        cwd=root,
        argv=argv,
        env=env,
        fn_name=fn,
        rcs_note="rc 0=生成完了/1=失敗/2=引数エラー",
    )


def _run(
    inv: ChatInvocation,
    *,
    allow_env: str,
    dry_run: bool,
    timeout: float | None,
) -> tuple[int, str]:
    if dry_run:
        return 0, inv.repro()
    if os.environ.get(allow_env) != "1":
        raise ChatError(
            f"実実行は既定で無効です。--dry-run で確認するか、{allow_env}=1 で"
            "明示許可してください (AI 実行・音声再生を含むため)"
        )
    result = run(
        inv.argv,
        cwd=str(inv.cwd),
        env_extra=inv.env,
        timeout=timeout,
        capture=True,
    )
    return result.returncode, (result.stderr or "")


def run_comment(
    g: GlobalConfig,
    *,
    game_name: str,
    source: str = "twitch",
    dry_run: bool = False,
    timeout: float | None = None,
) -> tuple[int, str]:
    inv = build_comment_invocation(g, game_name=game_name, source=source)
    return _run(inv, allow_env="DOCICH_ALLOW_REAL_COMMENT", dry_run=dry_run, timeout=timeout)


def run_radio(
    g: GlobalConfig,
    *,
    game_name: str,
    prompt_file: Path | None = None,
    topic: str | None = None,
    corner: str = "main",
    game_num: int = 0,
    score: str = "",
    dry_run: bool = False,
    timeout: float | None = None,
) -> tuple[int, str]:
    inv = build_radio_invocation(
        g,
        game_name=game_name,
        prompt_file=prompt_file,
        topic=topic,
        corner=corner,
        game_num=game_num,
        score=score,
    )
    return _run(inv, allow_env="DOCICH_ALLOW_REAL_RADIO", dry_run=dry_run, timeout=timeout)


def cli_chat(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    try:
        rc, detail = run_comment(
            g,
            game_name=args.game,
            source=args.source,
            dry_run=args.dry_run,
        )
    except (ConfigError, ChatError, OSError) as exc:
        raise ChatError(str(exc)) from exc
    if args.dry_run:
        print(f"docich: chat dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: chat 参照実行がエラー終了しました (rc={rc})", flush=True)
        return rc
    print("docich: chat 完了")
    return 0


def cli_radio(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    try:
        rc, detail = run_radio(
            g,
            game_name=args.game,
            prompt_file=args.prompt_file,
            topic=args.topic,
            corner=args.corner,
            game_num=args.game_num,
            score=args.score,
            dry_run=args.dry_run,
        )
    except (ConfigError, ChatError, OSError) as exc:
        raise ChatError(str(exc)) from exc
    if args.dry_run:
        print(f"docich: radio dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: radio 参照実行がエラー終了しました (rc={rc})", flush=True)
        return rc
    print("docich: radio 完了")
    return 0
