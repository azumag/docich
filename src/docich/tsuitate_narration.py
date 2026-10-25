"""Bounded 衝立将棋 narration: the corner introduction and the 感想戦.

The corner manager owns *when* this runs; this module only formats the fixed
introduction and the already-verified review facts, and hands them to the
reviewed shared sinks:

- overlay: ``overlay_queue.append_event`` with the existing ``prediction``
  category.  The introduction and the review are two events with distinct
  ``source_id`` suffixes (``tsuitate:intro:<request>`` / ``tsuitate:review:<request>``)
  because the shared queue keeps only the newest event per ``source_id``.
- speech: ``soren_output.enqueue_audio_text`` (opt-in via the catalog's
  ``audio_enabled``).

The 感想戦 text is generated from the *verified* public facts of the match that
just ended (see ``tsuitate_review``): result, end reason, move count, both sides'
thinking times, fouls, rating change and the opponent's revealed name.  When a
non-empty ``[tsuitate_corner] review_agents`` chain is configured, the shared LLM
dispatch turns those facts into natural prose; any failure, timeout or unsafe
output falls back to the fixed text below.  The model is never given the raw
board and is instructed not to invent pieces, captures or hidden information.
The per-stretch show uses deterministic board analysis instead of free prose.
Its model call may only select a supplied focal point; even provider outages
retain the explanation and comparison (see ``tsuitate_analysis``).
The shared dispatch label starts with ``RADIO`` like every other corner closing,
so a busy improvement lane can make the shared gate wait; the wait is bounded by
this module's overall budget and then falls back to the fixed text.

Both sinks are best-effort: a failure is reported as a fixed count and never
fails the corner or the game switch.
"""
from __future__ import annotations

import json
import os
import re
import time
import tomllib
from pathlib import Path

from .comment_queue import comment_queue_dir, is_quiet
from .overlay_queue import OVERLAY_BODY_LIMIT, OVERLAY_TITLE_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text, resolve_soren_root
from .tsuitate_prose import dedupe_text

TITLE = "AI衝立将棋"
INTRO_TITLE = "AI衝立将棋 コーナー紹介"
REVIEW_TITLE = "AI衝立将棋 感想戦"
INTRO_CONTEXT = "tsuitate:intro"
REVIEW_CONTEXT = "tsuitate:review"
MAX_SPEECH = 300
MIN_REVIEW_CHARS = 40
GENERATE_TIMEOUT_S = 20
REVIEW_LABEL = "RADIO:tsuitate-review"
SITE_NAME = "王様のかくれんぼ"

#: How long the 感想戦 waits for the shared audio queue to reach a boundary.
#: The corner keeps the result card on screen while it waits, so the wait is
#: bounded and the review is still spoken after the bound (fail-open): a busy
#: queue must never hold the shared program slot or lose the 感想戦.
REVIEW_BOUNDARY_WAIT_S = 180.0
REVIEW_BOUNDARY_POLL_S = 1.0
#: Fixed outcome vocabulary recorded in the corner state and diagnostics.
BOUNDARY_OUTCOMES = ("quiet", "timeout", "unavailable", "skipped")

#: Spoken once when the corner takes the display, before the match is found.
INTRO_TEXT = (
    "ここからはAI衝立将棋のコーナーです。衝立将棋は、相手の駒が見えない状態で指す将棋です。"
    "見えない駒の合法性を判定できるのはサーバーだけなので、合法だと思って指した手が反則になるのも"
    "正常な流れで、反則しても手番は変わりません。反則が10回に達すると反則負けです。"
    "このコーナーでは、docichのAIが"
    f"{SITE_NAME}で1局だけ対局します。持ち時間は300秒で、1手ごとに3秒が加算されます。"
    "対局のあとには、公開された棋譜をもとに感想戦をお届けします。しばらくお付き合いください。"
)

INTRO_OVERLAY_BODY = (
    "相手の駒が見えない状態で指す「衝立将棋」のコーナーです。\n"
    f"AIが{SITE_NAME}（beta.tsuitate.info）で1局対局し、終局後に公開棋譜から感想戦をお届けします。"
    "持ち時間300秒＋1手3秒、反則10回で反則負けです。"
)

