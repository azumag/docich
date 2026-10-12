"""Status summary of the Hanjuku policy memory for the corner panel.

Read-only over ``mem``: no screen, no input, no decisions.
"""
from __future__ import annotations

from .os_layer.world import NAME


def summary(mem: dict | None) -> dict:
    """Bounded, secret-free chart progress for corner state and diagnostics."""
    mem = mem if isinstance(mem, dict) else {}
    stats = mem.get('stats') if isinstance(mem.get('stats'), dict) else {}
    tally = mem.get('tally') if isinstance(mem.get('tally'), dict) else {}
    orders = mem.get('orders') if isinstance(mem.get('orders'), dict) else {}
    battle = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    chart_step = battle.get('step') if battle else mem.get('active')
    strategy_variant = battle.get('strategy_variant') if battle else mem.get('variant')
    as_int = lambda v: v if type(v) is int and 0 <= v <= 10**6 else None
    battles_started = as_int(tally.get('battles_started'))
    battles_judged = as_int(tally.get('battles_judged'))
    # A started battle without a verdict is not a win and not a loss: the
    # status must say so instead of reporting the judged subset as the whole.
    battles_unjudged = (max(0, battles_started - battles_judged)
                        if battles_started is not None and battles_judged is not None else None)
    return {
        'chapter': as_int(mem.get('chapter')),
        'chart_step': chart_step if isinstance(chart_step, str) else None,
        'strategy_variant': strategy_variant if isinstance(strategy_variant, str) else None,
        'orders_launched': sum(1 for v in orders.values() if v == 'launched'),
        'orders_failed': sum(1 for v in orders.values() if v == 'failed'),
        'captured': len(mem.get('captured') or []),
        'wins': as_int(stats.get('wins')), 'losses': as_int(stats.get('losses')),
        'unclassified': as_int(stats.get('unclassified')),
        'battles_started': battles_started,
        'battles_judged': battles_judged,
        'battles_unjudged': battles_unjudged,
        'castle_losses': as_int(tally.get('castle_losses')),
        'cards_used': (as_int(stats.get('cards_used')) if stats.get('card_evidence_version') == 1
                       and not battle.get('card_flow')
                       and battle.get('card_consumption_complete', True) is True else None),
        # Lower bound confirmed under the new evidence contract, not total use.
        'cards_confirmed': as_int(stats.get('cards_confirmed')),
        # No direct loss observer exists: even a legacy zero is not evidence.
        'generals_lost': None,
        'gold': as_int(mem.get('gold')),
        'month': mem.get('month') if isinstance(mem.get('month'), str) else None,
        'name_entered': bool((mem.get('name') or {}).get('done')),
        'name_matches': (mem.get('name') or {}).get('typed') == NAME,
    }
