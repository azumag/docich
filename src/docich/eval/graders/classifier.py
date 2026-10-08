"""Classifier grader: category + screen_need quality for #1233/#1243 (#1308 §4.3).

Reads the gold labels off the cases and the predictions off the runner outputs.
Reports macro F1 and per-label precision/recall, a game-intent recall the mean
would hide, screen_need precision/recall with the critical miss counts, and the
abstain rate. Accuracy alone is never returned as the summary.
"""
from __future__ import annotations

from .. import contracts, metrics

GRADER_VERSION = contracts.GRADER_VERSIONS["classifier"]

# The game-intent recall tracked for #1243 (issue #1308 section 4.3).
GAME_INTENT_LABELS = frozenset({"game_question", "game_status", "strategy_advice"})


def _outputs_by_case(outputs) -> dict:
    if isinstance(outputs, dict):
        return outputs
    return {output["case_id"]: output for output in outputs}


def category_pairs(cases, outputs) -> list:
    by_case = _outputs_by_case(outputs)
    pairs = []
    for case in cases:
        gold = case["expected"]["category"]
        if gold is None:
            continue
        output = by_case.get(case["case_id"]) or {}
        pairs.append((gold, output.get("category")))
    return pairs


def screen_need_pairs(cases, outputs) -> list:
    by_case = _outputs_by_case(outputs)
    pairs = []
    for case in cases:
        gold = case["expected"]["screen_need"]
        if gold is None:
            continue
        output = by_case.get(case["case_id"]) or {}
        pairs.append((gold, output.get("screen_need")))
    return pairs


def game_intent_recall(cases, outputs) -> dict:
    by_case = _outputs_by_case(outputs)
    game_cases = [case for case in cases if case["expected"]["intent_family"] == "game"]
    hits = sum((by_case.get(case["case_id"]) or {}).get("category") in GAME_INTENT_LABELS
               for case in game_cases)
    return {"support": len(game_cases), "hits": hits,
            "recall": metrics.ratio(hits, len(game_cases))}


def evaluate(cases, outputs) -> dict:
    category = category_pairs(cases, outputs)
    screen = screen_need_pairs(cases, outputs)
    return {
        "grader_version": GRADER_VERSION,
        "category": metrics.score_predictions(category),
        "screen_need": metrics.screen_need_metrics(screen),
        "game_intent": game_intent_recall(cases, outputs),
    }
