"""Authored deterministic sessions for the #697 law-simulator corner.

The production corner has no LLM brain yet (that is a separate owner-approved
step: model calls and billing).  Until then the corner rotates through this
fixed, fully-authored bank.  Every entry is a plain session payload that the
engine in :mod:`docich.communist_law_simulator` validates fail-closed, and the
whole bank is regression-tested (``tests/test_lawsim_corner.py``): an entry
that the engine would reject must fail the tests instead of going on air.

All names, laws, metrics and events remain fictional synthetic content; they
never claim to model a real state, economy or history.
"""

from __future__ import annotations

from .communist_law_simulator import CORNER_ID

REPUBLIC = "シンセティカ社会主義共和国（架空）"

#: One full pass of the longest bank entry is
#: ``(1 start + 11 turns + 1 end) * PAGE_SECONDS``; the corner's default
#: duration (``lawsim_corner.DEFAULT_DURATION_MINUTES``) covers it with margin.
SESSION_BANK = (
    {
        "corner": CORNER_ID,
        "session": {
            "title": "第1期最高会議 — 第一次五カ年計画",
            "republic": REPUBLIC,
            "laws": [
                "grain-procurement-tightening",
                "science-technology-plan",
                "five-year-plan-steel",
                "collective-farm-consolidation",
                "foreign-trade-opening",
                "currency-reform-confiscation",
                "anti-corruption-campaign",
                "price-control-reform",
                "consumer-goods-priority",
                "machinery-import-program",
                "rural-electrification",
            ],
        },
    },
    {
        "corner": CORNER_ID,
        "session": {
            "title": "臨時会議 — 調達強化路線",
            "republic": REPUBLIC,
            "laws": [
                "grain-procurement-tightening",
                "currency-reform-confiscation",
                "five-year-plan-steel",
                "collective-farm-consolidation",
                "state-security-purge",
            ],
        },
    },
    {
        "corner": CORNER_ID,
        "session": {
            "title": "第3期最高会議 — 消費財優先路線",
            "republic": REPUBLIC,
            "laws": [
                "consumer-goods-priority",
                "workers-council-reform",
                "cultural-enlightenment-decree",
                "rural-electrification",
                "anti-corruption-campaign",
                "price-control-reform",
            ],
        },
    },
)


def session_count() -> int:
    return len(SESSION_BANK)


def session_for_index(index: int) -> dict:
    """The bank entry at ``index``; the caller owns the bounds check."""

    if type(index) is not int or isinstance(index, bool) or not 0 <= index < len(SESSION_BANK):
        raise IndexError("lawsim session index is out of the bank")
    return SESSION_BANK[index]


def select_index(generation: int) -> int:
    """Deterministic rotation: one bank step per switch generation.

    The generation is a positive per-runtime counter owned by GameSwitch, so
    consecutive corner runs walk the bank in order and the same generation
    always rebuilds the same session (byte-identical trace via the engine).
    """

    if type(generation) is not int or isinstance(generation, bool) or generation < 1:
        raise ValueError("lawsim session selection requires a positive generation")
    return (generation - 1) % len(SESSION_BANK)
