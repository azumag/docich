"""戦略層: chart orders, sortie sources and garrison bookkeeping.

Which charted order is ready next, which general goes from which castle, the
hero-egg and boss-companion substitutions, reserved/retake targets and the
last-castle guard. Returns plans and records; never pad input.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust, hanjuku_reference as reference
from ...hanjuku_egg_reference import general_max_hp
from ..decision_log import _record
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _en_route, _owned
from .kit import _deploy_cards, _is_boss_order
from . import edition
from .execution import bound_fields, execution_claimed, execution_order


# ---------------------------------------------------------------- orders
def _sortie_general(order, mem):
    """Keep a confirmed departure's actor even if a plan/override changes."""
    step = order.get('step')
    bound = bound_fields(order, mem)
    if order.get('_execution_bound') is True:
        return bound['general'] if bound is not None else None
    context = (mem.get('order_context') or {}).get(step) or {}
    if mem.get('active') == step and 'departure_order' not in context:
        selected = context.get('actual_general') or (mem.get('sortie_general') or {}).get(step)
        if selected:
            return selected
    return (mem.get('general_override') or {}).get(step, order.get('general'))


def _hero_alternatives(mem, names):
    """Recognized idle companions; actual list selection must confirm each name.

    Garrison memory only permits opening the list, never a selection receipt.
    List order is retained; this is no prediction that a companion will win.
    """
    if not names or len(names) != len(set(names)):
        return []
    unavailable = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    return [g for g in names if g != NAME and general_max_hp(g) is not None and g not in unavailable]


def _order_hero_alternatives(order, mem, names):
    candidates = _hero_alternatives(mem, names)
    if not _is_boss_order(order, mem):
        return candidates
    # g706's selected シェーブル (27 HP, combat 4) entered a long road egg
    # fight; the isolated-tested hero has 90 HP/combat 14. Keep companions
    # with comparable primary stats, using the existing strength-gap rules.
    boss = (reference.general_strength(chart.BOSSES.get(mem.get('chapter')))
            or reference.general_strength('しゅじんこう'))
    if boss is None:
        return []
    fit = [name for name in candidates
           if reference.general_strength(name) is not None and not _boss_companion_unfit(mem, name)]
    strong = [name for name in fit
              if (stats := reference.general_strength(name)) is not None
              and boss[0] - stats[0] < reference.STRONG_GENERAL_HP_GAP
              and boss[1] - stats[1] < reference.STRONG_GENERAL_COMBAT_GAP]
    if strong:
        return strong
    # Owner (2026-10-14): waiting for a comparable companion left the boss
    # castle unattacked for a whole run while healthy generals sat in other
    # castles. A fit but weaker substitute still attacks, strongest first.
    # The hero is never sent (owner: 主人公boss禁止), and an injured or
    # egg-depleted substitute is still left to recover.
    return sorted(fit, key=lambda name: (reference.general_strength(name) or (0, 0))[:2],
                  reverse=True)


def _boss_companion_unfit(mem, name):
    status = (mem.get('house_eggs') or {}).get(name) or {}
    # Known injuries and broken/depleted eggs are reasons to prepare first;
    # absent readings never fabricate recovery or a full egg.
    hp, maximum = status.get('hp'), status.get('max_hp')
    return (status.get('broken') is True or status.get('uses') == 0
            or (type(hp) is int and type(maximum) is int and maximum > 0
                and hp * 10 < maximum * 6))


def _boss_companion_source(order, mem):
    """Recorded garrisons permit opening a list, never prove a departure."""
    garrison = mem.get('garrison') or {}
    source = _source(order, mem)
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    castles = [source] + sorted(owned - {source})
    return next((castle for castle in castles if castle in owned
                 and _order_hero_alternatives(order, mem, garrison.get(castle))), None)


def _hero_source_alternative(order, mem):
    return (order.get('general') == NAME
            and bool(_order_hero_alternatives(order, mem, (mem.get('garrison') or {}).get(_source(order, mem)))))


def _gate_dicts(mem, *keys):
    for key in keys:
        value = mem.get(key)
        if isinstance(value, dict):
            return value, key
    return None, keys[0]


def _hero_broken_sortie_suppressed(order, mem) -> bool:
    step = order.get('step')
    gates, _ = _gate_dicts(mem, 'hero_broken_sortie_gates', 'broken_hero_sortie_gates',
                           'hero_broken_egg_sortie_gates')
    if not isinstance(gates, dict):
        gates = {}
    gate = gates.get(step) if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return False
    if not _hero_egg_broken(mem):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    current_alternative = _hero_source_alternative(order, mem)
    if gate.get('alternative') != current_alternative:
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    if gate.get('month') != mem.get('month'):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    count = gate.get('count', 0)
    if count >= HERO_BROKEN_SORTIE_CANCEL_LIMIT:
        gates.pop(step, None)
        mem.setdefault('orders', {})[step] = 'failed'
        return True
    return True


