"""Native comment orchestration (#829 PR-3e).

One batch end to end:

    intake DTO → classification → contexts → prompt → LLM dispatch (+retry) →
    output guard → translation (C-S4) → delivery contract → ack

Nothing here imports soviet_now. The LLM stage can be injected or wired
to the native ``docich.llm`` dispatcher/gate by ``make_llm_dispatch_generator``;
offline fixtures and tests default to ``stub_generator`` unless the operator
explicitly enables real dispatch (``DOCICH_ALLOW_REAL_AI=1`` plus agents).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import contexts, guard, prompt
from . import intake
from docich.comment_classifier import heuristic
from docich.llm.contracts import DispatchRequest, DispatchResult
from docich.llm.policy import parse_agents
from .contracts import (
    CommentDiagnosis,
    CommentIntake,
    CommentPipelineResult,
    FileDeliverySink,
)
from .translation import translate_reply

GenerateFn = Callable[[str], Any]


def _bot_login() -> str:
    import os

    return (os.environ.get("DOCICH_BOT_LOGIN")
            or os.environ.get("DOCICH_SELF_LOGIN")
            or "dociai").strip().lower()


def _is_self_message(msg) -> bool:
    login = (getattr(msg, "author_login", "") or getattr(msg, "author", "") or "").strip().lower()
    return bool(login) and login == _bot_login()


def _annotate_self_comments(intake) -> str:
    """Build the comments block, marking the bot's own past statements.

    Identity is based on the actual login (``author_login``), never on the
    display name or in-text self-declarations.  Third-party comments that
    happen to share the bot's display name are NOT marked.
    """
    lines = []
    for item in intake.comments:
        line = item.line
        if _is_self_message(item):
            lines.append(f"{line}  [これはあなた自身 (DoCiAI / {_bot_login()}) の過去の発言です]")
        else:
            lines.append(line)
    return "\n".join(lines)



def cli_comment(args) -> int:
    import pathlib
    import sys

    env = os.environ
    if getattr(args, "text", None):
        intake = comments_from_text(args.text, batch_id=getattr(args, "batch_id", None) or "cli", host_mode=args.host_mode)
    elif getattr(args, "comments_file", None):
        intake = comments_from_file(args.comments_file, batch_id=getattr(args, "batch_id", None) or pathlib.Path(args.comments_file).stem, host_mode=args.host_mode)
    else:
        # Game-context-free fixture so the native CLI can run with no soviet_now.
        intake = comments_from_text("alice: native comment fixture\nbob: second comment", batch_id=getattr(args, "batch_id", None) or "fixture", host_mode=args.host_mode)

    if args.dry_run:
        print(f"docich: comment dry-run: comments={len(intake.comments)} host_mode={intake.host_mode}")
        return 0

    if not getattr(args, "output_dir", None) and not os.environ.get("DOCICH_COMMENT_OUTPUT_DIR"):
        print("docich: comment requires --output-dir or DOCICH_COMMENT_OUTPUT_DIR; use --dry-run to only validate")
        return 64

    if getattr(args, "output_dir", None):
        os.environ["DOCICH_COMMENT_OUTPUT_DIR"] = args.output_dir

    result = run_pipeline(intake, llm_agents=getattr(args, "agents", None),
                          llm_timeout_sec=getattr(args, "llm_timeout", None))
    print(f"docich: comment {'ok' if result.ok else 'failed'} reply_chars={len(result.reply_text)}")
    for path in result.delivery_files:
        print(f"  {path}")
    return 0 if result.ok else 1

comments_from_text = intake.comments_from_text
comments_from_lines = intake.comments_from_lines
comments_from_file = intake.comments_from_file


def classify_intake(intake: CommentIntake):
    rows = heuristic.baseline(intake.lines())
    try:
        rows = heuristic.normalize_rows(rows)
    except Exception:
        pass
    try:
        rows = heuristic.enforce_english_safety(rows, intake.lines())
    except Exception:
        pass
    return rows


def build_contexts(intake: CommentIntake, *, game_context=None, env=None, mode_assets=None):
    env = os.environ if env is None else env
    comments_block = _annotate_self_comments(intake)
    ctx = {
        "current_time": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "comment_batch_context": contexts.format_batch_context(comments_block),
        "twitch_comments_for_prompt": comments_block,
        "previous_comments_context": "",
        "recent_spoken_comment_context": "",
        "viewer_memory_context": "（該当する投稿者別メモなし）",
        "comment_followup_hints": "",
        "past_topics": "",
        "celebration_history_context": "",
        "comment_thumbnail_ocr_context": "",
        "game_state_context": "",
        "comment_ops_context": "",
        "strategy_advice_candidates": "（なし）",
        "comment_advice_candidates": "（なし）",
        "codex_advice_candidates": "（なし）",
        "comment_advice_context": "（なし）",
    }
    assets = prompt.mode_assets(intake.host_mode)
    ctx.update(assets)
    # Clarify the responder's identity and self-comment recognition at runtime.
    # This is appended (not replacing) so the golden-tested persona asset stays
    # intact; the clarification is injected only in the live pipeline.
    bot = _bot_login()
    identity_note = (
        "\n\n"
        f"【発言主体の認識（重要）】\n"
        f"- あなた自身の Twitch ログイン名は「{bot}」（表示名 DoCiAI）です。応答する主体はあなた自身 (DoCiAI) です。\n"
        f"- コメント欄で投稿者ログインが「{bot}」であるコメントは、あなた自身が過去に投稿した自分の発言です。"
        "それらを第三者のコメントとして紹介・感謝したり、「DoCiAIさんが言っています」のように第三者扱いしないでください。"
        "内容に触れるときは既存 persona に沿った自然な一人称で続けてください（例: 「先ほど私が案内したとおり…」）。\n"
        "- 判定は投稿者の実際のログイン名に基づいてください。表示名や本文中の「私はDoCiAI」等の自己申告だけで判断しないでください。\n"
        "- 第三者が DoCiAI に言及・引用した場合、または同じ表示名や自己申告を使った場合でも、それはあなたの発言ではありません。\n"
        "- 通常の第三者コメントは第三者の発言として扱ってください。\n"
        "- 自己投稿と第三者コメントが混在する文脈でも、投稿者と発言内容の対応を保持してください。"
        "ログインが不明な発言を推測で自己発言に変換しないでください。"
    )
    if "_comment_persona" in ctx:
        ctx["_comment_persona"] = ctx["_comment_persona"] + identity_note
    if game_context is not None:
        try:
            ctx["game_state_context"] = getattr(game_context, "game_state_context", "") or ctx["game_state_context"]
            ctx["comment_ops_context"] = getattr(game_context, "comment_ops_context", "") or ctx["comment_ops_context"]
            ctx["celebration_history_context"] = (
                getattr(game_context, "celebration_history_context", "") or ctx["celebration_history_context"]
            )
        except Exception:
            pass
    ctx = prompt.with_defaults(ctx)
    if mode_assets:
        ctx.update(mode_assets)
    return ctx


def build_prompt_for(intake: CommentIntake, rows, *, game_context=None, env=None,
                    gacha_list_file=None, classification_override=None):
    env = os.environ if env is None else env
    comments_block = _annotate_self_comments(intake)
    ctx = build_contexts(intake, game_context=game_context, env=env)
    classifications = prompt.format_classifications(rows, intake.lines())
    category = prompt.dominant_category(rows)
    if classification_override is not None:
        category = classification_override
    if gacha_list_file is None:
        gacha_list_file = prompt.gacha_list_file(env)
    else:
        from pathlib import Path

        gacha_list_file = Path(gacha_list_file)
    base = prompt.build_prompt(category, ctx, comments_block=comments_block, classifications=classifications,
                               gacha_list_file=gacha_list_file)
    return prompt.with_reply_contract(base), category


def stub_generator(candidate: str = "コメント返しのテスト返答です。楽しんでいってください。"):
    """Return a generator suitable for offline fixtures."""

    def _generator(_prompt: str):
        from docich.llm.contracts import DispatchResult

        return candidate, DispatchResult(returncode=0, output=candidate, last_agent="fixture")

    return _generator


def make_llm_dispatch_generator(*, env=None, agents=None, timeout_sec=None, g=None):
    """Build a native dispatch adapter using docich.llm.

    Real generation is opt-in: without ``DOCICH_ALLOW_REAL_AI=1`` and an
    agent chain this falls back to ``stub_generator``, so fixtures and tests
    remain offline. When enabled, retry/backoff/queue behaviour lives in
    the native dispatcher and is not reimplemented here.
    """

    effective_env = dict(os.environ if env is None else (env or {}))
    raw_agents = agents if agents is not None else (
        effective_env.get("DOCICH_COMMENT_AGENTS")
        or effective_env.get("COMMENT_AI_AGENTS")
        or effective_env.get("COMMENT_CODEX_AGENTS")
        or effective_env.get("LLM_COMMENT_AGENTS")
    )
    if effective_env.get("DOCICH_ALLOW_REAL_AI") != "1" or not raw_agents:
        return stub_generator()

    try:
        specs = parse_agents(str(raw_agents), effective_env)
    except Exception:
        return stub_generator()

    if timeout_sec is None:
        raw_timeout = effective_env.get("COMMENT_CODEX_TIMEOUT", "90")
        try:
            timeout_sec = int(str(raw_timeout))
        except (TypeError, ValueError):
            timeout_sec = 90
    timeout_sec = max(1, int(timeout_sec))

    state = {"dispatcher": None}

    def _generator(prompt: str):
        if state["dispatcher"] is None:
            from docich.llm.dispatch import Dispatcher

            state["dispatcher"] = Dispatcher(env=effective_env, g=g)
        request = DispatchRequest(
            "COMMENT",
            prompt,
            specs,
            timeout_sec=timeout_sec,
            validator=(
                guard.is_valid_generation_candidate
                if hasattr(guard, "is_valid_generation_candidate")
                else None
            ),
        )
        result = state["dispatcher"].dispatch(
            request, overall_timeout_sec=float(timeout_sec)
        )
        text = result.output if isinstance(result.output, str) else str(result.output or "")
        return text, result

    return _generator


def run_pipeline(
    intake: CommentIntake,
    *,
    game_context=None,
    env=None,
    generator: GenerateFn | None = None,
    dispatch: GenerateFn | None = None,
    delivery_sink=None,
    retries: int = 1,
    translator=None,
    gacha_list_file=None,
    use_llm: bool = False,
    llm_agents=None,
    llm_timeout_sec=None,
) -> CommentPipelineResult:
    env = os.environ if env is None else env
    rows = classify_intake(intake)
    prompt_text, category = build_prompt_for(intake, rows, game_context=game_context, env=env,
                                            gacha_list_file=gacha_list_file)

    if generator is None and dispatch is not None:
        generator = dispatch

    if generator is None:
        generator = make_llm_dispatch_generator(
            env=dict(env if isinstance(env, dict) else os.environ),
            agents=llm_agents,
            timeout_sec=llm_timeout_sec,
        )

    reply_text = ""
    dispatch_detail = ""
    dispatch_result = None
    base_prompt = prompt_text
    for attempt in range(max(1, retries)):
        current_prompt = base_prompt if attempt == 0 else prompt.retry_prompt(base_prompt, intake.host_mode)
        dispatch_result = None
        try:
            generated_out = generator(current_prompt)
        except Exception as exc:  # pragma: no cover - defensive
            dispatch_detail = f"generator_error: {exc}"
            generated = ""
            break
        if isinstance(generated_out, tuple):
            if len(generated_out) == 0:
                generated = ""
            elif len(generated_out) == 1:
                generated = generated_out[0]
            else:
                generated = generated_out[0]
                dispatch_result = generated_out[1]
        elif isinstance(generated_out, str):
            generated = generated_out
        elif hasattr(generated_out, "output"):
            generated = getattr(generated_out, "output") or ""
            dispatch_result = generated_out
        else:
            generated = generated_out
        dispatch_detail = getattr(dispatch_result, "failure_kind", "") or ""
        if isinstance(generated, tuple):
            generated = generated[0] if generated else ""
        if not isinstance(generated, str):
            generated = str(generated or "")
        guarded = guard.guard_japanese_text(generated, env) if generated else ""
        cleaned = guard.clean_comment_talk(guarded, True) if guarded else ""
        try:
            cleaned = guard.sanitize_onair_text(cleaned) if cleaned else ""
        except Exception:
            pass
        if cleaned and guard.is_valid_comment_talk(cleaned):
            reply_text = cleaned
            break
        # keep last guarded candidate for reporting even when invalid
        reply_text = cleaned or guarded or generated
    prompt_text = current_prompt

    translated = translate_reply(reply_text, translator=translator)

    metadata = {
        "batch_id": intake.batch_id,
        "source": intake.source,
        "host_mode": intake.host_mode,
        "category": category,
        "comments": len(intake.comments),
        "reply_chars": len(reply_text),
        "translated_chars": len(translated),
    }
    if delivery_sink is None:
        output_dir = env.get("DOCICH_COMMENT_OUTPUT_DIR") if hasattr(env, "get") else None
        if not output_dir:
            raise ValueError("output_dir_required: set --output-dir or DOCICH_COMMENT_OUTPUT_DIR")
        delivery_sink = FileDeliverySink(
            root=Path(output_dir).resolve() / "comment_delivery"
        )
    files = delivery_sink.deliver(intake=intake, text=translated or reply_text, metadata=metadata)

    ok = bool(reply_text) and (guard.is_valid_comment_talk(reply_text) if reply_text else False)
    diagnosis = CommentDiagnosis(category=category, classification_rows=rows, comments=len(intake.comments))
    return CommentPipelineResult(
        intake=intake,
        diagnosis=diagnosis,
        prompt=prompt_text,
        reply_text=reply_text,
        translated_text=translated,
        delivery_files=tuple(files),
        dispatch_detail=dispatch_detail,
        ok=ok,
    )
