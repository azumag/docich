"""Chart edition of a run: the speedrun chart or the steady (堅実) variant.

The owner (2026-10-13) chose a steadier route that keeps margin over the
speedrun chart and accepted a slower chapter 1 (6-8 months), keeping the
speedrun edition for comparison. ``[hanjuku].chart_edition`` selects
``speedrun`` (default), ``steady`` or ``ab`` (alternate by generation).

The edition is pinned once, when a new game's name entry completes, so a
hot-loaded change never switches a running game. Memory without a pinned
edition (runs started before this existed, resumed saves) is ``speedrun``.
"""
from __future__ import annotations

SPEEDRUN = 'speedrun'
STEADY = 'steady'
AB = 'ab'
EDITIONS = (SPEEDRUN, STEADY)
SETTINGS = (SPEEDRUN, STEADY, AB)


def setting(raw) -> str:
    """Validated config value; anything else falls back to the speedrun chart."""
    return raw if isinstance(raw, str) and raw in SETTINGS else SPEEDRUN


def resolve(raw, generation) -> str:
    """The edition a new game starts with. ``ab`` alternates by generation
    parity (even = steady); an unknown generation stays on speedrun."""
    value = setting(raw)
    if value != AB:
        return value
    if type(generation) is not int or generation <= 0:
        return SPEEDRUN
    return STEADY if generation % 2 == 0 else SPEEDRUN


def current(mem) -> str:
    value = mem.get('chart_edition') if isinstance(mem, dict) else None
    return value if value in EDITIONS else SPEEDRUN


def steady(mem) -> bool:
    return current(mem) == STEADY
