"""Native comment orchestration (#829 PR-3e).

One batch end to end:

    intake DTO → classification → contexts → prompt → LLM dispatch (+retry) →
    output guard → translation (C-S4) → delivery contract → ack

Nothing here imports soviet_now. The LLM stage is pluggable so fixtures and
unit tests can complete without provider credentials; production callers
pass the native ``docich.llm`` dispatcher through ``dispatch``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import contexts, guard, prompt
from . import intake
from docich.comment_classifier import heuristic
from .contracts import (
    CommentDiagnosis,
    CommentIntake,
    CommentPipelineResult,
    FileDeliverySink,
)
from .translation import translate_reply

GenerateFn = Callable[[str], tuple[str, object]]

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

    if getattr(args, "output_dir", None):
        os.environ["DOCICH_COMMENT_OUTPUT_DIR"] = args.output_dir

    result = run_pipeline(intake)
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
    comments_block = "\n".join(item.line for item in intake.comments)
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
    comments_block = "\n".join(item.line for item in intake.comments)
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


def run_pipeline(
    intake: CommentIntake,
    *,
    game_context=None,
    env=None,
    generator: GenerateFn | None = None,
    delivery_sink=None,
    retries: int = 1,
    translator=None,
    gacha_list_file=None,
) -> CommentPipelineResult:
    env = os.environ if env is None else env
    rows = classify_intake(intake)
    prompt_text, category = build_prompt_for(intake, rows, game_context=game_context, env=env,
                                            gacha_list_file=gacha_list_file)

    if generator is None:
        # Offline by default: never accidentally invoke a real model. Callers
        # that want real dispatch inject a generator wrapping docich.ai_generate.
        generator = stub_generator()

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
        delivery_sink = FileDeliverySink(root=Path(env.get("DOCICH_COMMENT_OUTPUT_DIR", ".")).resolve() / "comment_delivery")
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
