"""Read-only hero protection facts; list order is never an interception rule.

This model consumes explicitly attributed UI receipts, not garrison predictions.
It has no caller/input wiring until the receipt producer is independently verified.
"""
from . import hanjuku_chart as chart, hanjuku_roster as roster
from .hanjuku_egg_stock import observed_uses

MAX_AGE = 16


def _fresh(receipt, context, castle, kind):
    identity = roster.run_identity(context.get('identity'))
    return (isinstance(receipt, dict) and identity is not None
            and roster.run_identity(receipt.get('identity')) == identity
            and receipt.get('identity') == identity
            and type(context.get('chapter')) is int and context['chapter'] > 0
            and type(receipt.get('chapter')) is int and receipt['chapter'] == context['chapter']
            and isinstance(context.get('month'), str) and bool(context['month'])
            and receipt.get('month') == context['month'] and receipt.get('castle') == castle
            and receipt.get('kind') == kind
            and type(context.get('tick')) is int and type(receipt.get('tick')) is int
            and 0 <= receipt['tick'] <= context['tick'] <= receipt['tick'] + MAX_AGE)


def assess(context, castle, *, castle_status=None, castle_generals=None, hero_status=None):
    """Return observed facts/risk only; unknown observations stay unknown."""
    result = {'castle_level': None, 'general_count': None, 'hero_present': None,
              'hero_hp': None, 'hero_egg_uses': None, 'hero_list_tail': None,
              'hero_defense_tail': None, 'hero_protection_needed': None,
              'reorder_actions': []}
    if (not isinstance(context, dict) or type(context.get('chapter')) is not int
            or context['chapter'] <= 0 or not isinstance(castle, str)
            or castle not in chart.castles(context['chapter'])
            or not _fresh(castle_status, context, castle, 'castle_status')
            or castle_status.get('owner') != 'own'):
        return result
    level, count = castle_status.get('level'), castle_status.get('general_count')
    if type(level) is int and 1 <= level <= 5:
        result['castle_level'] = level
    if type(count) is int and 0 <= count <= roster.MAX_GENERALS:
        result['general_count'] = count
    if _fresh(castle_generals, context, castle, 'castle_generals'):
        names = castle_generals.get('names')
        if (isinstance(names, list) and len(names) <= roster.MAX_GENERALS
                and all(isinstance(n, str) and 1 <= len(n) <= 32
                        and not any(c.isspace() for c in n) and '\ufffd' not in n for n in names)
                and len(names) == len(set(names))
                and (result['general_count'] is None or len(names) <= result['general_count'])):
            complete = (castle_generals.get('complete') is True
                        and result['general_count'] is not None and len(names) == result['general_count'])
            result['hero_present'] = True if chart.HERO in names else False if complete else None
            result['hero_list_tail'] = bool(names and names[-1] == chart.HERO) if complete else None
    if (result['hero_present'] is True and _fresh(hero_status, context, castle, 'general_status')
            and hero_status.get('general') == chart.HERO and hero_status.get('location') == 'castle'
            and hero_status.get('location_observed') is True):
        hp, maximum, uses = (hero_status.get(k) for k in ('hp', 'max_hp', 'uses'))
        if type(hp) is int and type(maximum) is int and 0 <= hp <= maximum and maximum > 0:
            result['hero_hp'] = hp
        if observed_uses(uses) is not None:
            result['hero_egg_uses'] = uses
    if result['hero_present'] is False:
        result['hero_protection_needed'] = False  # only this castle's hero-specific concern
    elif result['hero_present'] is True and (result['general_count'] == 1
            or result['hero_hp'] is not None and result['hero_hp'] <= 12
            or result['hero_egg_uses'] == 0):
        result['hero_protection_needed'] = True
    return result