def _sortie_cancelled_observed_suppressed(order, mem) -> bool:
    step = order.get('step')
    gates, _ = _gate_dicts(mem, 'sortie_cancelled_observed_gates', 'sortie_cancelled_gates',
                           'sortie_cancel_gates', 'sortie_cancel_observed_gates', 'cancelled_sortie_gates')
    if not isinstance(gates, dict):
        gates = {}
    gate = gates.get(step) if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return False
    general = gate.get('general')
    source = gate.get('source')
    if gate.get('month') != mem.get('month'):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    garrison = (mem.get('garrison') or {}).get(source)
    general_present = isinstance(garrison, list) and general in garrison
    if not general_present:
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    sortie = (mem.get('sorties') or {}).get(step)
    if not (isinstance(sortie, dict) and sortie.get('status') == 'cancelled'):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    count = gate.get('count', 0)
    if count >= SORTIE_CANCELLED_OBSERVED_LIMIT:
        gates.pop(step, None)
        mem.setdefault('orders', {})[step] = 'failed'
        return True
    return True


def _source_defender_suppressed(order, mem) -> bool:
    step = order.get('step')
    gates, _ = _gate_dicts(mem, 'sortie_held_source_defender_gates', 'source_defender_cancel_gates',
                           'sortie_held_source_defender_cancel_gates', 'sortie_source_defender_cancel_gates')
    if not isinstance(gates, dict):
        gates = {}
    gate = gates.get(step) if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return False
    source = gate.get('source') or _source(order, mem)
    if gate.get('month') != mem.get('month'):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    if source != _source(order, mem):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    current_present = (mem.get('garrison') or {}).get(source)
    current_present_tuple = tuple(sorted(current_present)) if isinstance(current_present, list) else None
    if current_present_tuple != gate.get('present'):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    busy, _ = _en_route(mem)
    unknown = set(mem.get('general_location_unknown') or ())
    unavailable = busy | unknown
    current_available = None
    if isinstance(current_present, list):
        current_available = tuple(sorted(set(current_present) - unavailable))
    if current_available != gate.get('available'):
        gates.pop(step, None)
        if (mem.get('orders') or {}).get(step) == 'failed':
            mem.setdefault('orders', {})[step] = None
        return False
    count = gate.get('count', 0)
    if count >= SOURCE_DEFENDER_CANCEL_LIMIT:
        gates.pop(step, None)
        mem.setdefault('orders', {})[step] = 'failed'
        return True
    return True


def _record_source_defender_cancel(mem, order, present=None, available=None):
    step = order.get('step')
    if not step:
        return
    source = _source(order, mem)
    month = mem.get('month')
    present_tuple = None
    if isinstance(present, list):
        present_tuple = tuple(sorted(present))
    elif isinstance(present, (tuple, set, frozenset)):
        present_tuple = tuple(sorted(present))
    busy, _ = _en_route(mem)
    unknown = set(mem.get('general_location_unknown') or ())
    unavailable = busy | unknown
    if available is None and isinstance(present, list):
        available = tuple(sorted(set(present) - unavailable))
    elif isinstance(available, (list, tuple, set, frozenset)):
        available = tuple(sorted(available))
    counts = mem.setdefault('sortie_held_source_defender_counts', {})
    for key in ('source_defender_cancel_counts', 'sortie_held_source_defender_cancel_counts',
                'sortie_source_defender_cancel_counts'):
        mem[key] = counts
    counts[step] = counts.get(step, 0) + 1
    count = counts[step]
    gates = mem.setdefault('sortie_held_source_defender_gates', {})
    for key in ('source_defender_cancel_gates', 'sortie_held_source_defender_cancel_gates',
                'sortie_source_defender_cancel_gates'):
        mem[key] = gates
    limit = SOURCE_DEFENDER_CANCEL_LIMIT
    if count >= limit:
        gates.pop(step, None)
        mem.setdefault('orders', {})[step] = 'failed'
    else:
        gates[step] = {'source': source, 'month': month, 'present': present_tuple,
                       'available': available, 'count': count}


def _record_sortie_cancelled_observed(mem, order, sortie):
    step = order.get('step')
    if not step or not isinstance(sortie, dict):
        return
    general = sortie.get('general')
    source = sortie.get('source')
    month = mem.get('month')
    counts = mem.setdefault('sortie_cancelled_observed_counts', {})
    for key in ('sortie_cancelled_counts', 'sortie_cancel_counts', 'sortie_cancel_observed_counts',
                'cancelled_sortie_counts', 'sortie_cancel'):
        mem[key] = counts
    counts[step] = counts.get(step, 0) + 1
    count = counts[step]
    gates = mem.setdefault('sortie_cancelled_observed_gates', {})
    for key in ('sortie_cancelled_gates', 'sortie_cancel_gates', 'sortie_cancel_observed_gates',
                'cancelled_sortie_gates'):
        mem[key] = gates
    limit = SORTIE_CANCELLED_OBSERVED_LIMIT
    if count >= limit:
        gates.pop(step, None)
        mem.setdefault('orders', {})[step] = 'failed'
    else:
        gates[step] = {'general': general, 'source': source, 'month': month,
                       'count': count, 'target': sortie.get('target')}



def _boss_egg_depleted(order, mem) -> bool:
    # g486: どうし left with 3 uses and lost to the Queen's Hydra after
    # the second card missed its input window. Chapter 1's source recommends
    # a fully recovered egg as the backup; do not invent a missing count.
    uses = (mem.get('egg_uses') or {}).get(NAME)
    return (mem.get('chapter') == 1 and _sortie_general(order, mem) == NAME
            and _is_boss_order(order, mem) and type(uses) is int and 0 <= uses < 4)


def _hero_egg_broken(mem):
    # Only a read status is authoritative. A house dispatch/timeout is not
    # repair; only a later real non-broken status releases this guard.
    return (mem.get('house_eggs') or {}).get(NAME, {}).get('broken') is True


