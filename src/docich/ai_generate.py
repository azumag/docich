"""Native AI dispatch entrypoint (common_parts_chat_c4.md C-S1, #829 PR-1).

docich owns the LLM dispatch implementation in :mod:`docich.llm` now.  This
module builds a native request (label + agent chain + prompt text, all
validated; no shell, no arbitrary paths forwarded to providers) and runs it
through the golden-compatible chain: validation, backoff, lane queue,
improve-gate, provider fallback, telemetry.  A real run only happens when
the user explicitly allows it (``DOCICH_ALLOW_REAL_AI=1``); by default the
request is shown as a dry-run.  All runtime state stays in a private temp
dir, never in the production tree or any game checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import tempfile

from .config import ConfigError, GlobalConfig
from .llm import budget as budget_mod
from .llm import contracts as contracts_mod
from .llm import policy as policy_mod
from .tts import TtsError

SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# Namespaced provider/model IDs (e.g. OpenRouter) use '/' just as in webui.
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


class AiError(TtsError):
    """User-facing AI dispatch error."""


@dataclass(frozen=True)
class AiInvocation:
    label: str
    agents: str
    prompt_text: str
    timeout: int | None
    last_agent_file: Path
    failure_kind_file: Path
    state_dir: Path

    def repro(self) -> str:
        preview = self.prompt_text[:120].replace("\n", " ")
        return (
            f"native llm label={self.label} agents={self.agents} "
            f"timeout={self.timeout} prompt_chars={len(self.prompt_text)} "
            f"prompt_preview={preview!r}"
        )


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
        if not AGENT_RE.fullmatch(a):
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
    """Build a native dispatch request.

    ``game_name`` is accepted for CLI compatibility but intentionally unused:
    the native backend resolves providers and credentials from docich
    runtime/config, never from a game checkout.
    """
    _ = (g, game_name)
    label = _validate_label(label)
    agents = _validate_agents(agents, "エージェントリスト")
    prompt_path = Path(prompt_file)
    if not prompt_path.is_file():
        raise AiError(f"プロンプトファイルが見つかりません: {prompt_path}")
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AiError(f"プロンプトファイルを読めません: {exc}") from exc
    if timeout is not None and timeout < 1:
        raise AiError("timeout は 1 以上である必要があります")

    state_dir = Path(tempfile.mkdtemp(prefix="docich-ai-"))
    return AiInvocation(
        label=label,
        agents=agents,
        prompt_text=prompt_text,
        timeout=timeout,
        last_agent_file=(
            last_agent_file or (state_dir / "last_agent.txt")
        ),
        failure_kind_file=(
            failure_kind_file or (state_dir / "failure_kind.txt")
        ),
        state_dir=state_dir,
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
    settings = contracts_mod.LlmSettings.from_env()
    chain_budget = (
        budget_mod.ChainBudget(timeout_sec)
        if timeout_sec is not None and timeout_sec > 0
        else None
    )
    rc, text, winner, kind = policy_mod.generate_list(
        settings,
        inv.state_dir,
        inv.label,
        inv.prompt_text,
        inv.agents.split(","),
        timeout=inv.timeout,
        chain_budget=chain_budget,
        last_agent_file=inv.last_agent_file,
        failure_kind_file=inv.failure_kind_file,
    )
    if rc == 0:
        return 0, f"winner={winner} chars={len(text)}"
    return rc, f"failure_kind={kind} rc={rc}"


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
        print(f"docich: ai 生成がエラー終了しました (rc={rc})", flush=True)
        if detail:
            print(detail, file=sys.stderr, flush=True)
        return rc
    print("docich: ai 完了")
    return 0
