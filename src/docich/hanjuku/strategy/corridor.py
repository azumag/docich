"""Chapter 9 before its boss phase: send every general to the next castle.

Owner 2026-11-11: ９話においては単純に機械的に次の城にすべての将軍を送っていき、
それ以外の操作はdisableにしてほしい. Earlier the road ran through the normal
planner (garrison-named orders, in-flight caps, retake reservations, adjusted
plans whose steps run once) and kept stalling on one gate after another.

Mechanical mode replaces all of that with castle-scoped orders:

* the castle beside the next road castle attacks it (a retake when it was ours);
* every other owned castle moves one hop along the road toward it;
* each order names nobody: the deploy step sends whoever the list cursor is on
  (the hero last, never in an attack) and takes cards from the top;
* an order's step id carries a per-castle sequence, so the next order from the
  same castle is a fresh step as soon as the previous one left;
* a castle whose live list showed nobody usable is skipped until something is
  sent into it or the month changes (``drained``).

The boss phase (every road castle ours) keeps the normal flows.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from ..os_layer.world import _owned
from ..state.regions import plan as _plan_region, sorties as _sorties
from . import routes

PREFIX = 'I:c9:'
DRAINED = 'corridor_drained'


def mechanical(mem) -> bool:
    chapter = mem.get('chapter')
    if not chart.guard_free(chapter) or not chart.corridor(chapter):
        return False
    owned = _owned(mem) & set(chart.castles(chapter))
    return chart.corridor_next(chapter, owned) is not None


def _drained(mem, castle, purpose) -> bool:
    entry = (mem.get(DRAINED) or {}).get(castle)
    if not isinstance(entry, dict) or entry.get('month') != mem.get('month'):
        return False
    if entry.get('kind') == 'hero' and purpose == 'move':
        return False                       # the hero alone still walks forward
    since = entry.get('tick')
    if type(since) is not int:
        return False
    for sortie in _sorties(mem).values():
        if (isinstance(sortie, dict) and sortie.get('target') == castle
                and type(sortie.get('tick')) is int and sortie['tick'] > since):
            return False                   # somebody was sent in afterwards
    return True


def mark_drained(mem, castle, kind):
    """``kind``: 'empty' (no general) or 'hero' (only the hero; no attack)."""
    mem.setdefault(DRAINED, {})[castle] = {'kind': kind, 'tick': int(mem.get('tick') or 0),
                                          'month': mem.get('month')}


def _step(mem, source, target):
    prefix = f'{PREFIX}{source}>{target}:'
    status = _plan_region(mem).get('orders') or {}
    used = sum(1 for step, state in status.items()
               if step.startswith(prefix) and state not in (None, 'pending'))
    return f'{prefix}{used}'


def orders(mem) -> list[dict]:
    if not mechanical(mem):
        return []
    chapter = mem.get('chapter')
    owned = _owned(mem) & set(chart.castles(chapter))
    target = chart.corridor_next(chapter, owned)
    lost = set(mem.get('lost') or ())
    attacks, moves = [], []
    for source in sorted(owned):
        route = routes.path(mem, source, target, owned)
        if not route or len(route) < 2:
            continue
        if len(route) == 2:
            purpose = 'retake' if target in lost else 'attack'
            hop = target
        else:
            purpose, hop = 'move', route[1]
        if _drained(mem, source, purpose):
            continue
        order = {'step': _step(mem, source, hop), 'general': chart_adjust.PROBE_GENERAL,
                 'source': source, 'target': hop, 'cards': [], 'after': None,
                 'purpose': purpose, 'source_probe': True, 'corridor': True,
                 'note': (f'第9話: {source}の全員を{target}へ' if hop == target
                          else f'第9話: {target}へ向けて{source}から{hop}へ全員移動')}
        (moves if purpose == 'move' else attacks).append((len(route), order))
    attacks.sort(key=lambda item: item[0])
    moves.sort(key=lambda item: item[0])      # nearest the front first
    return [order for _, order in (*attacks, *moves)]