def _broken_hero_order(order, mem):
    general = _sortie_general(order, mem)
    return (general == NAME and _hero_egg_broken(mem)
            and order.get('purpose') != 'move'
            and order.get('target') in chart.castles(mem.get('chapter') or 0))


def _all_normal_captured(mem):
    """Boss unlock covers the chapter's full names, including unmeasured ones."""
    chapter = mem.get('chapter')
    chapter_names = chart.CASTLE_NAMES.get(chapter) if type(chapter) is int else None
    captured = mem.get('captured', [])
    if not chapter_names or not isinstance(captured, (list, tuple, set, frozenset)):
        return False
    chapter_castles = set(chapter_names) - {chart.home_castle(chapter), chart.boss_castle(chapter)}
    return chapter_castles <= set(captured)


def _boss_target_unlocked(order, mem):
    # An adjusted after=None (including normalized self-capture conditions),
    # a retry or a hotloaded active order cannot remove the game prerequisite.
    if not chart.is_boss_castle(order.get('target')):
        return True
    chapter = mem.get('chapter')
    if chapter == 9 or (chapter == 4 and order.get('target') not in chart.boss_sites(4)):
        return False
    return _is_boss_order(order, mem) and _all_normal_captured(mem)


def _ready(order, mem) -> bool:
    if '_run_identity' in mem:
        # An adopted/hotloaded plan may outlive its native map receipt. The
        # runtime must never navigate either endpoint from that old plan.
        # decide() always supplies this key: None must not opt out of checks.
        cells = chart.castles(mem.get('chapter') or 0)
        if _source(order, mem) not in cells or order.get('target') not in cells:
            return False
    if _sortie_cancelled_observed_suppressed(order, mem):
        return False
    if _source_defender_suppressed(order, mem):
        return False
    if _hero_broken_sortie_suppressed(order, mem):
        return False
    if not _boss_target_unlocked(order, mem):
        return False
    if ('step' in order and _is_boss_order(order, mem) and order.get('purpose') != 'move'
            and _boss_companion_source(order, mem) is None
            and (mem.get('garrison') or {}).get(_source(order, mem)) is not None):
        return False
    if ((_broken_hero_order(order, mem) or _boss_egg_depleted(order, mem))
            and not (_boss_companion_source(order, mem) if _is_boss_order(order, mem)
                     else _hero_source_alternative(order, mem))):
        return False
    after = order['after']
    captured = set(mem.get('captured', []))
    if after is None:
        return True
    if after[0] == 'captured':
        return after[1] in captured
    if after[0] == 'all_captured':
        return _all_normal_captured(mem)
    return False


def _orders(mem):
    """The order list in force: an adopted adjusted plan, else the base chart.

    The adopted plan (``chart_plan``) is separate from the request state
    (``chart_adjust``): a new off-chart request never discards a plan that is
    still waiting on a capture; only adopting a newer validated plan does.
    While no plan is waiting, one JEV-chosen interim order may run.
    """
    plan = mem.get('chart_plan') or {}
    interim = (mem.get('chart_adjust') or {}).get('interim_order')
    # An interim order only exists when no plan order is waiting (see
    # ``_off_chart``), so it may follow an exhausted plan as well as the base.
    base = plan['orders'] if plan.get('orders') else chart.orders(mem.get('chapter') or 0)
    return (*base, *((interim,) if interim else ()))


def _order_for_step(mem, step):
    """The order behind an execution id, even after its plan was replaced.

    A launched order's snapshot (``launched_orders``) keeps its general, cards,
    target and tactics until its battle and retries are done.
    """
    if not step:
        return None
    frozen = execution_order(mem, step)
    if frozen is not None:
        return frozen
    if execution_claimed(mem, step):
        return None  # foreign/stale execution is not a fresh plan or actor
    return (next((o for o in (*_orders(mem), *chart.orders(mem.get('chapter') or 0))
                  if o['step'] == step), None)
            or (mem.get('launched_orders') or {}).get(step))


def _plan_pending(mem) -> bool:
    """An adopted plan still has an order that can run, or one waiting on a
    capture that a marching unit is making.

    Orders that can never run (source lost, capture nobody is making) kept
    off-chart sorties (retake, staffing) off for an hour in g407.
    """
    status = mem.get('orders') or {}
    owned = _owned(mem)
    sorties = mem.get('sorties') or {}
    plan = (mem.get('chart_plan') or {}).get('orders') or ()
    # A recent sortie, or a launched plan order with no sortie record to age.
    heading = _en_route(mem)[1] | {o['target'] for o in plan
                                   if status.get(o['step']) == 'launched' and o['step'] not in sorties}

    garrison = mem.get('garrison') or {}

    def live(o):
        after = o.get('after') or ()
        if after and after[0] == 'captured' and after[1] not in owned:
            return after[1] in heading          # waits on a capture somebody is making
        # A source read empty cannot start the order (g438: dead ココット's J1).
        return (_ready(o, mem) and _source(o, mem) in owned
                and garrison.get(_source(o, mem)) != []
                and not _retake_reserved(o, mem) and not _source_spare_missing(o, mem, allow_probe=True))

    return any(status.get(o['step']) in (None, 'pending') and live(o) for o in plan)


