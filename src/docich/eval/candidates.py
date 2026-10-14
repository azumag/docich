"""Offline candidate adapters for the eval base (#1308 PR-2).

A candidate is ``callable(case) -> output dict``. These adapters keep the base
runner usable without any provider: pre-computed outputs from a JSONL file, the
keyless local comment-category heuristic as the classifier baseline, and a
tiny echo used by tests. None of them touch the network or production state.

Output keys understood downstream: ``response``, ``category``, ``screen_need``,
``image_attached``, ``capture_requested``, ``parse_ok``, ``timed_out``,
``side_effects``, ``usage``, ``cost_usd``, ``scores``, ``error``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import corpus
from .contracts import ContractError

# The baseline has no offline screen_need predictor (Jev only), so it abstains
# rather than inventing a capture decision.
SCREEN_NEED_ABSTAIN = "uncertain"


def jsonl_candidate(path) -> Callable:
    """Replay pre-computed outputs keyed by ``case_id`` (no provider call)."""
    rows = corpus.read_jsonl(path)
    by_case = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ContractError("invalid_candidate_output")
        by_case[row["case_id"]] = {key: value for key, value in row.items()
                                   if key != "case_id"}
    def candidate(case):
        return by_case.get(case["case_id"], {"error": "missing_output"})
    candidate.__name__ = "jsonl:" + Path(path).name
    return candidate


def heuristic_candidate() -> Callable:
    """The keyless local category baseline; screen_need abstains as ``uncertain``."""
    from ..comment_classifier import heuristic

    def candidate(case):
        comment = case["input"]["comment"]
        return {
            "response": None,
            "category": heuristic.classify("viewer", comment),
            "screen_need": SCREEN_NEED_ABSTAIN,
            "parse_ok": True,
            "side_effects": 0,
        }
    candidate.__name__ = "heuristic"
    return candidate


def echo_candidate(text: str = "ok") -> Callable:
    def candidate(case):
        return {"response": text, "side_effects": 0, "parse_ok": True}
    candidate.__name__ = "echo"
    return candidate


def capture_outputs(results) -> list:
    """Serialise a run's raw outputs for ``jsonl_candidate`` round-tripping."""
    rows = []
    for result in results:
        row = {"case_id": result.case_id}
        row.update({key: value for key, value in result.output.items()
                    if key != "_hard_fails"})
        rows.append(row)
    return rows
