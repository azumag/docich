"""Chart edition of a run: the speedrun chart or the steady (堅実) variant.

The owner (2026-10-13) chose a steadier route that keeps margin over the
speedrun chart and accepted a slower chapter 1 (6-8 months), keeping the
speedrun edition for comparison. ``[hanjuku].chart_edition`` selects
``speedrun`` (default), ``steady`` or ``ab`` (an even split between runs).

The edition is pinned once, when a new game's name entry completes, so a
hot-loaded change never switches a running game. Memory without a pinned
edition (runs started before this existed, resumed saves) is ``speedrun``.
"""
from __future__ import annotations

import hashlib

SPEEDRUN = 'speedrun'
STEADY = 'steady'
AB = 'ab'
EDITIONS = (SPEEDRUN, STEADY)
SETTINGS = (SPEEDRUN, STEADY, AB)


def setting(raw) -> str:
    """Validated config value; anything else falls back to the speedrun chart."""
    return raw if isinstance(raw, str) and raw in SETTINGS else SPEEDRUN


def resolve(raw, runtime_id) -> str:
    """The edition a new game starts with.

    ``ab`` splits runs by a hash of the run's runtime id. Generation parity
    cannot be used: generations are shared by every game switch, so their
    parity follows the rotation order (41 of 63 production Hanjuku runs up
    to 2026-10-13 had an even generation; the hash puts 32 of the same 63 on
    steady). An unknown runtime id stays on speedrun.
    """
    value = setting(raw)
    if value != AB:
        return value
    if not isinstance(runtime_id, str) or not runtime_id:
        return SPEEDRUN
    digest = hashlib.sha256(runtime_id.encode('utf-8')).digest()
    return STEADY if digest[0] % 2 == 0 else SPEEDRUN


def current(mem) -> str:
    value = mem.get('chart_edition') if isinstance(mem, dict) else None
    return value if value in EDITIONS else SPEEDRUN


def steady(mem) -> bool:
    return current(mem) == STEADY