#: End reasons the site publishes, in spoken Japanese.  An unknown reason is
#: reported as-is from the fixed identifier instead of being guessed.
REASON_LABELS = {
    "foul_limit": "反則負け",
    "checkmate": "詰み",
    "resign": "投了",
    "timeout": "時間切れ",
    "disconnect": "切断",
    "stalemate": "ステイルメイト",
    "repetition": "千日手",
    "draw": "引き分け",
    "aborted": "中止",
    "unknown": "理由未確定",
}
OUTCOME_LABELS = {"win": "AIの勝ち", "loss": "AIの負け", "draw": "引き分け", "unknown": "結果未確定"}

_UNSAFE = re.compile(r"(?i)thinking|reasoning|as an ai|assistant|<[^>]+>|```|https?://|\$\{|\bprompt\b")
_KANA_KANJI = re.compile(r"[぀-ヿ㐀-鿿]")
_SENTENCE = re.compile(r"[。！？!?]")


def review_agents(g) -> str:
    """The configured 感想戦 agent chain; empty means deterministic text only."""
    try:
        raw = tomllib.loads(Path(g.config_path).read_text(encoding="utf-8")).get("tsuitate_corner", {})
        value = raw.get("review_agents", "")
    except (OSError, ValueError, AttributeError, TypeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


def _seconds(ms) -> int:
    return max(0, int(round(ms / 1000))) if isinstance(ms, (int, float)) else 0


def _reason_label(reason) -> str:
    return REASON_LABELS.get(reason, reason if isinstance(reason, str) and reason else "理由未確定")


def facts_text(summary, terminal) -> str:
    """The reviewed facts of one match, as a bounded list for the prompt.

    Only values that were already verified against the settled result appear
    here; the raw move list and the board never do.
    """
    lines = [f"対局結果: {OUTCOME_LABELS.get(terminal.get('outcome'), '結果未確定')}",
             f"終局理由: {_reason_label(terminal.get('reason'))}",
             f"総手数: {terminal.get('moveNumber')}手"]
    if not isinstance(summary, dict):
        return "\n".join(lines)
    lines.append(f"AIの手番: {'先手' if summary.get('color') == 'sente' else '後手'}")
    lines.append(f"AIの着手数: {summary.get('ownMoves')}手 / 相手: {summary.get('opponentMoves')}手")
    lines.append(f"AIの反則: {summary.get('fouls')}回 / 相手の反則: {summary.get('opponentFouls')}回")
    own, opponent = summary.get("own") or {}, summary.get("opponent") or {}
    lines.append(
        f"AIの考慮時間: 合計{_seconds(own.get('totalMs'))}秒・最長{_seconds(own.get('maxMs'))}秒"
        f" / 相手: 合計{_seconds(opponent.get('totalMs'))}秒・最長{_seconds(opponent.get('maxMs'))}秒"
    )
    if isinstance(summary.get("ratingBefore"), int) and isinstance(summary.get("ratingAfter"), int):
        lines.append(f"AIのレーティング: {summary['ratingBefore']}から{summary['ratingAfter']}へ")
    if isinstance(summary.get("opponentName"), str):
        lines.append(f"対戦相手: {summary['opponentName']}")
    lines.append(f"成りの回数: {summary.get('promotions')}回 / 持ち駒を打った回数: {summary.get('drops')}回")
    return "\n".join(lines)


_RULES = (
    "条件: 与えられた事実だけを使う / 駒の種類・捕獲・盤面・相手の隠し駒を推測して書かない / "
    "相手の心中や隠れた情報に触れない / 絵文字・記号・箇条書き・括弧書き・URLを使わない / "
    "同じ趣旨の文を繰り返さず、対局の流れが追える自然な一連の文章にする / "
    "話し言葉の日本語で2〜3文、200字以内 / 最後は視聴者へのひとことで締める。\n"
    "出力は読み上げる本文だけにしてください。前置きや説明は書かないでください。"
)


def build_prompt(summary, terminal) -> str:
    return (
        "あなたは将棋配信の司会です。いま、AIが「衝立将棋」（相手の駒が見えない状態で指す将棋）の"
        "1局を終えました。公開された対局結果は次のとおりです。\n"
        + facts_text(summary, terminal)
        + "\nこの対局を振り返る感想戦を、視聴者へ話しかけるように書いてください。\n"
        + _RULES
    )


def sanitize(text) -> str | None:
    """One spoken paragraph, or ``None`` when the model output is unusable.

    Repeated sentences are dropped before the bounds are checked (#2737): a
    model that restates the same idea twice is kept only in its non-repeating
    form, and output that is *only* repetition falls back to the fixed text.
    """
    if not isinstance(text, str):
        return None
    value = " ".join(text.replace("　", " ").split()).strip("「」『』\"' ")
    value = dedupe_text(value)
    if not MIN_REVIEW_CHARS <= len(value) <= MAX_SPEECH or _UNSAFE.search(value):
        return None
    if len(_KANA_KANJI.findall(value)) < len(value) * 0.5:
        return None
    if not _SENTENCE.search(value):
        return None
    return value


def fallback_text(summary, terminal) -> str:
    """The deterministic 感想戦, used when no model output is available."""
    outcome = OUTCOME_LABELS.get(terminal.get("outcome"), "結果未確定")
    moves = terminal.get("moveNumber")
    head = f"感想戦です。今回の対局は{moves}手、{outcome}、終局理由は{_reason_label(terminal.get('reason'))}でした。"
    if not isinstance(summary, dict):
        return (head + "公開棋譜を確認できなかったため、結果だけをお伝えします。"
                "次の対局もぜひご覧ください。")[:MAX_SPEECH]
    side = "先手" if summary.get("color") == "sente" else "後手"
    parts = [
        head,
        f"AIは{side}で、自分の着手は{summary.get('ownMoves')}手、反則は{summary.get('fouls')}回でした。",
        f"考慮時間は合計{_seconds((summary.get('own') or {}).get('totalMs'))}秒、"
        f"相手は合計{_seconds((summary.get('opponent') or {}).get('totalMs'))}秒で、"
        f"持ち駒を打った手が{summary.get('drops')}回、成りが{summary.get('promotions')}回ありました。",
    ]
    if isinstance(summary.get("ratingBefore"), int) and isinstance(summary.get("ratingAfter"), int):
        parts.append(f"レーティングは{summary['ratingBefore']}から{summary['ratingAfter']}へ動きました。")
    if isinstance(summary.get("opponentName"), str):
        parts.append(f"対戦相手は{summary['opponentName']}でした。")
    parts.append("見えない駒と戦う難しさが出た一局でした。次もお付き合いください。")
    return "".join(parts)[:MAX_SPEECH]


def status_facts(summary) -> dict | None:
    """The verified public facts the 感想戦画面 shows as its status strip.

    The live view carries a status panel while the match runs; the kifu show
    replaced it with prose alone.  This projection keeps the same kind of
    at-a-glance status on screen during the review (#2737), built only from
    the already-published values :func:`facts_text` uses.  ``None`` means the
    replay summary is unavailable and the header's result line stands alone.
    """
    if not isinstance(summary, dict):
        return None
    own = summary.get("own") if isinstance(summary.get("own"), dict) else {}
    opponent = summary.get("opponent") if isinstance(summary.get("opponent"), dict) else {}
    facts: dict[str, str] = {}
    if (type(summary.get("ratingBefore")) is int
            and type(summary.get("ratingAfter")) is int):
        facts["rating"] = f"{summary['ratingBefore']}→{summary['ratingAfter']}"
    facts["clock"] = (f"AI計{_seconds(own.get('totalMs'))}秒"
                      f" / 相手計{_seconds(opponent.get('totalMs'))}秒")
    if type(summary.get("fouls")) is int or type(summary.get("opponentFouls")) is int:
        fouls = summary.get("fouls") if type(summary.get("fouls")) is int else 0
        opponent_fouls = (summary.get("opponentFouls")
                          if type(summary.get("opponentFouls")) is int else 0)
        facts["fouls"] = f"AI{fouls}回 / 相手{opponent_fouls}回"
    if type(summary.get("promotions")) is int or type(summary.get("drops")) is int:
        promotions = summary.get("promotions") if type(summary.get("promotions")) is int else 0
        drops = summary.get("drops") if type(summary.get("drops")) is int else 0
        facts["shapes"] = f"成り{promotions}回 / 打ち{drops}回"
    return facts or None


#: Fixed vocabulary for why the model prose was not used.  Only the shared
#: dispatcher's own failure kinds are mapped; free text never reaches the state.
MODEL_FAILURES = ("none", "unsafe", "gate", "timeout", "rate_limit", "provider", "exception")


#: The model chooses a focal point, never supplies unchecked board claims.
CHUNK_LABEL = REVIEW_LABEL
CHUNK_RULES = (
    "候補は公開棋譜と駒の動きから検証した感想戦です。狙い、利点と代償、比較候補の説明が"
    "視聴者に最も役立つ焦点を1つ選んでください。指し手の列挙はしません。"
    "公開後の盤面と対局中に見えた情報は区別します。候補は最善手や形勢評価ではありません。"
    '出力は候補のfocusを選んだJSONだけです。例: {"focus": 3}。本文や追加情報は書かないでください。'
)


def build_chunk_prompt(facts: str) -> str:
    return (
        "あなたは衝立将棋の感想戦の編集者です。次の区間の転機を選んでください。\n"
        + facts + "\n" + CHUNK_RULES
    )


def select_chunk(text, cards) -> str | None:
    """Only a supplied card can go on air, never the model's arbitrary prose."""
    if not isinstance(text, str) or len(text) > 100:
        return None
    try:
        selection = json.loads(text)
    except ValueError:
        return None
    if (not isinstance(selection, dict) or set(selection) != {"focus"}
            or type(selection["focus"]) is not int):
        return None
    return next((card["text"] for card in cards if card["focus"] == selection["focus"]), None)


def chunk_commentary(g, agents: str, moves, start: int, end: int, *, generate_fn=None) -> tuple[str, str]:
    """Select and render grounded analysis; outages retain the full explanation."""
    from .tsuitate_analysis import UNAVAILABLE, analysis_cards
    from .tsuitate_show import chunk_facts

    cards = analysis_cards(moves, start, end)
    if not cards:
        return UNAVAILABLE, "fallback"
    fallback = cards[0]["text"]
    if not agents:
        return fallback, "fallback"
    facts = chunk_facts(moves, start, end, cards=cards)
    try:
        if generate_fn is not None:
            text = select_chunk(generate_fn(g, agents, facts), cards)
        else:
            from .ai_generate import run_prompt

            env = {**os.environ, "DOCICH_ALLOW_REAL_AI": "1"}
            result = run_prompt(
                g, label=CHUNK_LABEL, agents=agents, prompt_text=build_chunk_prompt(facts),
                timeout=GENERATE_TIMEOUT_S, timeout_sec=float(GENERATE_TIMEOUT_S + 10), env=env,
            )
            text = select_chunk(result.output, cards) if result.returncode == 0 else None
    except Exception:
        text = None
    return (text, "ai") if text else (fallback, "fallback")


def _model_failure(failure_kind) -> str:
    kind = failure_kind if isinstance(failure_kind, str) else ""
    return {"gate_giveup": "gate", "timeout": "timeout",
            "rate_limit": "rate_limit"}.get(kind, "provider")


def generate(g, agents: str, summary, terminal, *, generate_fn=None) -> tuple[str | None, str]:
    """One bounded LLM call for the 感想戦; ``(None, reason)`` on any failure.

    A non-empty ``review_agents`` chain is the billed-run consent, exactly like
    the other corner closings: this function sets ``DOCICH_ALLOW_REAL_AI=1`` for
    the dispatch it makes.
    """
    if generate_fn is not None:
        text = sanitize(generate_fn(g, agents, summary, terminal))
        return (text, "none") if text else (None, "unsafe")
    from .ai_generate import run_prompt

    env = {**os.environ, "DOCICH_ALLOW_REAL_AI": "1"}
    result = run_prompt(
        g, label=REVIEW_LABEL, agents=agents, prompt_text=build_prompt(summary, terminal),
        timeout=GENERATE_TIMEOUT_S, timeout_sec=float(GENERATE_TIMEOUT_S + 10), env=env,
    )
    if result.returncode != 0:
        return None, _model_failure(getattr(result, "failure_kind", None))
    text = sanitize(result.output)
    return (text, "none") if text else (None, "unsafe")


def review_text(g, summary, terminal, *, agents="",
                generate_fn=None) -> tuple[str, str, str]:
    """Return ``(text, kind, model_failure)``.

    ``kind`` is ``"ai"`` or ``"fallback"``; ``model_failure`` is one of
    :data:`MODEL_FAILURES` and says why the model prose was not used.
    """
    if agents:
        try:
            text, failure = generate(g, agents, summary, terminal, generate_fn=generate_fn)
        except Exception:
            text, failure = None, "exception"
        if text:
            return text, "ai", "none"
        return fallback_text(summary, terminal), "fallback", failure
    return fallback_text(summary, terminal), "fallback", "none"


def _event(kind, title, body, request_id, now):
    event = {
        "ts": int(now()),
        "category": "prediction",
        "level": "info",
        "title": title[:OVERLAY_TITLE_LIMIT],
        "body": body,
    }
    if isinstance(request_id, str) and request_id:
        event["source_id"] = f"tsuitate:{kind}:{request_id}"
    return event


def _counts(total, *, skipped=0):
    return {"sent": 0, "failed": 0, "skipped": min(total, max(0, skipped))}


#: Spoken context per overlay kind; the audio worker groups playback by it.
CONTEXTS = {"intro": INTRO_CONTEXT, "review": REVIEW_CONTEXT}


def _publish(g, items, *, request_id, audio_enabled, overlay, speech, now, speaker):
    """Publish ``(kind, title, body, text)`` items to both shared sinks."""
    result = {"overlay": _counts(len(items)), "speech": _counts(
        len(items), skipped=0 if audio_enabled else len(items)),
        "overlay_events": len(items), "speech_items": len(items)}
    for kind, title, body, text in items:
        try:
            overlay(resolve_soren_root(g), _event(kind, title, body, request_id, now))
            result["overlay"]["sent"] += 1
        except Exception:
            # Never surface sink errors: they can contain private queue payloads.
            result["overlay"]["failed"] += 1
        if not audio_enabled:
            continue
        try:
            speech(g, text, context=CONTEXTS.get(kind, REVIEW_CONTEXT), speaker=speaker)
            result["speech"]["sent"] += 1
        except Exception:
            result["speech"]["failed"] += 1
    return result


def announce_intro(g, *, request_id, audio_enabled=False, overlay=None,
                   speech=None, now=time.time, speaker="") -> dict:
    """Publish the fixed corner introduction. Never raises."""
    return _publish(
        g, [("intro", INTRO_TITLE, INTRO_OVERLAY_BODY, INTRO_TEXT)],
        request_id=request_id, audio_enabled=audio_enabled,
        overlay=append_event if overlay is None else overlay,
        speech=enqueue_audio_text if speech is None else speech,
        now=now, speaker=speaker,
    )


def wait_for_audio_boundary(g, *, timeout_s=REVIEW_BOUNDARY_WAIT_S, sleep=time.sleep,
                            monotonic=time.monotonic, poll_s=REVIEW_BOUNDARY_POLL_S,
                            queue_dir=None) -> str:
    """Wait (bounded) until the shared audio queue reaches a boundary.

    The 感想戦 is spoken after whatever the audio worker is already reading, so
    it does not cut into a comment or a corner announcement that is playing.
    The corner holds the result card while it waits, so the wait is bounded by
    ``timeout_s`` and **fails open**: after the bound the review is still spoken.

    Returns a fixed outcome: ``quiet`` (boundary reached), ``timeout`` (bound
    expired), ``unavailable`` (the queue could not be resolved).  A missing or
    unreadable queue counts as quiet, exactly like the corner-entry boundary.
    """
    try:
        directory = Path(queue_dir) if queue_dir is not None else comment_queue_dir(
            Path(resolve_soren_root(g)))
    except Exception:
        return "unavailable"
    try:
        if is_quiet(directory):
            return "quiet"
        deadline = monotonic() + max(0.0, float(timeout_s))
        while monotonic() < deadline:
            sleep(min(poll_s, max(deadline - monotonic(), 0.01)))
            if is_quiet(directory):
                return "quiet"
    except Exception:
        return "unavailable"
    return "timeout"


def overlay_body(text, terminal) -> str:
    """A compact result line above the spoken 感想戦 for the shared overlay."""
    head = (f"{OUTCOME_LABELS.get(terminal.get('outcome'), '結果未確定')} / "
            f"{_reason_label(terminal.get('reason'))} / {terminal.get('moveNumber')}手")
    return f"{head}\n{text}"[:OVERLAY_BODY_LIMIT]


def announce_review(g, text, *, request_id, body=None, audio_enabled=False, overlay=None,
                    speech=None, now=time.time, speaker="") -> dict:
    """Publish the 感想戦. Never raises."""
    overlay_body_text = body if isinstance(body, str) and body else text
    return _publish(
        g, [("review", REVIEW_TITLE, overlay_body_text, text)],
        request_id=request_id, audio_enabled=audio_enabled,
        overlay=append_event if overlay is None else overlay,
        speech=enqueue_audio_text if speech is None else speech,
        now=now, speaker=speaker,
    )