def _boss_retry_due(order, mem) -> bool:
    """A boss sortie failed only by cancelled targets gets its remaining retries
    (g421 13:48: the tower-roof check cancelled どうし twice before #1251)."""
    return ((mem.get('orders') or {}).get(order['step']) == 'failed'
            and order.get('target') == chart.boss_castle(mem.get('chapter') or 0)
            and 0 < (mem.get('target_cancel') or {}).get(order['step'], 0) < BOSS_TARGET_CANCEL_LIMIT)


def next_order(mem):
    status = mem.setdefault('orders', {})
    current = list(_orders(mem))
    steps = {o['step'] for o in current}
    # A launched order put back to pending by a lost battle keeps its retry
    # even if a newer plan replaced the one it came from.
    retries = [o for step, o in (mem.get('launched_orders') or {}).items()
               if step not in steps and status.get(step) == 'pending']
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    garrison = mem.get('garrison') or {}
    # The base chart's remaining orders follow an adopted plan that cannot run
    # (g438 03:48: the plan's only order sent the dead ココット from an empty
    # ジョンリギ, so the bot idled with every castle but the boss's taken while
    # the base 1-B1 boss sortie was never looked at).
    base = [o for o in chart.orders(mem.get('chapter') or 0)
            if o['step'] not in steps and o['target'] not in owned] if mem.get('chart_plan') else []
    chart_steps = {o['step'] for o in chart.orders(mem.get('chapter') or 0)}
    for order in (*current, *retries, *base):
        if ((order['step'] in chart_steps or _is_boss_order(order, mem))
                and (status.get(order['step']) in (None, 'pending') or _boss_retry_due(order, mem))
                and _ready(order, mem)):
            _follow_general(mem, order, garrison, owned)
        if ((status.get(order['step']) in (None, 'pending') or _boss_retry_due(order, mem))
                and _ready(order, mem)
                and _source(order, mem) in owned
                # A source last read empty would open an empty list and fail
                # the order (g407: the plan's どうし/ヴィーナス from an empty home).
                and garrison.get(_source(order, mem)) != []
                and not _last_castle_held(mem, order)      # its castle must keep its last general
                and not _retake_reserved(order, mem)
                and not _source_spare_missing(order, mem, allow_probe=True)
                and (mem.get('general_override', {}).get(order['step'], order['general']) not in _en_route(mem)[0]
                     or _hero_source_alternative(order, mem))):
            return order
    return None


def _follow_general(mem, order, garrison, owned):
    """Send the order from the castle its general is recorded in (g438: 1-B1
    assumes どうし at スペンソニア, but he held ゴーメン)."""
    general = order.get('general')
    owned = owned & set(chart.castles(mem.get('chapter') or 0))
    source = _source(order, mem)
    if _is_boss_order(order, mem) and order.get('purpose') != 'move':
        where = _boss_companion_source(order, mem)
        if where in owned and where != source:
            mem.setdefault('source_override', {})[order['step']] = where
            _record(mem, 'boss_companion_source', chart_step=order['step'],
                    observed_metric={'source': source, 'companion_at': where},
                    reason='主人公を出撃させず、十分な能力の代役が記録された城の実一覧を確認する')
        return
    if general == NAME and _order_hero_alternatives(order, mem, garrison.get(source)):
        return  # inspect the chart source's companion rather than chase the hero
    if not general or general in (garrison.get(source) or ()):
        return
    where = next((c for c, names in garrison.items() if general in (names or ()) and c in owned), None)
    if where and where != source and not mem.get('source_override', {}).get(order['step']):
        mem.setdefault('source_override', {})[order['step']] = where
        _record(mem, 'order_source_changed', chart_step=order['step'], strategy_variant='follow_general',
                observed_metric={'source': source, 'general_at': where},
                reason=f'{general}は{where}にいると記録されているため、そこから出撃させる')


def _order(mem):
    return _order_for_step(mem, mem.get('active'))


