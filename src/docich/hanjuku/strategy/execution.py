"""A confirmed sortie's actor/source/kit, separate from its mutable plan.

The confirmation is not a departure or arrival receipt. Its binding survives
only in the same run/chapter and the same target-selection attempt or sortie.
"""
from __future__ import annotations

from copy import deepcopy

from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ...hanjuku_egg_reference import general_max_hp
from ..os_layer.roster import run_identity
from ..os_layer.world import NAME


def _role_cards(raw, cards):
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        return []
    remaining = list(raw)
    result = []
    for card in cards:
        if card in remaining:
            remaining.remove(card)
            result.append(card)
    return result


def _valid_roles(roles, cards):
    if not isinstance(roles, dict) or set(roles) != {'rare', 'strong', 'override'}:
        return False
    if roles['strong'] is not None and roles['strong'] not in cards:
        return False
    for key in ('rare', 'override'):
        value = roles[key]
        if key == 'rare' and value is None:
            continue
        if not isinstance(value, list) or _role_cards(value, cards) != value:
            return False
    return True


def _valid_departure(order, mem):
    if not isinstance(order, dict):
        return False
    binding = order.get('departure_binding')
    identity = run_identity(mem.get('_run_identity'))
    chapter = mem.get('chapter')
    if (not isinstance(binding, dict) or identity is None
            or run_identity(binding.get('identity')) != identity
            or binding.get('identity') != identity
            or type(chapter) is not int or not 1 <= chapter <= 12
            or type(binding.get('chapter')) is not int or binding['chapter'] != chapter
            or binding.get('step') != order.get('step')
            or not isinstance(order.get('step'), str) or not order['step']
            or type(binding.get('confirm_tick')) is not int or binding['confirm_tick'] < 0
            or type(mem.get('tick')) is not int or binding['confirm_tick'] > mem['tick']):
        return False
    actor, source, cards = binding.get('general'), binding.get('source'), binding.get('cards')
    return (isinstance(actor, str)
            and general_max_hp('しゅじんこう' if actor == NAME else actor) is not None
            and isinstance(source, str) and source in chart.castles(chapter)
            and isinstance(cards, list) and len(cards) <= 3
            and all(isinstance(c, str) and c in reference.ALL_CARD_IDS for c in cards)
            and _valid_roles(binding.get('roles'), cards)
            and order.get('general') == actor and order.get('source') == source
            and order.get('cards') == cards)


def confirm_departure_order(order, mem, general, source, cards):
    """Called only after this confirmation's named actor/kit/cursor were read."""
    if not isinstance(cards, (list, tuple)):
        return None
    snapshot = deepcopy(order)
    snapshot.pop('_execution_bound', None)
    snapshot.update(general=general, source=source, cards=list(cards))
    rare = _role_cards((mem.get('rare_card_kit') or {}).get(order['step']), list(cards))
    strong = (mem.get('strong_card_kit') or {}).get(order['step']) if rare is None else None
    if strong not in cards:
        strong = None
    snapshot['departure_binding'] = {
        'identity': run_identity(mem.get('_run_identity')), 'chapter': mem.get('chapter'),
        'step': order['step'], 'confirm_tick': mem.get('tick'),
        'general': general, 'source': source, 'cards': list(cards),
        'roles': {'rare': rare, 'strong': strong,
                  'override': (_role_cards((mem.get('card_override') or {}).get(order['step'], []), list(cards))
                               if rare is None else [])},
    }
    return snapshot if _valid_departure(snapshot, mem) else None


def execution_order(mem, step):
    """Resolve an in-flight attempt first, then its independently saved sortie."""
    context = (mem.get('order_context') or {}).get(step) or {}
    attempt = mem.get('sortie_attempt') or {}
    confirmed = context.get('departure_order')
    if (attempt.get('step') == step and _valid_departure(confirmed, mem)
            and type(attempt.get('confirmation_tick')) is int
            and attempt.get('confirmation_tick') == confirmed['departure_binding']['confirm_tick']):
        return {**deepcopy(confirmed), '_execution_bound': True}
    snapshot = (mem.get('launched_orders') or {}).get(step)
    sortie = (mem.get('sorties') or {}).get(step) or {}
    if ((mem.get('orders') or {}).get(step) in ('launched', 'launched_unconfirmed')
            and _valid_departure(snapshot, mem)
            and type(sortie.get('confirmation_tick')) is int
            and sortie.get('confirmation_tick') == snapshot['departure_binding']['confirm_tick']
            and sortie.get('general') == snapshot['general']
            and sortie.get('source') == snapshot['source']
            and sortie.get('status') in ('en_route', 'arrived', 'launched_unconfirmed')):
        return {**deepcopy(snapshot), '_execution_bound': True}
    return None


def bound_fields(order, mem):
    if isinstance(order, dict) and order.get('_execution_bound') is True and _valid_departure(order, mem):
        return order['departure_binding']
    return None


def departure_snapshot(order, mem, general, source):
    """Save actual metadata; untagged legacy context acquires no new authority."""
    confirmed = ((mem.get('order_context') or {}).get(order['step']) or {}).get('departure_order')
    if confirmed is not None:
        if not _valid_departure(confirmed, mem):
            return None
        snapshot = deepcopy(confirmed)
    else:
        snapshot = deepcopy(order)
        snapshot.update(general=general, source=source, cards=list(order['cards']),
                        after=list(order['after']) if order['after'] else None)
        snapshot.pop('departure_binding', None)
    snapshot.pop('_execution_bound', None)
    return snapshot


def execution_claimed(mem, step):
    """A stale tagged execution must not fall through to a new plan's actor."""
    attempt = mem.get('sortie_attempt') or {}
    snapshot = (mem.get('launched_orders') or {}).get(step) or {}
    return ((attempt.get('step') == step and 'confirmation_tick' in attempt)
            or ((mem.get('orders') or {}).get(step) in ('launched', 'launched_unconfirmed')
                and 'departure_binding' in snapshot))
