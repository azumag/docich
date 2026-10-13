"""Decision records shared by every Hanjuku layer.

``_record`` appends one structured decision (situation, chart step, strategy
variant, deviation, metrics) to the observation's record list; ``_tally``
keeps the bounded evidence counters shown on the status panel. Neither reads
the screen nor plans input.
"""
from __future__ import annotations


def _record(mem, kind, **fields):
    rec = {'decision': kind, 'chart_step': fields.pop('chart_step', mem.get('active')),
           'strategy_variant': fields.pop('strategy_variant', mem.get('variant', 'chart')),
           'chapter': mem.get('chapter'), 'deviation_reason': None,
           'expected_metric': None, 'observed_metric': None,
           'resulting_stage': None, 'resulting_event': None}
    rec.update(fields)
    mem.setdefault('_records', []).append(rec)
    return rec


def _tally(mem, key, amount=1):
    """Bounded, monotonically increasing evidence counter kept beside stats.

    `stats` only advances on a battle whose final HP panel was decisive, so a
    defeat seen on the world map (or a battle that ended before the panel was
    read) left no trace there. The corner status read "0 losses" while castles
    were visibly falling, so these counters give the status panel a complete,
    non-strategy-facing account. Nothing here feeds retries or experience.
    """
    counters = mem.setdefault('tally', {})
    value = counters.get(key)
    if type(value) is not int or value < 0:
        value = 0
    value = min(value + amount, 10 ** 6)
    counters[key] = value
    return value