def _tactics(mem, step):
    """Battle tactics for a step: the base chart's, plus derived ones for adjusted
    and interim orders (their steps never match a base tactic's step).

    A carried card reuses every verified base tactic for that card (its enemy
    and timing), re-keyed to this step. A card with no verified tactic uses the
    explicit default: once at the battle opening, the same mechanism and
    evidence guards as a retry's opening cards. An added strong-card kit is
    handled only by its strong-only tactic below. Only cards the sortie actually
    carried count (``_deploy_cards``): a card left behind at card select must
    never be planned or announced in battle (g438 04:18: the bot opened the
    card menu for ミックミー after the sortie had dropped it). A boss-castle
    order's unverified card opens only in the measured boss entry, so the boss
    kit never fires in the road/guard fights on the way there.
    """
    base = chart.tactics(mem.get('chapter') or 0)
    order = _order_for_step(mem, step)
    if order is None and execution_claimed(mem, step):
        return ()
    bound = bound_fields(order, mem)
    roles = bound['roles'] if bound is not None else None
    rare_kit = roles['rare'] if roles is not None else (mem.get('rare_card_kit') or {}).get(step)
    strong_kit = (roles['strong'] if roles is not None else
                  None if rare_kit is not None else (mem.get('strong_card_kit') or {}).get(step))
    rare = ()
    carried = None
    if rare_kit is not None and order:
        carried = (list(order['cards']) if bound_fields(order, mem) is not None else
                   list(((mem.get('order_context') or {}).get(step) or {}).get('observed_metric', {}).get('cards') or []))
        for card in (mem.get('kit_spent') or {}).get(step) or ():
            if card in carried:
                carried.remove(card)
        base = tuple(t for t in base if t.get('step') not in (None, step)
                     or t['card'] in carried)
        if 'キャトルミュー' in carried:
            rare = ({'enemy': None, 'card': 'キャトルミュー', 'open': True, 'step': step,
                     'tactic_id': f'rare:{step}:キャトルミュー',
                     'boss_only': _is_boss_order(order, mem),
                     'note': 'レアイベント札を活用: 通常将軍を一撃、EMへ224と石化、ボスへ90ダメージ'},)
        elif (mem.get('chapter') == 2 and step == '2-B1'
              and _is_boss_order(order, mem) and 'バルムンク' in carried):
            used = ((mem.get('kit_spent') or {}).get(step) or []).count('バルムンク')
            rare = tuple({'enemy': chart.BOSSES[2], 'card': 'バルムンク',
                          'open': True, 'step': step, 'boss_only': True,
                          'tactic_id': f'rare:{step}:バルムンク:{used + index}',
                          'note': '実携行を確認したイベント札を実測第2話ボスへ使用'}
                         for index in range(carried.count('バルムンク')))
    if order is None or any(o['step'] == step for o in chart.orders(mem.get('chapter') or 0)):
        return (*rare, *base)
    override = (set(roles['override']) if roles is not None else
                set() if rare_kit is not None else set((mem.get('card_override') or {}).get(step) or ()))
    if carried is None:
        carried = _deploy_cards(order, mem)
    carried = [card for card in carried
               if card not in override and card != strong_kit
               and not (rare and card == 'キャトルミュー')]
    derived, seen = [], set()
    occurrences = {}
    spent = (mem.get('kit_spent') or {}).get(step, [])
    for card in carried:
        verified = [t for t in base if t['card'] == card]
        if verified:
            if card in seen:
                continue
            seen.add(card)
            derived += [{**t, 'step': step, 'note': f"調整: {t['note']}"} for t in verified]
        else:
            ordinal = spent.count(card) + occurrences.get(card, 0)
            occurrences[card] = occurrences.get(card, 0) + 1
            # Removing the first spent card must not renumber the second
            # into an already-done tactic (two carried イッテツーン).
            derived.append({'enemy': None, 'card': card, 'open': True, 'step': step,
                            'tactic_id': f'd:{step}:{card}:{ordinal}',
                            'boss_only': _is_boss_order(order, mem),
                            'note': '調整チャート既定: 検証済み戦術のない携行切り札を開幕使用'})
    return (*rare, *base, *derived)


def _distance(chapter, a, b) -> int:
    castles = chart.castles(chapter)
    (ax, ay), (bx, by) = castles[a], castles[b]
    return abs(ax - bx) + abs(ay - by)


def _sortie_reserved_target(mem, step, sortie):
    if sortie.get('target'):
        return sortie['target']
    if sortie.get('status') == 'launched_unconfirmed':
        # Reserve the intended destination without claiming observed arrival.
        # An interrupted target selection must not dispatch reinforcements.
        return ((mem.get('launched_orders') or {}).get(step) or {}).get('target')
    return None


def _reserved_targets(mem):
    now = int(mem.get('tick') or 0)
    return {_sortie_reserved_target(mem, step, s)
            for step, s in (mem.get('sorties') or {}).items()
            if s.get('status') in ('en_route', 'launched_unconfirmed')
            and s.get('tick') is not None
            and 0 <= now - int(s['tick']) < SORTIE_BUSY_TICKS} - {None}


def _retake_order(order, mem):
    return (not _is_boss_order(order, mem)
            and (order.get('purpose') == 'retake'
                 or order.get('target') in (mem.get('lost') or ())))


def _defense_break_allowed(order, mem):
    """Use the monthly fallback only for the exact mechanically chosen actor/route.

    A chart field alone is not permission. Revalidate the current monthly
    evaluation, loss latch and effective actor/source at the execution gate.
    """
    interim = (mem.get('chart_adjust') or {}).get('interim_order')
    wait = mem.get('defense_wait') or {}
    if (not isinstance(interim, dict)
            or any(order.get(k) != interim.get(k) for k in
                   ('step', 'general', 'source', 'target', 'purpose'))
            or not str(order.get('step', '')).startswith(chart_adjust.INTERIM_PREFIX)
            or type(wait.get('cycles')) is not int or wait['cycles'] < 1
            or not mem.get('month') or wait.get('eval_month') != mem['month']):
        return False
    source = _source(order, mem)
    return (source == interim.get('source') and source in _owned(mem)
            and _sortie_general(order, mem) == interim.get('general')
            and interim.get('general') in ((mem.get('garrison') or {}).get(source) or ())
            and interim.get('general') not in (_en_route(mem)[0]
                                              | set(mem.get('general_location_unknown') or ()))
            and source not in (mem.get('lost') or ())
            and source not in (mem.get('defense_break_blocked') or ())
            and (mem.get('defense_break_sources') or {}).get(source) == interim.get('target'))


def _reserve_source_guard(order, mem):
    if _defense_break_allowed(order, mem):
        return False
    # Original chart offensives and boss waves retain their sequences. A
    # recapture or an off-chart attack must leave a measured idle defender.
    # Adjusted orders normally omit purpose; only explicit moves are exempt.
    # The steady edition holds base chart advances to the same rule: a
    # captured castle is not left empty to keep the speedrun line moving.
    return (_retake_order(order, mem)
            or (edition.steady(mem) and not _is_boss_order(order, mem)
                and order.get('purpose') != 'move'
                and not order['step'].startswith((chart_adjust.PLAN_PREFIX, chart_adjust.INTERIM_PREFIX)))
            or (not _is_boss_order(order, mem) and order.get('purpose') != 'move'
                and order['step'].startswith(chart_adjust.PLAN_PREFIX))
            or (order.get('purpose') == 'attack' and order['step'].startswith('I:')))


