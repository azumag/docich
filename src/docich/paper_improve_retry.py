"""Bounded retry entrypoint for end-of-corner PAPER strategy improvement."""
from __future__ import annotations

import json
import sys
from typing import Sequence


def is_paper_improve_invocation(argv: Sequence[str]) -> bool:
    """Return true for the existing ``docich ... trading ... paper-improve`` route."""
    args = list(argv)
    try:
        trading_index = args.index("trading")
    except ValueError:
        return False
    tail = args[trading_index + 1 :]
    if not tail:
        return False
    if tail[0] == "paper-improve":
        return True
    if tail[0] == "--state-dir":
        return len(tail) >= 3 and tail[2] == "paper-improve"
    if tail[0].startswith("--state-dir="):
        return len(tail) >= 2 and tail[1] == "paper-improve"
    return False


def build_validation_repair_prompt(prompt: str, reason: str) -> str:
    safe_reason = str(reason).replace("\n", " ")[:240]
    return (
        f"{prompt}\n\n"
        "直前の候補はサーバ側の安全なスキーマ検証に失敗しました。"
        "前回の出力そのものは再利用せず、同じfactsから候補を1つだけ作り直してください。\n"
        f"validation_error={json.dumps(safe_reason, ensure_ascii=False)}\n"
        "許可済みfeature・演算子・lookback・件数・max_notional_fraction・相関上限を厳守し、"
        "有効なJSONオブジェクト1つだけを返してください。説明文やMarkdownは不要です。"
    )


def _read_validation_failure(status_path):
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if data.get("status") != "failed" or data.get("phase") != "validate":
        return None
    return str(data.get("detail") or "candidate validation failed")[:240]


def main(argv: list[str] | None = None) -> int:
    from . import cli as docich_cli
    from .trading import cli as trading_cli
    from .trading.ai_text import generate_text
    from .trading.paper_improve import DEFAULT_TIMEOUT, IMPROVE_LABEL, run_paper_improve

    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = docich_cli.build_parser()
    args = parser.parse_args(args_list)
    try:
        if args.command != "trading" or args.trading_command != "paper-improve":
            raise trading_cli.TradingCliError("paper-improve retry entrypoint received another command")
        g = docich_cli._load_global(args)
        state_dir = trading_cli._state_dir(args, docich_cli._repo_root(), g)
        agents = args.agents
        if agents is None:
            agents = trading_cli._paper_corner_improve_agents(g)
        cleaned_agents = (agents or "").strip()

        summary = run_paper_improve(
            g,
            trading_dir=state_dir,
            agents=cleaned_agents,
            dry_run=bool(args.dry_run),
        )
        retry_reason = None if args.dry_run else _read_validation_failure(
            state_dir / "paper_improve_status.json"
        )
        if summary.get("status") == "failed" and retry_reason and cleaned_agents:
            def repair_llm(prompt_text):
                return generate_text(
                    g,
                    label=IMPROVE_LABEL,
                    agents=cleaned_agents,
                    prompt_text=build_validation_repair_prompt(prompt_text, retry_reason),
                    timeout=DEFAULT_TIMEOUT,
                )

            summary = run_paper_improve(
                g,
                trading_dir=state_dir,
                agents=cleaned_agents,
                dry_run=False,
                llm=repair_llm,
            )
            summary = dict(summary)
            summary["validation_retry"] = True

        trading_cli._json_print(summary)
        return 0
    except docich_cli.USER_ERRORS as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
