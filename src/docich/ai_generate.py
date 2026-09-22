"""Native, game-independent AI dispatch (Issue #829 PR-1).

The first migration step owns the provider chain used by COMMENT/RADIO. It
does not source a game checkout, shell library, game ``.env`` or legacy AI
queue. Comment/radio orchestration remains in the legacy compatibility
wrappers until later PRs, but all new docich callers use this module.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from .config import ConfigError, GlobalConfig
from .llm.contracts import DispatchRequest, DispatchResult, LlmError
from .llm.dispatch import Dispatcher
from .llm.policy import MAX_PROMPT_BYTES, parse_agents, validate_label
from .tts import TtsError


class AiError(TtsError):
    """User-facing native dispatch error."""


def _read_prompt(path: Path) -> str:
    prompt = Path(path).resolve()
    if not prompt.is_file():
        raise AiError(f"プロンプトファイルが見つかりません: {prompt}")
    try:
        value = prompt.read_text(encoding="utf-8")
    except OSError as exc:
        raise AiError("プロンプトファイルを読み込めません") from exc
    if not value:
        raise AiError("プロンプトが空です")
    if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise AiError("プロンプトが大きすぎます")
    return value


def _validate_prompt_text(prompt_text: str) -> str:
    if not isinstance(prompt_text, str) or not prompt_text:
        raise AiError("プロンプトが空です")
    try:
        if len(prompt_text.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise AiError("プロンプトが大きすぎます")
    except UnicodeError as exc:
        raise AiError("プロンプトの文字コードが不正です") from exc
    return prompt_text


def _validate_timeout(timeout: int | None) -> None:
    if timeout is not None and (type(timeout) is not int or timeout < 1):
        raise AiError("timeout は1以上の整数である必要があります")


def run_prompt(
    g: GlobalConfig | None,
    *,
    label: str,
    agents: str,
    prompt_text: str,
    timeout: int | None = None,
    timeout_sec: float | None = None,
    last_agent_file: Path | None = None,
    failure_kind_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> DispatchResult:
    """Dispatch one in-memory prompt through the native ordered chain."""

    _validate_prompt_text(prompt_text)
    _validate_timeout(timeout)
    try:
        safe_label = validate_label(label)
        specs = parse_agents(agents, env)
    except LlmError as exc:
        raise AiError(str(exc)) from exc
    request = DispatchRequest(
        label=safe_label,
        prompt=prompt_text,
        agents=specs,
        timeout_sec=timeout,
    )
    return Dispatcher(g=g, env=env, provider_caller=None).dispatch(
        request,
        overall_timeout_sec=timeout_sec,
        last_agent_file=last_agent_file,
        failure_kind_file=failure_kind_file,
    )


def run_ai(
    g: GlobalConfig | None,
    *,
    game_name: str | None = None,
    label: str,
    agents: str,
    prompt_file: Path,
    timeout: int | None = None,
    dry_run: bool = False,
    timeout_sec: float | None = None,
    last_agent_file: Path | None = None,
    failure_kind_file: Path | None = None,
) -> tuple[int, str]:
    """Run the native CLI contract; ``game_name`` is retained for compatibility."""

    del game_name  # The native dispatcher is intentionally game-independent.
    prompt = _read_prompt(Path(prompt_file))
    _validate_timeout(timeout)
    try:
        safe_label = validate_label(label)
        specs = parse_agents(agents)
    except LlmError as exc:
        raise AiError(str(exc)) from exc
    if dry_run:
        models = ",".join(spec.resolved_model for spec in specs)
        return 0, f"label={safe_label} agents={','.join(spec.raw for spec in specs)} models={models} backend=native"
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        raise AiError(
            "実実行は既定で無効です。--dry-run で確認するか、"
            "DOCICH_ALLOW_REAL_AI=1 で明示許可してください"
        )
    result = run_prompt(
        g,
        label=safe_label,
        agents=agents,
        prompt_text=prompt,
        timeout=timeout,
        timeout_sec=timeout_sec,
        last_agent_file=last_agent_file,
        failure_kind_file=failure_kind_file,
    )
    if result.returncode != 0:
        return result.returncode, result.failure_kind or result.detail or "dispatch_failed"
    return 0, f"native dispatch complete (agent={result.last_agent})"


def cli_ai(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    try:
        rc, detail = run_ai(
            g,
            game_name=getattr(args, "game", None),
            label=args.label,
            agents=args.agents,
            prompt_file=Path(args.prompt_file),
            timeout=args.timeout,
            dry_run=args.dry_run,
            timeout_sec=args.run_timeout,
        )
    except (ConfigError, AiError, OSError, LlmError) as exc:
        raise AiError(str(exc)) from exc
    if args.dry_run:
        print(f"docich: ai dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: ai native dispatchがエラー終了しました (rc={rc})", flush=True)
        if detail:
            print(detail, file=sys.stderr, flush=True)
        return rc
    print("docich: ai 完了")
    return 0