def _retake_reserved(order, mem):
    if (not _retake_order(order, mem)
            or (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed'):
        return False
    now = int(mem.get('tick') or 0)
    return any(step != order['step'] and _sortie_reserved_target(mem, step, s) == order.get('target')
               and s.get('status') in ('en_route', 'launched_unconfirmed')
               and s.get('tick') is not None
               and 0 <= now - int(s['tick']) < SORTIE_BUSY_TICKS
               for step, s in (mem.get('sorties') or {}).items())


def _unplaced_castle_generals(mem):
    """Generals read in a castle this month whom no garrison list holds.

    g684 2-5 13:45-14:18: アマンディーヌ and カシュー had joined (status:
    location castle) but no castle list had been reopened, so every garrison
    read one general, every sortie lacked a spare and nothing launched for
    30 minutes (owner 2026-10-11: しょうぐんがいるのに新規出撃をしてない).
    """
    month = mem.get('month')
    placed = {g for gs in (mem.get('garrison') or {}).values() for g in gs or ()}
    busy = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    return sorted(name for name, info in (mem.get('house_eggs') or {}).items()
                  if isinstance(info, dict) and info.get('location') == 'castle'
                  and info.get('month') == month and name not in placed and name not in busy)


def _arrival_castle(mem, owned):
    """Where joiners appear: the hero's castle (home if his list is unread).

    None once that castle's real list refuted the current unplaced set.
    """
    garrison = mem.get('garrison') or {}
    hero = [c for c in owned if NAME in (garrison.get(c) or ())]
    castle = hero[0] if len(hero) == 1 else chart.home_castle(mem.get('chapter') or 0)
    refuted = ((mem.get('unplaced_refuted_castles') or {}).get(castle)
               or mem.get('unplaced_refuted') or {})
    if (refuted.get('castle') == castle and refuted.get('month') == mem.get('month')
            and refuted.get('names') == _unplaced_castle_generals(mem)):
        return None
    return castle


SOURCE_LOCATION_RECHECK_LIMIT = 3
SORTIE_CANCELLED_OBSERVED_LIMIT = 3
SOURCE_DEFENDER_CANCEL_LIMIT = 3
HERO_BROKEN_SORTIE_CANCEL_LIMIT = 3


def _source_location_recheck(mem, source):
    """An apparent second idle name needs its own fresh location reading."""
    present = set((mem.get('garrison') or {}).get(source) or ()) - _en_route(mem)[0]
    unknown = present & set(mem.get('general_location_unknown') or ())
    if len(present) < 2 or not unknown or len(present - unknown) >= 2:
        return None
    scope = [mem.get('chapter'), mem.get('month'), sorted(present), sorted(unknown)]
    old = (mem.get('source_location_rechecks') or {}).get(source) or {}
    attempts = old.get('attempts', 0) if old.get('scope') == scope else 0
    if type(attempts) is not int or attempts < 0:
        attempts = SOURCE_LOCATION_RECHECK_LIMIT
    return {'scope': scope, 'attempts': attempts}


def _source_location_recheck_due(mem, source):
    recheck = _source_location_recheck(mem, source)
    return recheck is not None and recheck['attempts'] < SOURCE_LOCATION_RECHECK_LIMIT


def _source_spare_missing(order, mem, *, allow_probe=False):
    if (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed':
        return False  # reconcile an issued sortie; do not cancel its carried-kit receipt
    source = _source(order, mem)
    present = (mem.get('garrison') or {}).get(source)
    if _reserve_source_guard(order, mem) and _source_location_recheck(mem, source) is not None:
        return not (allow_probe and _source_location_recheck_due(mem, source))
    if allow_probe and order.get('source_probe') is True:
        return False  # open its verified list; never bypass the live-list/card guards
    unavailable = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    if (present is not None and _unplaced_castle_generals(mem)
            and source == _arrival_castle(mem, _owned(mem))):
        return False   # the list is read at the sortie screen (_keep_sortie_defender)
    return (_reserve_source_guard(order, mem) and present is not None
            and len(set(present) - unavailable) < 2)


def _finish_order(mem, state, **fields):
    step = mem.get('active')
    if step:
        mem.setdefault('orders', {})[step] = state
        if state == 'launched':
            # A new sortie carries a fresh kit: attempted uses from the
            # previous one must not shrink it (``kit_spent`` is per sortie).
            (mem.get('kit_spent') or {}).pop(step, None)
            (mem.get('sortie_confirm_miss') or {}).pop(step, None)
        _record(mem, 'order_' + state, chart_step=step, **fields)
    mem['active'] = None
    mem['picked'] = []


SOURCE_MISS_LIMIT = 3         # failed castle menus per order before giving the source up


def _source(order, mem):
    bound = bound_fields(order, mem)
    if order.get('_execution_bound') is True:
        return bound['source'] if bound is not None else None
    return mem.get('source_override', {}).get(order['step'], order['source'])


def _give_up_source(mem, order):
    """Bound A on a castle that never opens its menu (g401: 900 presses).

    The chart's own source falls back to the home castle once, as for an
    empty general list; after that the order fails with evidence so the next
    order (or an off-chart sortie) runs instead of a permanent loop.
    """
    step = order['step']
    source = _source(order, mem)
    if (mem.get('orders') or {}).get(step) == 'launched_unconfirmed':
        mem['sorties'][step]['verification_unavailable'] = True
        mem['active'] = None
        _record(mem, 'sortie_verification_held', chart_step=step,
                reason='出撃元の一覧を開けないため未確定の出撃を維持し再指示を抑止')
        return
    home = chart.home_castle(mem.get('chapter') or 0)
    # Navigation state is left to the nav_reset of this same miss: dropping
    # the cell would freeze the camera on open sea again (g358).
    misses = mem.get('source_miss', {}).pop(step, 0)
    garrison = mem.get('garrison') or {}
    if source != home and home in _owned(mem) and not mem.get('source_override', {}).get(step):
        mem.setdefault('source_override', {})[step] = home
        garrison.pop(source, None)
        _record(mem, 'order_source_changed', chart_step=step, strategy_variant='source_fallback',
                deviation_reason=f'{source}で決定しても城メニューが{misses}回開かない',
                observed_metric={'source': source, 'menu_miss': misses},
                reason='本城から出撃し直す')
        return
    _finish_order(mem, 'failed', deviation_reason=f'{source}で決定しても城メニューが{misses}回開かない',
                  observed_metric={'source': source, 'menu_miss': misses},
                  reason='出撃元の城を選べないため指示を諦めて次の指示へ進む')


def _near_goal(mem, order, name, mode):
    near = mem.get('near_goal') or {}
    return near.get('step') == order['step'] and near.get('goal') == name and near.get('mode') == mode


def _recall_goal(mem, flags=None):
    """Nearest owned castle; hero's castle wins ties or supplies an unknown origin."""
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    owned = sorted(c for c in _owned(mem) if c in cells
                   and (flags is None or flags.get(c) == 'own'))
    state = mem.get('recall') or {}
    hero = [c for c in owned if NAME in ((mem.get('garrison') or {}).get(c) or ())]
    hero_castle = (hero[0] if len(hero) == 1 and NAME not in _en_route(mem)[0]
                   and NAME not in (mem.get('general_location_unknown') or ()) else None)
    origin = state.get('origin')
    if (isinstance(origin, (list, tuple)) and len(origin) == 2
            and all(type(value) is int for value in origin)):
        # Units march directly; axis-summed distance can rank a diagonal
        # nearer castle behind a farther one. Squaring avoids rounding ties.
        return min(owned, key=lambda c: (sum((cells[c][a] - origin[a]) ** 2 for a in (0, 1)),
                                         c != hero_castle, c)) if owned else None
    if hero_castle:
        return hero_castle
    for step in (mem.get('recall') or {}).get('sorties') or ():
        source = ((mem.get('sorties') or {}).get(step) or {}).get('source')
        if source in owned:
            return source
    return owned[0] if len(owned) == 1 else None
BOSS_TARGET_CANCEL_LIMIT = 4   # the boss sortie is worth more retries


def _garrison_move(mem, general, source=None, target=None):
    """A general left ``source`` and/or now holds ``target`` (known lists only)."""
    from ..os_layer.roster import dirty_status
    dirty_status(mem, [general] if general else None, fields=('hp', 'location'))
    garrison = mem.setdefault('garrison', {})
    # Movement-derived roster changes are not new observations.
    observed = mem.setdefault('garrison_observed_at', {})
    observed.pop(source, None)
    observed.pop(target, None)
    if source and garrison.get(source) is not None:
        garrison[source] = [g for g in garrison[source] if g != general]
    if target and general:
        mem['general_location_unknown'] = [g for g in mem.get('general_location_unknown') or () if g != general]
        here = [g for g in garrison.get(target) or () if g != general]
        garrison[target] = [*here, general]


LAST_CASTLE_HOLD_TICKS = 300   # observations before the last castle's list is read again


def _staffed_elsewhere(mem, source) -> bool:
    """Another castle of ours is known to hold a general who is not marching.

    A castle whose list was never read (or was cleared by a recruit) is not
    counted: the guard below errs towards keeping someone home.
    """
    busy, _ = _en_route(mem)
    garrison = mem.get('garrison') or {}
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    return any(c != source and any(g not in busy for g in garrison.get(c) or ()) for c in owned)


LAST_CASTLE_GUARD_OWNED = 2   # the guard only matters while so few castles remain


def _few_castles(mem) -> bool:
    """Owner (2026-09-29): with six castles the guard only cancelled the chart's
    hero sortie to スペンソニア on stream; a total loss (game over) is a risk
    only when one or two castles are left (g436 18:57: the last one fell)."""
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    return len(owned) <= LAST_CASTLE_GUARD_OWNED


def _last_castle_held(mem, order) -> bool:
    hold = mem.get('last_castle_hold') or {}
    source = _source(order, mem)
    return (not _is_boss_order(order, mem) and order.get('purpose') != 'move' and hold.get('castle') == source
            and _few_castles(mem) and not _staffed_elsewhere(mem, source)
            and int(mem.get('tick') or 0) - int(hold.get('tick') or 0) < LAST_CASTLE_HOLD_TICKS)


# ---------------------------------------------------------------- battles
def _match_sortie(mem, castle, general):
    """The execution id of the sortie a measured entry message belongs to.

    Returns ``(step, status)``: exactly one en-route sortie of this general to
    this castle is ``matched``; several are ``ambiguous`` (never guessed);
    none falls back to the legacy per-castle record.

    Only recent sorties count, with the same bound as ``_en_route``. An
    arrival that was never read leaves its ``en_route`` record behind (g749
    2026-10-14: sixty of them, including two ヴィーナス waves to けっかい and a
    キッシュ/ゴーメン pair from an older interim order). Without the bound the
    second record made every later entry message ``ambiguous``, so the arrival
    was never bound, the castle list never recorded the army that had actually
    arrived, and no further sortie could start.
    """
    sorties = mem.get('sorties') or {}
    now = int(mem.get('tick') or 0)
    candidates = [step for step, sortie in sorties.items()
                  if sortie.get('status') in ('en_route', 'launched_unconfirmed')
                  and sortie.get('target') in (None, castle)
                  and sortie.get('general') == general
                  # A record with no tick predates the bound: it stays a
                  # candidate so two of them are still ambiguous, never guessed.
                  and (sortie.get('tick') is None
                       or 0 <= now - int(sortie['tick']) < SORTIE_BUSY_TICKS)]
    if len(candidates) == 1:
        return candidates[0], 'matched'
    if candidates:
        return None, 'ambiguous'
    launched = (mem.get('launched') or {}).get(castle) or {}
    if launched.get('general') == general and launched.get('step') not in sorties:
        return launched.get('step'), 'legacy'
    return None, 'none'


def _bind_sortie(mem, step, castle):
    sortie = (mem.get('sorties') or {}).get(step)
    if sortie:
        if sortie.get('target') is None:
            _record(mem, 'sortie_arrival_confirmed', chart_step=step, general=sortie['general'],
                    castle=castle, reason='将軍名の一致する入城表示で未確定だった出撃と行先を確定')
            _garrison_move(mem, sortie['general'], source=sortie.get('source'))
            mem.setdefault('orders', {})[step] = 'launched'
            if mem.get('active') == step:
                mem['active'] = None
            if (mem.get('sortie_attempt') or {}).get('step') == step:
                mem.pop('sortie_attempt', None)
        sortie.update(status='arrived', target=castle)


def _transit_sorties(mem, castle, names):
    """In-flight sorties whose general is listed inside ``castle`` right now.

    The game stops a marching general inside any of our own castles on the
    route and announces nothing (owner 2026-10-14: only an enemy entry or an
    enemy attack prints a message), so the general list of that castle is the
    only measurement. A general still recorded as marching to another castle
    while his name is on this list has arrived here instead.
    """
    now = int(mem.get('tick') or 0)
    listed = set(names or ())
    out = []
    for step, sortie in (mem.get('sorties') or {}).items():
        source = sortie.get('source')
        if (sortie.get('status') not in ('en_route', 'launched_unconfirmed')
                or sortie.get('general') not in listed
                or sortie.get('purpose') in ('house', 'move')
                or sortie.get('target') in (None, castle)
                # The castle he left is not a transit: a sortie still listed at
                # its own source never departed, and the source verification
                # owns that case. An unrecorded source is not route evidence.
                or not source or source == castle
                or sortie.get('tick') is None
                or not 0 <= now - int(sortie['tick']) < SORTIE_BUSY_TICKS):
            continue
        out.append(step)
    return out


def _continue_transit_sorties(mem, castle, names):
    """Re-issue each order from the castle its general actually stopped in.

    Owner (2026-10-14): with the army scattered over several castles there was
    no route that carried an order through an intervening own castle; the
    sortie simply ended there. The same order keeps its general, cards and
    target, and runs again from this castle (``source_override``), so the
    march continues toward the original destination instead of idling.
    """
    changed = []
    listed = sorted(set(names or ()))
    for step in _transit_sorties(mem, castle, names):
        sortie = mem['sorties'][step]
        planned = sortie.get('target')
        _garrison_move(mem, sortie['general'], source=sortie.get('source'), target=castle)
        sortie.update(status='arrived', target=castle, transit_to=planned)
        mem.setdefault('source_override', {})[step] = castle
        mem.setdefault('orders', {})[step] = 'pending'
        if mem.get('active') == step:
            mem['active'] = None
        if (mem.get('sortie_attempt') or {}).get('step') == step:
            mem.pop('sortie_attempt', None)
        _record(mem, 'sortie_transit_continue', chart_step=step, general=sortie['general'],
                castle=castle, expected_metric={'target': planned},
                observed_metric=listed[:8],
                reason='経由した自城の一覧で将軍を確認したため、その城から目的地へ出撃し直す')
        changed.append(step)
    return changed


def _sortie_step_for(mem, general, castle):
    """The newest sortie of this general already bound to this castle.

    An ambiguous boss entry (g460 17:32: the base 1-B1 and the adjusted
    A:bd2304e3:K1 were both still marching on けっかい) must not bind an order
    id that was never read. The battle still has to fight with the kit of the
    sortie that is actually out there: with an unknown step every charted
    tactic is filtered out and the hero swings bare-handed (g460 17:32:
    planned_cards=[] -> ally HP 88..0 -> 17:35 game over)."""
    if not general or not castle:
        return None
    march = [(sortie.get('tick') or 0, step)
             for step, sortie in (mem.get('sorties') or {}).items()
             if sortie.get('general') == general
             and sortie.get('target') == castle
             and sortie.get('status') in ('en_route', 'arrived')]
    return max(march)[1] if march else None
