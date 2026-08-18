"""Reference-run wrapper for soviet_now's AI dispatch (common_parts_chat_c4.md C-S1).

docich does not copy or own the AI dispatch implementation.  This module builds
a fixed bash wrapper that sources ``eloop_lib.sh`` (the same source order as
production) and calls one allowlisted function (``ai_generate_list``) with
allowlisted arguments.  A real run only happens when the user explicitly allows
it (``DOCICH_ALLOW_REAL_AI=1``); by default the invocation is shown as a dry-run.
Backoff/lock state is redirected to a private temp dir so a reference run never
touches production state.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import tempfile

from .config import ConfigError, GlobalConfig
from .procs import run
from .tts import TtsError, game_submodule


# Only the list-with-backoff entrypoint is exposed.  ``ai_generate`` (single
# primary + fallback) is used by classification inside comment.sh and is not a
# stable docich-facing contract.  The validator argument is never forwarded;
# docich leaves validation to the caller that owns the prompt format.
AI_FUNCTION = "ai_generate_list"

SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# Fixed wrapper.  No user text or shell metacharacters ever enter this file;
# the function name and arguments are validated by docich before execution.
WRAPPER = """\
#!/usr/bin/env bash
# docich reference-run wrapper for soviet_now ai_generate_list.
set -uo pipefail
ROOT="$1"; shift
cd "$ROOT" || exit 2
export ELOOP_LIB_DIR="$ROOT"
export EXPLORE_MODE="${EXPLORE_MODE:-0}"
[ -f "eloop_lib.sh" ] || exit 2
source ./eloop_lib.sh
"$@"
"""


class AiError(TtsError):
    """User-facing AI dispatch reference error."""


@dataclass(frozen=True)
class AiInvocation:
    script_path: Path
    cwd: Path
    argv: list[str]
    env: dict[str, str]
    fn_name: str
    agents: str
    rcs_note: str = ""

    def repro(self) -> str:
        parts = [f"cwd={self.cwd}"]
        for key, value in sorted(self.env.items()):
            parts.append(f"{key}={value}")
        parts.append(f"function={self.fn_name}")
        parts.append("argv=" + " ".join(repr(str(part)) for part in self.argv))
        return " ".join(parts)


def _safe_token(value: str, what: str) -> str:
    if not SAFE_TOKEN_RE.match(value):
        raise AiError(f"{what} は安全な値に限定されます: {value!r}")
    return value


def _validate_agents(agents: str, what: str) -> str:
    """Validate a comma-separated agent identifier list and strip whitespace."""

    items = [a.strip() for a in agents.split(",")]
    if not items or not all(a for a in items):
        raise AiError(f"{what} が空です: {agents!r}")
    for a in items:
        if not AGENT_RE.match(a):
            raise AiError(
                f"{what} に安全でない識別子が含まれます: {a!r} "
                "(英数字 / . _ : - のみ、カンマ区切り)"
            )
    return ",".join(items)


def _validate_label(label: str) -> str:
    """Validate the dispatch label (e.g. ``COMMENT`` or ``RADIO:corner``)."""

    label = _safe_token(label, "ラベル")
    if not (label.startswith("COMMENT") or label.startswith("RADIO")):
        raise AiError("ラベルは COMMENT または RADIO で始まる必要があります")
    return label


def _script_dir(g: GlobalConfig, game_name: str) -> Path:
    root = game_submodule(g, game_name)
    script = root / "lib" / "ai_generate.sh"
    if not script.is_file():
        raise AiError(f"lib/ai_generate.sh が見つかりません: {script}")
    if not (root / "eloop_lib.sh").is_file():
        raise AiError(f"eloop_lib.sh が見つかりません: {root / 'eloop_lib.sh'}")
    return root


def _write_wrapper(tmp_dir: Path) -> Path:
    wrapper = tmp_dir / "ai_ref.sh"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    wrapper.chmod(0o700)
    return wrapper


def _env_for(g: GlobalConfig, state_dir: Path) -> dict[str, str]:
    env = {
        # Redirect backoff/lock state so a reference run never mutates the
        # production tree's tmp/state.  AI generation queue and opencode run
        # locks follow the same rule by pointing at the private state dir.
        "AI_BACKOFF_DIR": str(state_dir / "ai_backoff"),
        "AI_GENERATION_QUEUE_LOCK_DIR": str(state_dir / "ai_generation_locks"),
        "OPENCODE_RUN_LOCK_DIR": str(state_dir / "opencode_run_locks"),
        "SAY_CONTEXT_LABEL": "docich",
        "SAY_CC_TEXT": "",
        "DOCICH_CC_ENABLED": "0",
    }
    if g.audio.enabled:
        env["PULSE_SINK"] = g.audio.sink_name
        env["SAY_AUDIO_DEVICE"] = g.audio.sink_name
    return env


def build_ai_invocation(
    g: GlobalConfig,
    *,
    game_name: str,
    label: str,
    agents: str,
    prompt_file: Path,
    timeout: int | None = None,
    last_agent_file: Path | None = None,
    failure_kind_file: Path | None = None,
) -> AiInvocation:
    label = _validate_label(label)
    agents = _validate_agents(agents, "エージェントリスト")
    prompt_path = Path(prompt_file).resolve()
    if not prompt_path.is_file():
        raise AiError(f"プロンプトファイルが見つかりません: {prompt_path}")

    root = _script_dir(g, game_name)
    tmp_dir = Path(tempfile.mkdtemp(prefix="docich-ai-"))
    wrapper = _write_wrapper(tmp_dir)

    # Write the prompt into the temp dir so the referenced script can read it,
    # and the caller never needs to provide an arbitrary path on the command
    # line.  The prompt path passed to the wrapper is a private temp file.
    local_prompt = tmp_dir / "prompt.txt"
    local_prompt.write_text(prompt_path.read_text(encoding="utf-8"), encoding="utf-8")

    last_file = last_agent_file or (tmp_dir / "last_agent.txt")
    kind_file = failure_kind_file or (tmp_dir / "failure_kind.txt")

    argv = [
        "bash",
        str(wrapper),
        str(root),
        AI_FUNCTION,
        label,
        str(local_prompt),
        agents,
    ]
    if timeout is not None:
        if timeout < 1:
            raise AiError("timeout は 1 以上である必要があります")
        argv.append(str(timeout))
    else:
        # Always keep the timeout slot so the following positional arguments
        # (validator, last_agent_file, failure_kind_file) stay in the right
        # positions for ai_generate_list.
        argv.append("")
    # validator is intentionally omitted; validation belongs to the prompt owner.
    argv.append("")
    argv.append(str(last_file))
    argv.append(str(kind_file))

    env = _env_for(g, tmp_dir)
    return AiInvocation(
        script_path=root / "lib" / "ai_generate.sh",
        cwd=root,
        argv=argv,
        env=env,
        fn_name=AI_FUNCTION,
        agents=agents,
        rcs_note="rc 0=生成成功 / 1=全エージェント失敗 / 79=レート制限 / 124=タイムアウト",
    )


def run_ai(
    g: GlobalConfig,
    *,
    game_name: str,
    label: str,
    agents: str,
    prompt_file: Path,
    timeout: int | None = None,
    dry_run: bool = False,
    timeout_sec: float | None = None,
) -> tuple[int, str]:
    inv = build_ai_invocation(
        g,
        game_name=game_name,
        label=label,
        agents=agents,
        prompt_file=prompt_file,
        timeout=timeout,
    )
    if dry_run:
        return 0, inv.repro()
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        raise AiError(
            "実実行は既定で無効です。--dry-run で確認するか、"
            "DOCICH_ALLOW_REAL_AI=1 で明示許可してください (AI 呼び出しを含むため)"
        )
    result = run(
        inv.argv,
        cwd=str(inv.cwd),
        env_extra=inv.env,
        timeout=timeout_sec,
        capture=True,
    )
    return result.returncode, (result.stderr or "")


def cli_ai(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    try:
        rc, detail = run_ai(
            g,
            game_name=args.game,
            label=args.label,
            agents=args.agents,
            prompt_file=Path(args.prompt_file),
            timeout=args.timeout,
            dry_run=args.dry_run,
            timeout_sec=args.run_timeout,
        )
    except (ConfigError, AiError, OSError) as exc:
        raise AiError(str(exc)) from exc
    if args.dry_run:
        print(f"docich: ai dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: ai 参照実行がエラー終了しました (rc={rc})", flush=True)
        if detail:
            print(detail, file=sys.stderr, flush=True)
        return rc
    print("docich: ai 完了")
    return 0
