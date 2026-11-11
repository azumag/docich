"""戦略層: chart orders, sortie sources and garrison bookkeeping.

Which charted order is ready next, which general goes from which castle, the
hero-egg and boss-companion substitutions, reserved/retake targets and the
last-castle guard. Returns plans and records; never pad input.
"""
from __future__ import annotations

from copy import deepcopy

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust, hanjuku_reference as reference
from ...hanjuku_egg_reference import GENERAL_EGGS, general_max_hp
from ..decision_log import _record, _tally
from ..os_layer.world import (NAME, SORTIE_BUSY_TICKS, _en_route, _owned,
                              BOSS_ARRIVAL_LIMIT, MARCH_TICKS_PER_CELL,
                              LONG_MARCH_TICKS, MARCH_ARRIVAL_CAP,
                              march_arrival_limit)
from .kit import _deploy_cards, _is_boss_order
from . import edition
from . import routes
from .execution import bound_fields, execution_claimed, execution_order
from ..state.regions import world as _world, flow as _flow, journal as _journal, sorties as _sorties, plan as _plan_region


# ---------------------------------------------------------------- orders
def _sortie_general(order, mem):
    """Keep a confirmed departure's actor even if a plan/override changes."""
    step = order.get('step')
    bound = bound_fields(order, mem)
    if order.get('_execution_bound') is True:
        return bound['general'] if bound is not None else None
    context = (_plan_region(mem).get('order_context') or {}).get(step) or {}
    if _plan_region(mem).get('active') == step and 'departure_order' not in context:
        selected = context.get('actual_general') or (_plan_region(mem).get('sortie_general') or {}).get(step)
        if selected:
            return selected
    return (_plan_region(mem).get('general_override') or {}).get(step, order.get('general'))


def _hero_alternatives(mem, names):
    """Recognized idle companions; actual list selection must confirm each name.

    Garrison memory only permits opening the list, never a selection receipt.
    List order is retained; this is no prediction that a companion will win.
    """
    if not names or len(names) != len(set(names)):
        return []
    unavailable = _en_route(mem)[0] | set(_world(mem).get('general_location_unknown') or ())
    return [g for g in names if g != NAME and general_max_hp(g) is not None and g not in unavailable]


def _order_hero_alternatives(order, mem, names, relaxed=True):
    # Duplicate names are ambiguous for a selection receipt. The unmeasured
    # egg-holder seat below iterates ``names`` directly, so reject here too.
    if not names or len(names) != len(set(names)):
        return []
    candidates = _hero_alternatives(mem, names)
    if not _is_boss_order(order, mem):
        return candidates
    site = order.get('target') in chart.boss_sites(4)
    if site:
        # Owner 2026-10-30: a 四季王 sortie still leaves HERO_CASTLE_GUARD
        # generals beside the hero when it leaves the hero's castle. The
        # 四季王 fights as a summoned monster from the first turn with no
        # きりふだ row (g1075 08:27:56 サマカンテ), so a general without an
        # egg can only melee it (カペリーニ/ミュスカデ were sent so).
        # Owner 2026-10-30 (1人残す): any other castle keeps one general too;
        # g1075 lost フヌイヌム (08:34) and ミヒラギアン (08:47) after their
        # lone ミュスカデ/アルテミス left for the sites.
        # Owner 2026-10-30 (卵持ちを残す): that one holds an egg; g1075 lost
        # アルカ (09:01:32) when the egg-less キール, kept as ヴィーナス left,
        # met リーキ's おーでーん summon.
        unavailable = _en_route(mem)[0] | set(_world(mem).get('general_location_unknown') or ())
        present = set(names or ()) - unavailable
        candidates = [g for g in candidates if _holds_egg(mem, g)
                      and not chart_adjust.hero_guard_short(names, g, unavailable)
                      and (NAME in present or any(_holds_egg(mem, s) for s in present - {g}))]
        if relaxed and not candidates and NAME in present:
            # Owner 2026-10-30 (定常的にボス城へ進行): strict guards may leave
            # no sortie-able general even at the hero's castle - g1075 held
            # every site order pending for an hour while a lone egg-holder
            # guarded each castle. An egg-holder still goes when the hero
            # keeps one other general beside him. Only the hero's castle
            # relaxes: an egg-less guard elsewhere lost アルカ to a summon.
            candidates = [g for g in _hero_alternatives(mem, names)
                          if g in present and _holds_egg(mem, g)
                          and len(present - {g, NAME}) >= 1]
    # g706's selected シェーブル (27 HP, combat 4) entered a long road egg
    # fight; the isolated-tested hero has 90 HP/combat 14. Keep companions
    # with comparable primary stats, using the existing strength-gap rules.
    if _boss_stats(mem) is None:
        return []
    fit = [name for name in candidates
           if reference.general_strength(name) is not None and not _boss_companion_unfit(mem, name)]
    strong = [name for name in fit if _boss_comparable(mem, name)]
    if strong:
        # Owner 2026-10-30 (四季王): 強い将軍から, so the strongest leads.
        return sorted(strong, key=_strength, reverse=True) if site else strong
    # Owner (2026-10-14): waiting for a comparable companion left the boss
    # castle unattacked for a whole run while healthy generals sat in other
    # castles. A fit but weaker substitute still attacks, strongest first.
    # The hero is never sent (owner: 主人公boss禁止), and an injured or
    # egg-depleted substitute is still left to recover.
    weak = sorted(fit, key=_strength, reverse=True)
    # Owner (2026-10-30): 弱い将軍をたくさん送って少しずつ削る選択肢も狭めない.
    # An egg-holder absent from the strength table is a valid last seat on an
    # attrition sortie; it only goes after every measured general and still
    # honours the site egg rule, the hero-castle guard and the unfit list.
    # A broken/depleted egg or a heavy injury keeps recovering as before —
    # a dead sortie chips nothing and can feed the enemy a defector.
    unavailable = _en_route(mem)[0] | set(_world(mem).get('general_location_unknown') or ())
    present = set(names or ()) - unavailable
    unknown = [g for g in names or ()
               if g not in weak and g != NAME and g not in unavailable
               and _holds_egg(mem, g) and not _boss_companion_unfit(mem, g)
               and (not site or (not chart_adjust.hero_guard_short(names, g, unavailable)
                                 and (NAME in present
                                      or any(_holds_egg(mem, o) for o in present - {g}))))]
    return weak + unknown


def _strength(name):
    return (reference.general_strength(name) or (0, 0))[:2]


def _boss_stats(mem):
    return (reference.general_strength(chart.BOSSES.get(mem.get('chapter')))
            or reference.general_strength('しゅじんこう'))


def _boss_comparable(mem, name):
    boss, stats = _boss_stats(mem), reference.general_strength(name)
    return (boss is not None and stats is not None
            and boss[0] - stats[0] < reference.STRONG_GENERAL_HP_GAP
            and boss[1] - stats[1] < reference.STRONG_GENERAL_COMBAT_GAP)


def _holds_egg(mem, name):
    """An egg the sortie screen showed, else the roster's own (GENERAL_EGGS);
    a rotten egg or a read count of 0 is none."""
    kind = (mem.get('egg_types') or {}).get(name)
    held = kind != 'くさってる' if kind is not None else bool((GENERAL_EGGS.get(name) or (False,))[0])
    return held and (mem.get('egg_uses') or {}).get(name) != 0


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
    garrison = _world(mem).get('garrison') or {}
    source = _source(order, mem)
    chapter = mem.get('chapter') or 0
    owned = _owned(mem) & set(chart.castles(chapter))
    if order.get('target') in chart.boss_sites(4):
        # Owner 2026-10-30: 強い将軍から. Every read list's strongest companion
        # competes, not the chart source's first fit one (g1075 08:20-08:25
        # sent each source's lone アルテミス 49/7, カペリーニ 63/7, ミュスカデ
        # 57/9). The hero's castle, where joiners stand, is read first while
        # no read list holds one as strong as the hero.
        best = {c: names[0] for c in owned
                if (names := _order_hero_alternatives(order, mem, garrison.get(c), relaxed=False))}
        if not best:
            # Strict guards that leave no sortie-able general anywhere relax
            # to keeping one present defender (owner 2026-10-30: 定常的に
            # ボス城へ進行). Still never an empty castle.
            best = {c: names[0] for c in owned
                    if (names := _order_hero_alternatives(order, mem, garrison.get(c)))}
        home = chart.home_castle(chapter)
        if (home in owned and garrison.get(home) is None
                and not any(_boss_comparable(mem, g) for g in best.values())):
            return home
        return max(best, key=lambda c: (_strength(best[c]), -_distance(chapter, c, order['target'])),
                   default=None)
    castles = [source] + sorted(owned - {source})
    return next((castle for castle in castles if castle in owned
                 and _order_hero_alternatives(order, mem, garrison.get(castle))), None)


def _hero_source_alternative(order, mem):
    return (order.get('general') == NAME
            and bool(_order_hero_alternatives(order, mem, (_world(mem).get('garrison') or {}).get(_source(order, mem)))))


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
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    current_alternative = _hero_source_alternative(order, mem)
    if gate.get('alternative') != current_alternative:
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    if gate.get('month') != mem.get('month'):
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    count = gate.get('count', 0)
    if count >= HERO_BROKEN_SORTIE_CANCEL_LIMIT:
        gates.pop(step, None)
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
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
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    garrison = (_world(mem).get('garrison') or {}).get(source)
    general_present = isinstance(garrison, list) and general in garrison
    if not general_present:
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    sortie = _sorties(mem).get(step)
    if not (isinstance(sortie, dict) and sortie.get('status') == 'cancelled'):
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    count = gate.get('count', 0)
    if count >= SORTIE_CANCELLED_OBSERVED_LIMIT:
        gates.pop(step, None)
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
        return True
    return True


def _gate_names(names):
    # The policy is JSON between observations, so a stored tuple comes back
    # as a list (g1075 01:20-01:29: A:6d0149e9:J1 was re-issued 11 times).
    return tuple(names) if isinstance(names, list) else names


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
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    if source != _source(order, mem):
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    current_present = (_world(mem).get('garrison') or {}).get(source)
    current_present_tuple = tuple(sorted(current_present)) if isinstance(current_present, list) else None
    if current_present_tuple != _gate_names(gate.get('present')):
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    busy, _ = _en_route(mem)
    unknown = set(_world(mem).get('general_location_unknown') or ())
    unavailable = busy | unknown
    current_available = None
    if isinstance(current_present, list):
        current_available = tuple(sorted(set(current_present) - unavailable))
    if current_available != _gate_names(gate.get('available')):
        gates.pop(step, None)
        if (_plan_region(mem).get('orders') or {}).get(step) == 'failed':
            _plan_region(mem).setdefault('orders', {})[step] = None
        return False
    count = gate.get('count', 0)
    if count >= SOURCE_DEFENDER_CANCEL_LIMIT:
        gates.pop(step, None)
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
        return True
    return True


def _record_source_defender_cancel(mem, order, present=None, available=None, gate=True):
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
    unknown = set(_world(mem).get('general_location_unknown') or ())
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
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
    elif not gate:
        gates.pop(step, None)     # a location recheck reopens the same list at once
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
        _plan_region(mem).setdefault('orders', {})[step] = 'failed'
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
    return _normal_missing(mem) == 0


def _normal_missing(mem):
    chapter = mem.get('chapter')
    chapter_names = chart.CASTLE_NAMES.get(chapter) if type(chapter) is int else None
    captured = mem.get('captured', [])
    if not chapter_names or not isinstance(captured, (list, tuple, set, frozenset)):
        return len(chart.CASTLE_NAMES.get(chapter) or ()) + 1
    chapter_castles = set(chapter_names) - {chart.home_castle(chapter), chart.boss_castle(chapter)}
    return len(chapter_castles - set(captured))


# Owner 2026-10-30 (全城条件は厳しいため緩和): a 四季王 site may start while
# one normal castle is missing or lost; two or more still waits for retakes.
SEASON_SITE_MISSING_LIMIT = 1


def _season_site_ready(order, mem):
    if ((mem.get('chapter') or 0) != 4 or order.get('target') not in chart.boss_sites(4)
            or chart.home_castle(4) in (mem.get('lost') or ())):
        return False
    return _normal_missing(mem) <= SEASON_SITE_MISSING_LIMIT


def _boss_path_captured(mem):
    """Every castle on the order chain toward the boss is held.

    Owner 2026-11-02 (クロシェット取り返し中でも同時にボス城に攻め込める):
    ``all_captured`` gated the boss order behind side castles that have
    nothing to do with the boss route — the assault may run in parallel
    with their retakes once every castle the chain marches through is held.
    """
    chapter = mem.get('chapter') or 0
    all_orders = chart.all_orders(chapter)
    path = {o.get('target') for o in all_orders
            if chart.is_boss_castle(o.get('target'))}
    boss_targets = set(path)
    while True:
        grow = {o.get('source') for o in all_orders
                if o.get('target') in path and o.get('source') not in path}
        if not grow:
            break
        path |= grow
    captured = set(mem.get('captured') or ()) | {chart.home_castle(chapter)}
    chain = path - boss_targets
    return bool(chain) and all(c in captured for c in chain)


def _post_conquest_wait(mem):
    """All normal castles held while the boss fight is still ahead (#2985).

    Chapter 4's 四季王 sites open while one normal castle is still missing
    (owner 2026-10-30, SEASON_SITE_MISSING_LIMIT), so the same wait window
    starts at that readiness too.
    """
    chapter = mem.get('chapter')
    if type(chapter) is not int or chapter not in chart.CASTLE_NAMES:
        return False
    if _all_normal_captured(mem):
        return True
    return chapter == 4 and _normal_missing(mem) <= SEASON_SITE_MISSING_LIMIT


def _defense_priority_castles(mem):
    """Castles whose garrison must stay strong while the boss is awaited:
    the owned home castle plus the owned castle(s) nearest the boss castle
    (the frontier a boss sortie reaches first)."""
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    boss = chart.boss_castle(chapter)
    owned = _owned(mem) & set(cells)
    if not owned:
        return set()
    home = chart.home_castle(chapter)
    if boss not in cells:
        return {home} if home in owned else set()
    nearest = min(_distance(chapter, c, boss) for c in owned)
    return {c for c in owned
            if c == home or _distance(chapter, c, boss) == nearest}


GARRISON_FRONT_TARGET = 3   # 敵城に最も近い城の目標人数
GARRISON_NEAR_TARGET = 2    # その次の輪の目標人数
GARRISON_REAR_TARGET = 1    # 敵城から遠い城の目標人数
GARRISON_REAR_FLOOR = 0     # 遠い城は0人まで送り出してよい (owner 2026-11-04)
GARRISON_REAR_SPAN = 0.5    # 最前線の1.5倍以上遠い城だけを「遠い城」とする


def _enemy_distance(mem, castle):
    """Manhattan distance from ``castle`` to the nearest castle we do not own.

    The enemy side is every castle on the map outside ``_owned`` — uncaptured
    towns and the boss castle/sites alike. ``None`` for a castle the chapter
    map does not place.
    """
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    if castle not in cells:
        return None
    owned = _owned(mem) & set(cells)
    enemies = [c for c in cells if c not in owned]
    if not enemies:
        return 0
    return min(_distance(chapter, castle, e) for e in enemies)


def _garrison_plan(mem):
    """Owner 2026-11-04: staff owned castles by distance to an enemy castle.

    A castle with few generals falls easily — its defenders step in one at a
    time — so generals are rotated toward the front instead of sitting in the
    rear. The owned castle(s) nearest an enemy castle aim for
    ``GARRISON_FRONT_TARGET`` generals, the next ring for
    ``GARRISON_NEAR_TARGET``, and everything farther holds one. Only a castle
    at least ``GARRISON_REAR_SPAN`` farther than the front ring is treated as
    the rear (``GARRISON_REAR_FLOOR``): it is the least likely to be attacked
    and may be left empty. The home castle and a castle we already lost once
    always keep one general.

    Returns ``{castle: (enemy_distance, target, floor)}`` for the owned
    castles the chapter map places; ``floor`` is the fewest generals the
    rotation leaves behind when its spare moves forward.
    """
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    owned = _owned(mem) & set(cells)
    if not owned:
        return {}
    home = chart.home_castle(chapter)
    lost = set(mem.get('lost') or ()) | set(mem.get('defense_break_blocked') or ())
    distance = {c: _enemy_distance(mem, c) for c in owned}
    rings = sorted(set(distance.values()))
    plan = {}
    for castle in owned:
        ring = rings.index(distance[castle])
        target = (GARRISON_FRONT_TARGET if ring == 0
                  else GARRISON_NEAR_TARGET if ring == 1
                  else GARRISON_REAR_TARGET)
        floor = GARRISON_REAR_TARGET
        front = rings[0]
        if (ring >= 2 and castle != home and castle not in lost
                and distance[castle] >= front * (1 + GARRISON_REAR_SPAN)):
            floor = GARRISON_REAR_FLOOR
        plan[castle] = (distance[castle], target, floor)
    return plan


def _defense_score(mem, name):
    """(has a usable egg, HP, combat); unmeasured stats fall back to (0, 0)."""
    hp, combat = (reference.general_strength(name) or (0, 0))[:2]
    return (1 if _holds_egg(mem, name) else 0, hp, combat)


def _defense_move_pick(mem, source, target, sendable):
    """Which sendable general a move order takes.

    Boss wait only (#2985): a priority castle parts with its weakest general
    so the strongest stays, and a weak priority castle is reinforced with the
    donor's strongest spare. Outside the wait the list head goes, as before.
    The sort is stable, so tied scores keep the observed order — a roster
    whose strength is unmeasured never sends someone the head pick would
    have kept. The hero is sendable only when alone.
    """
    if not sendable:
        return None
    if not _post_conquest_wait(mem):
        return sendable[0]
    priority = _defense_priority_castles(mem)
    pool = [g for g in sendable if g != NAME] or sendable
    ranked = sorted(pool, key=lambda g: _defense_score(mem, g), reverse=True)
    if source in priority:
        return ranked[-1]
    if target in priority:
        return ranked[0]
    return sendable[0]


def _boss_target_unlocked(order, mem):
    # An adjusted after=None (including normalized self-capture conditions),
    # a retry or a hotloaded active order cannot remove the game prerequisite.
    if not chart.is_boss_castle(order.get('target')):
        return True
    chapter = mem.get('chapter')
    if chapter == 9:
        # The moving boss may only be attacked at a cell measured on this
        # month's Y view; the orchestrator clears the registry when the
        # reading expires, so a stale position never unlocks the order.
        return order.get('target') == chart.boss_castle(9) and bool(chart.moving_boss_cell(9))
    if chapter == 4 and (order.get('target') not in chart.boss_sites(4)
                         or _season_site_held(order, mem)):
        return False
    if _season_site_ready(order, mem):
        return True
    # Parallel assault while side castles are retaken: the boss target is
    # unlocked once every castle on its own chain is held (owner 2026-11-02).
    return _is_boss_order(order, mem) and (_all_normal_captured(mem)
                                         or _boss_path_captured(mem))


def _season_site_held(order, mem):
    """A 四季王 site waits for every retake and is not attacked again once won.

    Owner 2026-10-30: 奪還を先 - a lost castle is taken back before the next
    site sortie starts; one already marching goes on. The same owner relaxed
    it later that day: a single lost castle no longer holds the sites (全城
    条件は厳しい), two or more still waits for retakes.
    """
    lost = mem.get('lost') or ()
    return (chart.home_castle(mem.get('chapter') or 0) in lost
            or len(lost) > SEASON_SITE_MISSING_LIMIT or _site_king_down(mem, order.get('target')))


def _ready(order, mem) -> bool:
    if (routes.is_road(mem.get('chapter')) and not chart.is_boss_castle(order.get('target'))
            and not routes.order_allowed(mem, order,
                                         _source(order, mem) if 'step' in order else order.get('source'))):
        return False
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
            and order.get('roster_probe') is not True
            and _boss_companion_source(order, mem) is None
            and (_world(mem).get('garrison') or {}).get(_source(order, mem)) is not None):
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
        if _season_site_ready(order, mem):
            return True
        # The boss assault may run while side castles are still being
        # retaken — only the castles on its own chain must be held
        # (owner 2026-11-02: クロシェット取り返し中でも並行で攻め込める).
        if chart.is_boss_castle(order.get('target')) and _boss_path_captured(mem):
            return True
        return _all_normal_captured(mem)
    return False


def _orders(mem):
    """The order list in force: an adopted adjusted plan, else the base chart.

    The adopted plan (``chart_plan``) is separate from the request state
    (``chart_adjust``): a new off-chart request never discards a plan that is
    still waiting on a capture; only adopting a newer validated plan does.
    While no plan is waiting, one JEV-chosen interim order may run.
    """
    from . import corridor
    if corridor.mechanical(mem):
        return tuple(corridor.orders(mem))   # chapter 9: nothing but the march
    plan = _plan_region(mem).get('chart_plan') or {}
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
            or (_world(mem).get('launched_orders') or {}).get(step))


def _plan_pending(mem) -> bool:
    """An adopted plan still has an order that can run, or one waiting on a
    capture that a marching unit is making.

    Orders that can never run (source lost, capture nobody is making) kept
    off-chart sorties (retake, staffing) off for an hour in g407.
    """
    status = _plan_region(mem).get('orders') or {}
    owned = _owned(mem)
    sorties = _world(mem).get('sorties') or {}
    plan = (_plan_region(mem).get('chart_plan') or {}).get('orders') or ()
    # A recent sortie, or a launched plan order with no sortie record to age.
    heading = _en_route(mem)[1] | {o['target'] for o in plan
                                   if status.get(o['step']) == 'launched' and o['step'] not in sorties}

    garrison = _world(mem).get('garrison') or {}

    def live(o):
        after = o.get('after') or ()
        if after and after[0] == 'captured' and after[1] not in owned:
            return after[1] in heading          # waits on a capture somebody is making
        # A source read empty cannot start the order (g438: dead ココット's J1).
        return (_ready(o, mem) and _source(o, mem) in owned
                and _source_list_open(o, mem)
                and not _retake_reserved(o, mem) and not _source_spare_missing(o, mem, allow_probe=True))

    return any(status.get(o['step']) in (None, 'pending') and live(o) for o in plan)


def _source_list_open(order, mem) -> bool:
    """The order's source list may be opened.

    A list last read empty is never opened (g407: the plan's どうし/ヴィーナス
    from an empty home; g438: dead ココット's J1), except by a probe order for
    a castle a transfer was sent into (chapter 3 ``routes.probe_due``): that
    list is the only receipt of the arrival. g1075 2026-10-29 11:38: the
    interim retake of リグア from グリン (recorded empty, two moves just sent
    there) was built as a probe but never started.
    """
    source = _source(order, mem)
    if (_world(mem).get('garrison') or {}).get(source) != []:
        return True
    if order.get('roster_probe') is True and source in (mem.get('roster_read_due') or {}):
        return True   # docich#3515: a blind launch asks for one roster read
    return order.get('source_probe') is True and routes.probe_due(mem, source)


def _boss_retry_due(order, mem) -> bool:
    """A boss sortie failed only by cancelled targets gets its remaining retries
    (g421 13:48: the tower-roof check cancelled どうし twice before #1251).

    The cancels belong to the boss cell they were counted at. A cell the chart
    no longer holds, or cancels recorded before cells were kept, leave the
    current cell untried (g1075 2026-10-29: four cancels at the cave)."""
    chapter = mem.get('chapter') or 0
    if ((_plan_region(mem).get('orders') or {}).get(order['step']) != 'failed'
            or not chart.is_boss_castle(order.get('target'))):
        return False
    cancels = (mem.get('target_cancel') or {}).get(order['step'], 0)
    cell = list(chart.castles(chapter).get(order['target']) or ())
    if cancels and (mem.get('boss_cancel_cell') or {}).get(order['step']) != cell:
        return True
    return 0 < cancels < BOSS_TARGET_CANCEL_LIMIT


BOSS_RELAUNCH_LIMIT = 2                     # boss sorties sent again per step after a march that never entered
BOSS_STALE_COOLDOWN = 3 * BOSS_ARRIVAL_LIMIT   # an exhausted step re-arms after a long wait
# A 四季王 step whose march record expired stays 'launched' with no record, no
# overdue path and nothing that can bind an entry, so the step never runs
# again. g1163 2026-11-09 02:5x: 4-B1/4-B2 sat launched with an empty sorties
# map while 南東 was the only live site and next_order returned None.
SITE_PHANTOM_TICKS = 900                    # observations a site step may look launched without a record
SITE_STEP_REARM_COOLDOWN = 300              # between two re-arms of the same step
# A site march starts from a corner castle and can outlive two relaunch
# cycles. Keep an unentered attempt bindable after its step is sent again, so
# a late 「ボスじょうにせめこんだ!!」 is not orphaned by the overwritten record.
SITE_LATE_ENTRY_TICKS = 8 * SORTIE_BUSY_TICKS
RETIRED_SORTIES_PER_STEP = 3


def _retire_unentered_sortie(mem, step, sortie):
    """Archive an unentered boss march before its sortie record is replaced.

    The sorties map holds one record per step. Re-sending an overdue march
    used to overwrite the old general, so his late entry matched nothing and
    the site fight never reached boss_victories (g1075: four 4-B steps cycled
    overdue for hours). Only boss targets are archived; normal castle entries
    are already bounded by the short march window.
    """
    target = sortie.get('target') or sortie.get('planned_target')
    if not chart.is_boss_castle(target) or sortie.get('status') == 'arrived':
        return
    retired = _world(mem).get('retired_sorties')
    if not isinstance(retired, dict):
        retired = _world(mem)['retired_sorties'] = {}
    rows = retired.setdefault(step, [])
    if not isinstance(rows, list):
        rows = retired[step] = []
    rows.append(deepcopy(sortie))
    del rows[:-RETIRED_SORTIES_PER_STEP]


def _site_king_down(mem, target):
    """A 四季王 site whose king's defeat is already measured.

    A measured victory is the strongest proof, but a fight that ends on the
    closing dialogue never leaves a readable 0 HP (g1163 2026-11-09: three
    sites were cleared and still attacked). The Y view's marker is the other
    measurement: the cleared site draws a pennant instead of the skull.
    """
    if (mem.get('chapter') == 4 and mem.get('boss_defeated') == 4
            and target in chart.boss_sites(4)):
        return True
    marker = (mem.get('season_sites') or {}).get(target)
    if (mem.get('chapter') == 4 and isinstance(marker, dict)
            and marker.get('state') == 'closed' and marker.get('chapter') == 4):
        return True
    return any(isinstance(victory, dict) and victory.get('site') == target
               for victory in (mem.get('boss_victories') or {}).values())


def _boss_fight_live(mem, step):
    """The boss order's entry still has an attack or battle record running."""
    return any(isinstance(record, dict) and record.get('step') == step
               for record in (_plan_region(mem).get('attack'), mem.get('battle')))


def parked_boss_steps(mem):
    """Boss steps whose march has been overdue past the stale cooldown.

    A step is parked when its order still says launched, its sortie is
    overdue (or an entered fight left no result), its bounded relaunch budget
    is spent, and the stale cooldown has elapsed. At that point only
    _relaunch_overdue_boss can restart it, so a caller stuck in a menu can
    prioritize returning to the field map.
    """
    now = int(mem.get('tick') or 0)
    orders_status = _plan_region(mem).get('orders') or {}
    relaunches = mem.get('boss_relaunch') or {}
    out = []
    for step, sortie in _sorties(mem).items():
        if not isinstance(sortie, dict):
            continue
        target = sortie.get('target') or sortie.get('planned_target')
        status = sortie.get('status')
        if (orders_status.get(step) != 'launched' or not chart.is_boss_castle(target)
                or status not in ('overdue', 'arrived')
                or (status == 'arrived' and _boss_fight_live(mem, step))
                or type(sortie.get('tick')) is not int
                or relaunches.get(step, 0) < BOSS_RELAUNCH_LIMIT
                or now - sortie['tick'] < BOSS_STALE_COOLDOWN):
            continue
        out.append(step)
    return sorted(out)


def _drop_spent_sortie_attempt(mem, step):
    """Clear a departure attempt the overdue march already spent.

    g1097 2026-11-02: チコリ's アイアイエー sortie confirmed departure at tick
    89608, the interrupted attempt was never reconciled, and the march went
    overdue at 90408. The order went back to pending and was re-picked, but
    ``execution_claimed`` still saw the attempt's ``confirmation_tick`` and
    resolved the step to ``None`` — so every castle menu was backed out of
    with a silent B and the same ``order_start`` looped for a whole month
    (launches stuck at 93). An attempt only claims its step while it carries
    a confirmation tick; an overdue sortie can never complete that attempt,
    so drop it with the rest of the launch-time state.
    """
    attempt = _flow(mem).get('sortie_attempt') or {}
    if attempt.get('step') == step and 'confirmation_tick' in attempt:
        _flow(mem).pop('sortie_attempt', None)


def _queue_overdue_location_recheck(mem, sortie):
    """An overdue sortie is not evidence that its general returned home.

    Mark only this general's cached location dirty for the next monthly
    roster-first status pass. Until their own status and castle are observed,
    neither a stale source garrison nor an expired march may make them
    eligible for another sortie.
    """
    general = sortie.get('general')
    if not isinstance(general, str) or not general:
        return
    # A post-departure camp/castle status is already direct evidence; the
    # normal observed-end path handles it instead of reopening a known row.
    if _observed_march_end(mem, sortie, sortie.get('tick')):
        return
    from ..os_layer import roster as receipts
    receipts.dirty_status(mem, [general], fields=('location',))
    unknown = _world(mem).setdefault('general_location_unknown', [])
    new = general not in unknown
    if new:
        from ..os_layer.world import mark_location_unknown
        mark_location_unknown(mem, general)
    for residents in (_world(mem).get('garrison') or {}).values():
        if isinstance(residents, list) and general in residents:
            residents.remove(general)
    if new:
        _record(mem, 'sortie_location_recheck_queued', general=general,
                reason='行軍追跡の期限超過は帰城の証拠でないため、月次の本人ステータスで所在を再確認する')


def _relaunch_overdue_boss(mem):
    """Send a boss order again when its march never entered the boss castle,
    or when its entry ended without a measured result.

    Only an entry ends a launched boss march (``_bind_sortie`` marks it
    arrived). g1075 2026-10-29: the v293 marker landed 6 px outside the boss
    building, アポロン camped beside it at 15:00, and 3-B1 stayed launched and
    en route for over 3000 observations with nothing left to start it. The
    order goes back to pending like a lost boss battle: its unlock, source
    and companion checks run again, and the old march stops counting.

    An arrived sortie is sent again too once its fight is over: g1075
    2026-10-30 had 4-B1 アルテミス enter 四季王城(南西) at 08:27 and 4-B4
    ヴィーナス enter 北西 at 08:41, and both steps stayed 'launched' past
    12:30 — their fights predate the season-king panel record, so no win or
    loss could ever reset them. A live battle for the step or a king already
    measured defeated keeps the sortie out of this rule.
    """
    now = int(mem.get('tick') or 0)
    status = _plan_region(mem).setdefault('orders', {})
    for step, sortie in _sorties(mem).items():
        # An unconfirmed launch keeps its cell in planned_target only; g1075
        # 4-B2 カペリーニ stayed en route for 3066 ticks (2026-10-30 09:28).
        target = sortie.get('target') or sortie.get('planned_target')
        if sortie.get('status') == 'overdue':
            _drop_spent_sortie_attempt(mem, step)
        if (status.get(step) != 'launched' or not chart.is_boss_castle(target)
                or type(sortie.get('tick')) is not int or now - sortie['tick'] < _arrival_limit(mem, sortie)):
            continue
        relaunches = mem.setdefault('boss_relaunch', {})
        # Owner 2026-10-30: a spent relaunch budget must not park a site
        # forever. After a very long wait past the normal limit the step
        # re-arms once - the counter still bounds rapid cycles, but a chapter
        # whose kings are unmeasured can never be abandoned mid-attrition.
        stale = (sortie.get('status') == 'overdue'
                 and relaunches.get(step, 0) >= BOSS_RELAUNCH_LIMIT)
        if (sortie.get('status') not in ('en_route', 'launched_unconfirmed')
                and not (sortie.get('status') == 'arrived'
                         and not _site_king_down(mem, target)
                         and not _boss_fight_live(mem, step))
                and not (stale and now - sortie['tick'] >= BOSS_STALE_COOLDOWN)):
            continue
        entered = sortie.get('status') == 'arrived'
        sortie['status'] = 'overdue'
        _queue_overdue_location_recheck(mem, sortie)
        _drop_spent_sortie_attempt(mem, step)
        # The cell and the measured source list go with the record: a boss march
        # that never enters is either a wrong cell or a launch that never left
        # (g1133 2026-11-05: five boss sorties recorded as launched while the
        # world map showed a single marching unit).
        observed = {'general': sortie.get('general'), 'source': sortie.get('source'),
                    'marching': now - sortie['tick'], 'relaunches': relaunches.get(step, 0),
                    'cell': list(chart.castles(mem.get('chapter') or 0).get(target) or ()),
                    'source_still_has_general': sortie.get('general') in
                    ((_world(mem).get('garrison') or {}).get(sortie.get('source')) or ())}
        # g1133 2026-11-05: an unentered boss march used to re-send the same
        # step at the same tile until the relaunch budget ran out (ブレッツェル
        # 2508, アテナ 2443, ベーグル 2451 observations at cell (818, 184))
        # while the wave that entered at 16:42 (ブリオッシュ) used candidate 2.
        # The parallel rotation only assigns new steps, so advance this step's
        # candidate whenever its march ends without an entry.
        probes = chart.BOSS_CELL_PROBES.get(mem.get('chapter') or 0) or ()
        assigned = mem.setdefault('boss_step_probe', {})
        index = int(assigned.get(step) or 0)
        if not entered and probes and index + 1 < len(probes):
            assigned[step] = index + 1
            relaunches[step] = 0
            _record(mem, 'boss_cell_probe_shift', chart_step=step, general=sortie.get('general'),
                    observed_metric={**observed, 'from': index, 'to': index + 1},
                    reason='入城しないボス行軍の出し直し先を、次の候補セルへ1タイル進める')
        if relaunches.get(step, 0) >= BOSS_RELAUNCH_LIMIT:
            if not stale:
                _record(mem, 'boss_relaunch_exhausted', chart_step=step, observed_metric=observed,
                        reason='ボス出撃の出し直しが上限に達したため保留')
                continue
            relaunches[step] = 0
            _record(mem, 'boss_relaunch_stale_retry', chart_step=step, observed_metric=observed,
                    reason='ボス出撃の出し直しが上限後も長期間進まないため、間隔を置いて出し直す')
        relaunches[step] = relaunches.get(step, 0) + 1
        for key in ('rare_card_kit', 'strong_card_kit',
                    'dropper_card_kit', 'sortie_actor_miss'):
            (mem.get(key) or {}).pop(step, None)
        for key in ('general_override', 'source_override', 'card_override'):
            (_plan_region(mem).get(key) or {}).pop(step, None)
        _plan_region(mem).setdefault('order_context', {}).pop(step, None)
        _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
        (_world(mem).get('launched_orders') or {}).pop(step, None)
        status[step] = 'pending'
        _record(mem, 'boss_sortie_overdue', chart_step=step, general=sortie.get('general'),
                observed_metric=observed, resulting_event=f'relaunch:{relaunches[step]}',
                reason=('ボス城への突入後に勝敗の実測が残らず上限を超えたため、チャートの指示から出し直す'
                        if entered else
                        'ボス城への出撃が上限の観測内に入城しないため、チャートの指示から出し直す'))


MARCH_RELAUNCH_LIMIT = 2                       # non-boss sorties sent again per step


def _redirect_blocked_sources(mem):
    """Re-point a pending order whose baked source lies behind an own castle.

    Owner 2026-11-02 (g1097): orders planned with source アルマムーン keep
    re-launching into オトラント, which absorbs every march toward
    クロチェット — the blocker is the real launch point, exactly the way a
    transit-continue re-issues an order from the castle its general stopped
    in. Interim candidates already prefer a clear source; this fixes orders
    whose source was baked before that preference (or by an older plan).
    Chapter 3 marches follow the road table, not the straight line, so the
    geometric check does not apply there.
    """
    chapter = mem.get('chapter') or 0
    if routes.is_road(chapter):
        return
    owned = _owned(mem) & set(chart.castles(chapter))
    overrides = _plan_region(mem).setdefault('source_override', {})
    status = _plan_region(mem).get('orders') or {}
    seen = set()
    for order in [*_orders(mem), *(_world(mem).get('launched_orders') or {}).values()]:
        step = order.get('step')
        if (not step or step in seen or step in overrides
                or status.get(step) != 'pending'):
            continue
        seen.add(step)
        source, target = order.get('source'), order.get('target')
        blocker = (source and target
                   and chart.march_blocker(chapter, source, target, owned))
        if blocker:
            overrides[step] = blocker
            _record(mem, 'sortie_source_march_block', chart_step=step,
                    expected_metric={'source': source, 'target': target},
                    observed_metric={'blocker': blocker},
                    reason='出撃元と目標の間に自城があり行軍がそこに吸収されるため、その城を発射元にする')


def _relaunch_overdue_march(mem):
    """Settle a non-boss march that never arrived, the way the boss rule does.

    Only an enemy-castle entry (a battle) or an own-castle roster sighting
    (transit continue) ends a recorded march. A general who died or stopped
    somewhere never observed leaves the sortie 'en_route' and the order
    'launched' forever — g1097 2026-11-02: 26 stale marches up to 40k ticks
    held a month with launches stuck at 60 while generals sat garrisoned or
    nowhere. The order goes back to pending like the boss relaunch: source,
    companion and readiness checks run again, and a missing general is simply
    not listed at the source so the launch picks somebody else. Sorties with
    no launched order (house/move) are just closed so they stop counting as
    busy marches.
    """
    now = int(mem.get('tick') or 0)
    status = _plan_region(mem).setdefault('orders', {})
    relaunches = mem.setdefault('sortie_relaunch', {})
    for step, sortie in _sorties(mem).items():
        target = sortie.get('target') or sortie.get('planned_target')
        if sortie.get('status') == 'overdue':
            # Already settled: still drop a spent attempt that claims the
            # step, or a re-picked pending order cancels in the menu forever.
            _drop_spent_sortie_attempt(mem, step)
            continue
        # A measured defeat or retreat on the road (battle_end's
        # sortie_road_defeat) ends the march as surely as the deadline does.
        road_defeat = type(sortie.get('road_defeat')) is int
        if (chart.is_boss_castle(target) or type(sortie.get('tick')) is not int
                or sortie.get('status') not in ('en_route', 'launched_unconfirmed')
                or (now - sortie['tick'] < _arrival_limit(mem, sortie) and not road_defeat)):
            continue
        en_route = sortie.get('status') == 'en_route'
        sortie['status'] = 'overdue'
        _queue_overdue_location_recheck(mem, sortie)
        _drop_spent_sortie_attempt(mem, step)
        observed = {'general': sortie.get('general'), 'source': sortie.get('source'),
                    'target': target, 'marching': now - sortie['tick'],
                    'relaunches': relaunches.get(step, 0),
                    **({'road_defeat': True} if road_defeat else {})}
        if status.get(step) != 'launched':
            _record(mem, 'sortie_march_overdue_settled', chart_step=step,
                    observed_metric=observed,
                    reason='出撃が上限の観測内に決着しないため、保留中のままの行軍を済ませたものとして片付ける')
            continue
        order = (_world(mem).get('launched_orders') or {}).get(step) or {}
        if (en_route and target in _owned(mem)
                and (sortie.get('purpose') or order.get('purpose')) == 'move'):
            # g1097 2026-11-02: a move into our own castle has no arrival
            # report and nothing reads the destination's list on a
            # schedule, so every relaunch simply drained the donor —
            # アルマムーン sent ジェラート, チコリ, ピオーネ, ピスタチオ into
            # アイアイエー one after another while the earlier movers were
            # already sitting there. Close the confirmed march's order; the
            # castle stays off the move list via routes.unread_dispatches
            # until its own general list is read. An unconfirmed launch
            # still retries through the normal relaunch budget.
            status[step] = 'failed'
            _record(mem, 'sortie_move_overdue_settled', chart_step=step,
                    general=sortie.get('general'), observed_metric=observed,
                    reason='自城への移動の入城は行先の一覧でしか観測できず、出し直しても同じ確認漏れになるため、指示を閉じて行先一覧の確認へ委ねる')
            continue
        # A march whose straight path is now covered by our own castle stops
        # inside that castle and can never reach the target: re-issuing it just
        # repeats the same stall (g1163 chapter 7 2026-11-09 15:30/16:32 sent
        # アルマムーン → ロキュータ twice after レオナール had been captured on
        # that line, and アルマムーン → フィロメラ once behind ベンゴディ).
        # Close the order so the planner sorties from the blocking castle.
        source = sortie.get('source') or order.get('source')
        chapter = mem.get('chapter') or 0
        if (isinstance(source, str) and isinstance(target, str)
                and not chart.is_boss_castle(target)
                and target not in _owned(mem)
                and not routes.is_road(chapter)
                and chart.march_blocked(chapter, source, target, set(_owned(mem)))):
            status[step] = 'failed'
            _record(mem, 'sortie_march_blocked_settled', chart_step=step,
                    general=sortie.get('general'), observed_metric=observed,
                    reason='行路が自城に塞がれて入城できないため指示を閉じ、塞ぐ城からの出撃へ委ねる')
            continue
        if relaunches.get(step, 0) >= MARCH_RELAUNCH_LIMIT:
            _record(mem, 'sortie_march_relaunch_exhausted', chart_step=step,
                    observed_metric=observed,
                    reason='出撃の出し直しが上限に達したため、以後の行軍は行先の喪失確認に委ねる')
            continue
        relaunches[step] = relaunches.get(step, 0) + 1
        for key in ('rare_card_kit',
                    'strong_card_kit', 'dropper_card_kit', 'sortie_actor_miss'):
            (mem.get(key) or {}).pop(step, None)
        for key in ('general_override', 'source_override', 'card_override'):
            (_plan_region(mem).get(key) or {}).pop(step, None)
        _plan_region(mem).setdefault('order_context', {}).pop(step, None)
        _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
        (_world(mem).get('launched_orders') or {}).pop(step, None)
        status[step] = 'pending'
        _record(mem, 'sortie_march_overdue', chart_step=step, general=sortie.get('general'),
                observed_metric=observed, resulting_event=f'relaunch:{relaunches[step]}',
                reason=('出撃した将軍が目的地に着く前の戦闘で敗北・退却したため、オーダーを未発射に戻す'
                        if road_defeat else
                        '敵城への出撃が上限の観測内に入城も経由も観測されないため、オーダーを未発射に戻す'))


SPENT_SORTIE_TICKS = SITE_LATE_ENTRY_TICKS   # keep an attempt bindable as long as a late site entry
SPENT_SORTIE_EXPIRE_LIMIT = 8                 # records forgotten per observation


def _observed_march_end(mem, sortie, tick):
    """Did the general's own status panel, read after departure, end the march?

    A sortie record is normally aged out by tick alone, but g1133 2026-11-06
    showed デュオニソス retreat from a field encounter and camp while his boss
    sortie (tick 106559) stayed 'en_route' until the 3200-tick window: the
    monthly status read キャンプしています at tick 107151 and nothing used it.
    A measured camp or castle residence after the departure tick proves the
    march is over, so the general can rejoin the wave immediately. A reading
    from before the departure, or a 'march' reading, proves nothing.
    """
    if type(tick) is not int:
        return False
    rows = (mem.get('general_statuses') or {}).get('statuses')
    row = rows.get(sortie.get('general')) if isinstance(rows, dict) else None
    if (not isinstance(row, dict) or row.get('location_observed') is not True
            or type(row.get('tick')) is not int or row['tick'] < tick):
        return False
    return row.get('location') in ('camp', 'castle')


def _refresh_live_marches(mem):
    """A status panel that still says しんぐんちゅうです keeps the march alive.

    The fixed windows (LONG_MARCH_TICKS for the bind, SPENT_SORTIE_TICKS for
    expiry) assume a march ends within them, but g1133 2026-11-06 read
    しんぐんちゅうです at ticks 111501-111545 for boss sorties launched at
    109756-110373: ナストリーニ (306 cells), ヘーゼル (428), デーメーテール
    (1015) were all still walking near the 2400-tick relaunch window, which
    would have re-sent them while they were on the road and orphaned their
    late entry. A measured march reading after departure is positive evidence
    that the march is alive, so restart its age anchor at the reading. Only an
    observed camp/castle ends it (_observed_march_end); an old reading cannot
    extend anything.
    """
    rows = (mem.get('general_statuses') or {}).get('statuses')
    if not isinstance(rows, dict):
        return
    refreshed = 0
    for sortie in _sorties(mem).values():
        if (not isinstance(sortie, dict)
                or sortie.get('status') not in ('en_route', 'launched_unconfirmed')):
            continue
        tick = sortie.get('tick')
        row = rows.get(sortie.get('general'))
        if (type(tick) is not int or not isinstance(row, dict)
                or row.get('location_observed') is not True
                or row.get('location') != 'march'
                or type(row.get('tick')) is not int or row['tick'] <= tick):
            continue
        sortie['tick'] = row['tick']
        refreshed += 1
    if refreshed:
        _record(mem, 'sortie_march_refreshed',
                observed_metric={'refreshed': refreshed,
                                 'remaining': len(_sorties(mem))},
                reason='出撃後に行軍中を実測したため行軍記録の期限を更新する')


def _expire_spent_sorties(mem):
    """Expire stale march records without fabricating a return to the source.

    An unobserved end makes the general's *location* dirty for the next
    monthly individual-status pass. Keep them unavailable until a real status
    or source roster observation establishes their whereabouts. A measured
    post-departure camp/castle status can still finish the record immediately.

    docich#3515: when that end is observed for a launch that was never
    confirmed (blind/kept boss probe), the order is reopened at once instead
    of stranding it launched with no sortie. A confirmed launch (marker or
    roster-gone) that retreated keeps the #3500 behaviour: discard and return,
    no reopen.
    """
    now = int(mem.get('tick') or 0)
    sorties = _world(mem).get('sorties') or {}
    garrison = _world(mem).get('garrison') or {}
    placed = {g for names in garrison.values() for g in (names or ())}
    # A step the current plan still offers keeps its record: the retry and
    # roster-reprobe paths bind through it (chapter 4 sites).
    planned = {o.get('step') for o in _orders(mem)}
    planned |= set((_world(mem).get('launched_orders') or {}))
    expired = 0
    ended = 0
    for step, sortie in list(sorties.items()):
        if expired + ended >= SPENT_SORTIE_EXPIRE_LIMIT:
            break
        # A march cannot still be running past the bindable window: an
        # en_route/launched_unconfirmed record that old is a zombie left by a
        # lost battle or a re-send (g1133 2026-11-05: グレナデン sat
        # launched_unconfirmed for 5115 observations and held his name out of
        # every wave). Expire it with the overdue ones.
        if (not isinstance(sortie, dict)
                or sortie.get('status') not in ('overdue', 'en_route', 'launched_unconfirmed')):
            continue
        tick = sortie.get('tick')
        # A measured camp/castle reading after departure ends the march now,
        # without waiting for the tick window (see _observed_march_end).
        measured = _observed_march_end(mem, sortie, tick)
        if not measured:
            if type(tick) is not int or now - tick < SPENT_SORTIE_TICKS:
                continue
            # A planned step keeps its record only while a late entry could still
            # bind (the chapter-4 site retry reads it). Past twice the bindable
            # window nothing can bind: g1133 2026-11-05 kept グレナデン
            # launched_unconfirmed for 9689 observations because every plan still
            # listed the step, and the release path only touches 'launched' orders.
            if step in planned and now - tick < 2 * SPENT_SORTIE_TICKS:
                continue
        if not measured:
            _queue_overdue_location_recheck(mem, sortie)
        sorties.pop(step, None)
        general, source = sortie.get('general'), sortie.get('source')
        if measured:
            # docich#3515: a camp/castle reading for a kept/blind launch
            # proves the general never left (or never confirmed). Reopen the
            # order at once so the wave re-sends this month instead of
            # waiting for _arrival_limit. No probe means the launch was
            # already confirmed: a retreat discards only.
            probe = (_flow(mem).get('launch_probe') or {}).get(step) or {}
            unconfirmed = bool(sortie.get('launch_kept') or probe.get('kept') or probe.get('blind'))
            if unconfirmed and (_plan_region(mem).get('orders') or {}).get(step) == 'launched':
                # Mirrors flows.map.LAUNCH_MISS_LIMIT; strategy must not import
                # flows (layers test), so the budget lives here.
                _MISS_LIMIT = 2
                misses = _flow(mem).setdefault('launch_miss', {})
                misses[step] = int(misses.get(step) or 0) + 1
                for key in ('rare_card_kit',
                            'strong_card_kit', 'dropper_card_kit', 'sortie_actor_miss'):
                    (mem.get(key) or {}).pop(step, None)
                for key in ('general_override', 'source_override', 'card_override'):
                    (_plan_region(mem).get(key) or {}).pop(step, None)
                _plan_region(mem).setdefault('order_context', {}).pop(step, None)
                _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
                (_world(mem).get('launched_orders') or {}).pop(step, None)
                for target, entry in list((_world(mem).get('launched') or {}).items()):
                    if isinstance(entry, dict) and entry.get('step') == step:
                        _world(mem)['launched'].pop(target, None)
                (_flow(mem).get('launch_probe') or {}).pop(step, None)
                rows = (mem.get('general_statuses') or {}).get('statuses') or {}
                row = rows.get(general) if isinstance(rows, dict) else None
                observed = {'general': general, 'source': source, 'target': sortie.get('target'),
                            'location': row.get('location') if isinstance(row, dict) else None,
                            'status_tick': row.get('tick') if isinstance(row, dict) else None,
                            'misses': misses[step]}
                if misses[step] >= _MISS_LIMIT:
                    _plan_region(mem).setdefault('orders', {})[step] = 'failed'
                    _record(mem, 'sortie_launch_failed', chart_step=step, general=general,
                            deviation_reason='出撃確定後に部隊が現れない',
                            observed_metric=observed,
                            reason='野営・在城を実測したが出撃が未発射のため再試行の上限に達し指示を失敗にする')
                else:
                    _plan_region(mem).setdefault('orders', {})[step] = 'pending'
                    _record(mem, 'sortie_launch_unverified', chart_step=step, general=general,
                            observed_metric=observed, resulting_event='order_reopened',
                            reason='出撃後の野営・在城を実測し出撃が成立していないため出撃を未発射に戻す')
        if (general and general != NAME and general not in placed
                and source in chart.castles(mem.get('chapter') or 0)
                and measured):
            # Retain the previously established observed-end behavior; a
            # timeout alone must never manufacture a source resident.
            if not isinstance(garrison.get(source), list):
                garrison[source] = []
            if general not in garrison[source]:
                garrison[source].append(general)
            placed.add(general)
        if measured:
            ended += 1
        else:
            expired += 1
    if expired:
        _record(mem, 'sortie_record_expired',
                observed_metric={'expired': expired, 'remaining': len(sorties)},
                reason='期限超過の行軍記録を破棄し、所在未確認の将軍は月次確認の対象に残す')
    if ended:
        _record(mem, 'sortie_record_observed_ended',
                observed_metric={'ended': ended, 'remaining': len(sorties)},
                reason='野営・在城を実測したため行軍の終了とみなし、将軍を出撃元の名簿へ戻す')


def _rearm_phantom_site_steps(mem):
    """Re-open a chapter 4 site step whose march record vanished.

    Clearing only the bookkeeping is safe: a site the Y view measured closed is
    still blocked by ``_site_king_down``, and a live record, live fight or fresh
    step is left alone. The bounded cooldown keeps a step from being re-armed
    on every observation.
    """
    if (mem.get('chapter') or 0) != 4:
        return
    status = _plan_region(mem).get('orders') or {}
    sorties = _world(mem).get('sorties') or {}
    now = int(mem.get('tick') or 0)
    started = mem.get('order_start_tick') or {}
    rearmed = mem.setdefault('site_step_rearm', {})
    for order in chart.orders(4):
        step, target = order.get('step'), order.get('target')
        if (status.get(step) != 'launched' or target not in chart.boss_sites(4)
                or step in sorties or _boss_fight_live(mem, step)
                or _site_king_down(mem, target)):
            continue
        tick = started.get(step)
        if type(tick) is not int or now - tick < SITE_PHANTOM_TICKS:
            continue
        last = rearmed.get(step)
        if type(last) is int and now - last < SITE_STEP_REARM_COOLDOWN:
            continue
        rearmed[step] = now
        status[step] = 'pending'
        _record(mem, 'site_step_rearmed', chart_step=step, general=order.get('general'),
                observed_metric={'site': target, 'launched_for': now - tick,
                                 'sorties': sorted(sorties)},
                resulting_event=f'rearm:{step}',
                reason='撃破実測のない四季王stepの行軍記録が消えていたため、次の出撃のために開き直す')


BOSS_PHANTOM_TICKS = 2 * BOSS_ARRIVAL_LIMIT   # grace before a launch-less boss step re-arms
BOSS_STEP_REARM_COOLDOWN = 300


def _rearm_phantom_boss_steps(mem):
    """Re-open a boss step whose march record vanished.

    A boss order marked launched with no sortie record can never finish: the
    overdue relaunch and the expiry both work on a sortie, so the step keeps
    the boss target reserved forever (g1163 chapter 6 2026-11-09 12:47:
    A:bf8c3dee:M256898 stayed launched with no sortie while every other boss
    wave waited behind it). Clearing only the bookkeeping is safe: a live
    sortie, a live fight or a fresh step is left alone, and the same bounded
    cooldown keeps a step from re-arming on every observation.
    """
    if (mem.get('chapter') or 0) == 4:
        return          # chapter 4's sites have their own re-arm and king rules
    status = _plan_region(mem).get('orders') or {}
    sorties = _world(mem).get('sorties') or {}
    now = int(mem.get('tick') or 0)
    started = mem.get('order_start_tick') or {}
    rearmed = mem.setdefault('boss_step_rearm', {})
    for order in _orders(mem):
        step, target = order.get('step'), order.get('target')
        if (status.get(step) != 'launched' or not chart.is_boss_castle(target)
                or step in sorties or _boss_fight_live(mem, step)):
            continue
        tick = started.get(step)
        if type(tick) is not int or now - tick < BOSS_PHANTOM_TICKS:
            continue
        last = rearmed.get(step)
        if type(last) is int and now - last < BOSS_STEP_REARM_COOLDOWN:
            continue
        rearmed[step] = now
        status[step] = 'pending'
        _record(mem, 'boss_step_rearmed', chart_step=step, general=order.get('general'),
                observed_metric={'target': target, 'launched_for': now - tick,
                                 'sorties': sorted(sorties)},
                resulting_event=f'rearm:{step}',
                reason='行軍記録が消えたボスstepは完了できないため、次の出撃のために開き直す')


def next_order(mem):
    # A measured しんぐんちゅうです reading restarts the age of a live march
    # before the fixed windows can relaunch it while it is still walking.
    _refresh_live_marches(mem)
    _rearm_phantom_site_steps(mem)
    _rearm_phantom_boss_steps(mem)
    _relaunch_overdue_boss(mem)
    _relaunch_overdue_march(mem)
    _expire_spent_sorties(mem)
    _redirect_blocked_sources(mem)
    status = _plan_region(mem).setdefault('orders', {})
    from . import corridor
    if corridor.mechanical(mem):
        # Chapter 9 (owner 2026-11-11): every castle-scoped march order is
        # startable as soon as its castle is ours; no reservation, in-flight
        # cap, garrison or spare check applies.
        owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
        return next((o for o in corridor.orders(mem)
                     if status.get(o['step']) in (None, 'pending') and _source(o, mem) in owned
                     and _ready(o, mem)), None)
    current = list(_orders(mem))
    steps = {o['step'] for o in current}
    # A launched order put back to pending by a lost battle keeps its retry
    # even if a newer plan replaced the one it came from.
    retries = [o for step, o in (_world(mem).get('launched_orders') or {}).items()
               if step not in steps and status.get(step) == 'pending']
    owned = _owned(mem) & set(chart.castles(mem.get('chapter') or 0))
    # A lost attack's retry is pending until it runs again; another order may
    # take the castle first. g1075 2026-10-29: レモン took リグア at 13:22, yet
    # ポセイドン's I:b59ab6:3 retry (lost 13:07) sent ジェラート into our own
    # リグア at 13:27, ahead of the boss sortie 3-B1 (started 13:30). Two older
    # retakes of our own ドンダム were still pending. A retry whose target is
    # ours has nothing left to win; moves aim at our own castles by design.
    retired = mem.setdefault('retries_retired', [])
    stale = [o for o in retries if o.get('target') in owned and o.get('purpose') != 'move']
    for order in stale:
        if order['step'] not in retired:
            retired.append(order['step'])
            _record(mem, 'order_retry_retired', chart_step=order['step'],
                    general=order.get('general'), target=order.get('target'),
                    reason='再試行の目標城は既に自軍のため再出撃せず、次の指示へ進む')
    retries = [o for o in retries if o not in stale]
    garrison = _world(mem).get('garrison') or {}
    # The base chart's remaining orders follow an adopted plan that cannot run
    # (g438 03:48: the plan's only order sent the dead ココット from an empty
    # ジョンリギ, so the bot idled with every castle but the boss's taken while
    # the base 1-B1 boss sortie was never looked at).
    base = [o for o in chart.orders(mem.get('chapter') or 0)
            if o['step'] not in steps and o['target'] not in owned] if _plan_region(mem).get('chart_plan') else []
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
                # the order (g407: the plan's どうし/ヴィーナス from an empty home),
                # unless a transfer into it is due to be read (probe).
                and _source_list_open(order, mem)
                and not _last_castle_held(mem, order)      # its castle must keep its last general
                and not _retake_reserved(order, mem)
                and not _source_spare_missing(order, mem, allow_probe=True)
                and (_plan_region(mem).get('general_override', {}).get(order['step'], order['general']) not in _en_route(mem)[0]
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
            _plan_region(mem).setdefault('source_override', {})[order['step']] = where
            _record(mem, 'boss_companion_source', chart_step=order['step'],
                    observed_metric={'source': source, 'companion_at': where},
                    reason='主人公を出撃させず、十分な能力の代役が記録された城の実一覧を確認する')
        return
    if general == NAME and _order_hero_alternatives(order, mem, garrison.get(source)):
        return  # inspect the chart source's companion rather than chase the hero
    if not general or general in (garrison.get(source) or ()):
        return
    where = next((c for c, names in garrison.items() if general in (names or ()) and c in owned), None)
    if where and where != source and not _plan_region(mem).get('source_override', {}).get(order['step']):
        _plan_region(mem).setdefault('source_override', {})[order['step']] = where
        _record(mem, 'order_source_changed', chart_step=order['step'], strategy_variant='follow_general',
                observed_metric={'source': source, 'general_at': where},
                reason=f'{general}は{where}にいると記録されているため、そこから出撃させる')


def _order(mem):
    # A mechanical chart is a stand-in while the LLM answer is pending; once a
    # normal chart order becomes ready, discard it so the base chart resumes.
    if _plan_region(mem).get('active') is None:
        plan = _plan_region(mem).get('chart_plan') or {}
        if plan.get('source') == 'mechanical':
            chapter = mem.get('chapter') or 0
            try:
                if any(_ready(o, mem) for o in chart.orders(chapter)):
                    _plan_region(mem)['chart_plan'] = None
            except Exception:
                pass
    return _order_for_step(mem, _plan_region(mem).get('active'))


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
                   list(((_plan_region(mem).get('order_context') or {}).get(step) or {}).get('observed_metric', {}).get('cards') or []))
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
        elif _is_boss_order(order, mem) and 'バルムンク' in carried:
            # owner 2026-11-04: ボスへ効く切り札(バルムンクはボス250)は積極的に使う。
            # 第2話は実測済みの敵名つき戦術、他の章は敵名を問わずボス突入の実測でのみ開く。
            used = ((mem.get('kit_spent') or {}).get(step) or []).count('バルムンク')
            chapter = mem.get('chapter')
            boss_name = chart.BOSSES.get(chapter) if chapter != 4 else None  # 四季王は敵名が季節で変わる
            rare = tuple({'enemy': boss_name, 'card': 'バルムンク',
                          'open': True, 'step': step, 'boss_only': True,
                          'tactic_id': f'rare:{step}:バルムンク:{used + index}',
                          'note': ('実携行を確認したイベント札を実測第2話ボスへ使用' if chapter == 2 else
                                   '実携行を確認したバルムンク(ボス250)をボスへ開幕で使用')}
                         for index in range(carried.count('バルムンク')))
    if order is None or any(o['step'] == step for o in chart.orders(mem.get('chapter') or 0)):
        return (*rare, *base)
    override = (set(roles['override']) if roles is not None else
                set() if rare_kit is not None else set((_plan_region(mem).get('card_override') or {}).get(step) or ()))
    if carried is None:
        carried = _deploy_cards(order, mem)
    carried = [card for card in carried
               if card not in override and card != strong_kit
               and not (rare and card in ('キャトルミュー', 'バルムンク'))]
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
                            # ボス専用札はボス注文でなくても開かない。通常戦の
                            # 携行分を「未使用の計画札」として予定に数えない。
                            'boss_only': (_is_boss_order(order, mem)
                                          or card in reference.BOSS_ONLY_CARDS),
                            'note': '調整チャート既定: 検証済み戦術のない携行切り札を開幕使用'})
    return (*rare, *base, *derived)


def _distance(chapter, a, b) -> int:
    # A single-road chapter measures in road hops: straight-line closeness
    # means nothing across impassable terrain (ナッテ sits beside アルマムーン
    # in a straight line and is the 7th castle on chapter 9's road).
    hops = chart.corridor_gap(chapter, a, b)
    if hops is not None:
        return hops * chart.CORRIDOR_STEP
    castles = chart.castles(chapter)
    (ax, ay), (bx, by) = castles[a], castles[b]
    return abs(ax - bx) + abs(ay - by)


def _sortie_reserved_target(mem, step, sortie):
    if sortie.get('target'):
        return sortie['target']
    if sortie.get('status') == 'launched_unconfirmed':
        # Reserve the intended destination without claiming observed arrival.
        # An interrupted target selection must not dispatch reinforcements.
        return ((_world(mem).get('launched_orders') or {}).get(step) or {}).get('target')
    return None


def _reserved_targets(mem):
    now = int(mem.get('tick') or 0)
    return {_sortie_reserved_target(mem, step, s)
            for step, s in _sorties(mem).items()
            if s.get('status') in ('en_route', 'launched_unconfirmed')
            and s.get('tick') is not None
            and 0 <= now - int(s['tick']) < max(SORTIE_BUSY_TICKS, _arrival_limit(mem, s))} - {None}


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
            and interim.get('general') in ((_world(mem).get('garrison') or {}).get(source) or ())
            and interim.get('general') not in (_en_route(mem)[0]
                                              | set(_world(mem).get('general_location_unknown') or ()))
            and source not in (mem.get('lost') or ())
            and source not in (mem.get('defense_break_blocked') or ())
            and (mem.get('defense_break_sources') or {}).get(source) == interim.get('target'))


def _reserve_source_guard(order, mem):
    if chart.guard_free(mem.get('chapter')):
        return False        # chapter 9 (owner 2026-11-09): a castle may send its last general
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
            or (_plan_region(mem).get('orders') or {}).get(order['step']) == 'launched_unconfirmed'):
        return False
    # Chapter 9's next road castle takes waves (v660, owner 2026-11-11: ひとり
    # づつ出撃していたら間に合わない). The planner already keeps up to
    # SQUAD expeditions at it (#3944: the wave cap is the planner's budget,
    # counted the same way on both sides); this gate held every one of
    # them behind the first marching retake. g1163 14:12:19-14:14:41 sat on
    # the map with seven retakes of タオ planned and none started while
    # シナモン's retake was still walking.
    from .replan import _corridor_wave_open
    # Only corridor-issued orders may bypass the ordinary one-retake gate.
    # A plain retake at the same chapter-9 target must remain serialized.
    if order.get('corridor') is True and _corridor_wave_open(mem, order.get('target')):
        return False
    if order.get('corridor') is True:
        # Chapter 9's mechanical march caps its own vanguard at six marching
        # (corridor.SQUAD, v671). This gate's cap of three held the 4th-6th:
        # g1163 2026-11-11 20:16-20:37, サヌト>オシロガ was started and
        # cancelled every three seconds (167 holds, step ids up to :215).
        return False
    now = int(mem.get('tick') or 0)
    return any(step != order['step'] and _sortie_reserved_target(mem, step, s) == order.get('target')
               and s.get('status') in ('en_route', 'launched_unconfirmed')
               and s.get('tick') is not None
               and 0 <= now - int(s['tick']) < max(SORTIE_BUSY_TICKS, _arrival_limit(mem, s))
               for step, s in _sorties(mem).items())


def _unplaced_castle_generals(mem):
    """Generals read in a castle this month whom no garrison list holds.

    g684 2-5 13:45-14:18: アマンディーヌ and カシュー had joined (status:
    location castle) but no castle list had been reopened, so every garrison
    read one general, every sortie lacked a spare and nothing launched for
    30 minutes (owner 2026-10-11: しょうぐんがいるのに新規出撃をしてない).
    """
    month = mem.get('month')
    placed = {g for gs in (_world(mem).get('garrison') or {}).values() for g in gs or ()}
    busy = _en_route(mem)[0] | set(_world(mem).get('general_location_unknown') or ())
    names = {name for name, info in (mem.get('house_eggs') or {}).items()
             if isinstance(info, dict) and info.get('location') == 'castle' and info.get('month') == month}
    # g1075 2026-10-30 11:48 (#3077): chapter 4 recorded no house statuses,
    # while this month's roster named six generals no read castle list held.
    # An unread list is opened by its own probe, so the roster counts only
    # once every owned castle's list has been read.
    roster = mem.get('recruit_roster') or {}
    garrison = _world(mem).get('garrison') or {}
    if (roster.get('chapter') == mem.get('chapter') and roster.get('month') == month
            and all(garrison.get(c) is not None for c in _owned(mem))):
        names |= {name for name in roster.get('names') or () if name != NAME}
    return sorted(name for name in names if name not in placed and name not in busy)


def _arrival_castle(mem, owned):
    """Where joiners appear: the hero's castle (home if his list is unread).

    None once that castle's real list refuted the current unplaced set.
    """
    garrison = _world(mem).get('garrison') or {}
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
    present = set((_world(mem).get('garrison') or {}).get(source) or ()) - _en_route(mem)[0]
    unknown = present & set(_world(mem).get('general_location_unknown') or ())
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
    if (_plan_region(mem).get('orders') or {}).get(order['step']) == 'launched_unconfirmed':
        return False  # reconcile an issued sortie; do not cancel its carried-kit receipt
    if order.get('roster_probe') is True:
        return False  # read-only: opening the list dispatches nobody
    source = _source(order, mem)
    present = (_world(mem).get('garrison') or {}).get(source)
    # A genuine arrival or newly joined general may have changed the list
    # since it was measured. Preserve those evidence-gathering reads; a
    # recorded singleton elsewhere must not launch a doomed sortie.
    if _reserve_source_guard(order, mem) and _source_location_recheck(mem, source) is not None:
        return not (allow_probe and _source_location_recheck_due(mem, source))
    if (allow_probe and order.get('source_probe') is True
            and (present is None or routes.probe_due(mem, source))):
        return False  # an unread/arriving roster needs its own receipt
    unavailable = _en_route(mem)[0] | set(_world(mem).get('general_location_unknown') or ())
    if (present is not None and _unplaced_castle_generals(mem)
            and source == _arrival_castle(mem, _owned(mem))):
        return False   # fresh joiners are checked by _keep_sortie_defender
    if (_plan_region(mem).get('active') != order.get('step')
            and not chart.guard_free(mem.get('chapter'))
            and isinstance(present, (list, tuple)) and present
            and len(set(present) - unavailable) < 2):
        return True  # pre-flight only: never reopen a plan for the last defender
    # The hero's castle must keep HERO_CASTLE_GUARD generals beside the hero.
    # g1163 2026-11-09 04:39: a plan order sent a general from アルマムーン
    # (hero + 2), the real list cancelled it (sortie_held_hero_guard) and the
    # bot idled for the next chart. Decide it before opening the castle menu.
    if (_plan_region(mem).get('active') != order.get('step')
            and len(_owned(mem)) > 1
            and order.get('source_probe') is not True
            and str(order.get('step') or '').startswith(
                (chart_adjust.INTERIM_PREFIX, chart_adjust.PLAN_PREFIX))
            and source == chart.home_castle(mem.get('chapter') or 0)
            and isinstance(present, (list, tuple)) and present
            and chart_adjust.hero_guard_short(set(present) - unavailable,
                                              order.get('general'))):
        return True
    return (_reserve_source_guard(order, mem) and present is not None
            and len(set(present) - unavailable) < 2)


def _finish_order(mem, state, **fields):
    step = _plan_region(mem).get('active')
    if step:
        _plan_region(mem).setdefault('orders', {})[step] = state
        if state == 'launched':
            # A new sortie carries a fresh kit: attempted uses from the
            # previous one must not shrink it (``kit_spent`` is per sortie).
            (mem.get('kit_spent') or {}).pop(step, None)
            (mem.get('sortie_confirm_miss') or {}).pop(step, None)
        _record(mem, 'order_' + state, chart_step=step, **fields)
        if state == 'failed':
            # Remember the content name: a rebuilt plan gives the same content a
            # new execution id, so it would be re-issued at once (g1133
            # 2026-11-05: the boss order failed 96 times in ten minutes because
            # no castle menu opened at either source).
            mem.setdefault('failed_local_step', {})[step.rsplit(':', 1)[-1]] = int(mem.get('tick') or 0)
            from . import goals
            order = _order_for_step(mem, step) or {}
            goal_kind = {'retake': 'retake', 'move': 'move'}.get(order.get('purpose'))
            if goal_kind:
                goals.note_attempt(mem, goal_kind, 'failed', target=order.get('target'))
    _plan_region(mem)['active'] = None
    mem['picked'] = []


SOURCE_MISS_LIMIT = 3         # failed castle menus per order before giving the source up


def _source(order, mem):
    bound = bound_fields(order, mem)
    if order.get('_execution_bound') is True:
        return bound['source'] if bound is not None else None
    override = _plan_region(mem).get('source_override') or {}
    step = order.get('step')
    if step in override:
        return override[step]
    # A rebuilt plan gives the same content a new execution id (“A:<rid>:M1”),
    # so a fallback decided for the content must survive that: g1133 2026-11-05
    # fell back to the home castle 96 times in ten minutes because each rebuild
    # forgot the decision and aimed at the unopenable castle again.
    local = step.rsplit(':', 1)[-1] if isinstance(step, str) else None
    return override.get(local, order['source'])


def _give_up_source(mem, order):
    """Bound A on a castle that never opens its menu (g401: 900 presses).

    The chart's own source falls back to the home castle once, as for an
    empty general list; after that the order fails with evidence so the next
    order (or an off-chart sortie) runs instead of a permanent loop.
    """
    step = order['step']
    source = _source(order, mem)
    if (_plan_region(mem).get('orders') or {}).get(step) == 'launched_unconfirmed':
        _world(mem).setdefault('sorties', {})[step]['verification_unavailable'] = True
        _plan_region(mem)['active'] = None
        _record(mem, 'sortie_verification_held', chart_step=step,
                reason='出撃元の一覧を開けないため未確定の出撃を維持し再指示を抑止')
        return
    home = chart.home_castle(mem.get('chapter') or 0)
    # Navigation state is left to the nav_reset of this same miss: dropping
    # the cell would freeze the camera on open sea again (g358).
    # Reset by assignment, not by pop: g1133 2026-11-05 kept source_miss at 5
    # for an hour (the pop never reached the persisted state, while plain
    # assignments such as orders[...] did), so the give-up fired on every
    # observation and the order could never even start walking (issue #3417).
    miss_map = mem.setdefault('source_miss', {})
    misses = miss_map.get(step, 0)
    miss_map[step] = 0
    garrison = _world(mem).get('garrison') or {}
    if source != home and home in _owned(mem):
        # The home castle may keep only the hero, so the fallback source cannot
        # send anyone: g1133 2026-11-05 aimed at the home castle on every
        # observation while no sortie could ever leave it. Fail the order so
        # the plan offers another (a castle that still has a spare).
        if _last_castle_held(mem, {**order, 'source': home}):
            _finish_order(mem, 'failed', deviation_reason=f'{source}で決定しても城メニューが{misses}回開かず、本城も出せない',
                          observed_metric={'source': source, 'menu_miss': misses, 'home': home},
                          reason='出撃元の城を選べず、本城も将軍1人のため出撃できないので指示を諦める')
            return
    if source != home and home in _owned(mem) and not _plan_region(mem).get('source_override', {}).get(step):
        _plan_region(mem).setdefault('source_override', {})[step] = home
        _plan_region(mem)['source_override'].setdefault(step.rsplit(':', 1)[-1], home)
        garrison.pop(source, None)
        # A counter, not a decision record: the caller that logs this keeps
        # source_miss and the override out of the persisted state (g1133
        # 2026-11-05 probe: override null and source_miss 5 on all 224 calls),
        # so it fired on every observation - 94 records in five minutes.
        _tally(mem, 'source_fallback')
        return
    _finish_order(mem, 'failed', deviation_reason=f'{source}で決定しても城メニューが{misses}回開かない',
                  observed_metric={'source': source, 'menu_miss': misses},
                  reason='出撃元の城を選べないため指示を諦めて次の指示へ進む')


def _near_goal(mem, order, name, mode):
    near = mem.get('near_goal') or {}
    return near.get('step') == order['step'] and near.get('goal') == name and near.get('mode') == mode


def _recall_goal(mem, flags=None):
    """Nearest owned castle to the recalled unit's own position.

    The bound ``origin`` cell wins first; without one, each recalled sortie's
    recorded departure castle serves as the unit's position proxy so the goal
    follows the recalling unit instead of fixing on the hero's garrison
    (#2907). Only when neither is known does the hero's castle apply, then
    the sole owned castle. ``hero_castle`` keeps the distance tie-break.
    """
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    owned = sorted(c for c in _owned(mem) if c in cells
                   and (flags is None or flags.get(c) == 'own'))
    state = _flow(mem).get('recall') or {}
    hero = [c for c in owned if NAME in ((_world(mem).get('garrison') or {}).get(c) or ())]
    hero_castle = (hero[0] if len(hero) == 1 and NAME not in _en_route(mem)[0]
                   and NAME not in (_world(mem).get('general_location_unknown') or ()) else None)
    origin = state.get('origin')
    if (isinstance(origin, (list, tuple)) and len(origin) == 2
            and all(type(value) is int for value in origin)):
        # Units march directly; axis-summed distance can rank a diagonal
        # nearer castle behind a farther one. Squaring avoids rounding ties.
        return min(owned, key=lambda c: (sum((cells[c][a] - origin[a]) ** 2 for a in (0, 1)),
                                         c != hero_castle, c)) if owned else None
    # origin が束縛できない場合は、帰還する本人の出撃元を代理の現在地として
    # 距離判定へ使う（issue #2907 の方向 b）。出撃元が自軍城でなくなって
    # いても座標は有効なので、そこへ最も近い自軍城を選べる。
    sources = [cells[s] for s in
               ((_sorties(mem).get(step) or {}).get('source')
                 for step in (state.get('sorties') or ()))
               if s in cells]
    if sources:
        # 複数の出撃元を持つ場合は合計距離が最小の城（重心に近い）を選ぶ。
        return min(owned, key=lambda c: (sum(sum((cells[c][a] - o[a]) ** 2 for a in (0, 1))
                                             for o in sources),
                                         c != hero_castle, c)) if owned else None
    if hero_castle:
        return hero_castle
    return owned[0] if len(owned) == 1 else None
def _fort_recall_goal(mem, state):
    """The nearest own castle to the 砦, by whichever position is measured.

    Owner rule (2026-11-04): a 砦's general leaves for the nearest own castle,
    not for the hero's castle. _recall_goal ends on the hero's castle when it
    cannot bind a position, so a 砦 must not consult it first — g1097
    2026-11-02 17:25-17:33 (ヘパイストス, HP19/49, in a 砦 with キール) opened the
    picker with no own castle in view and got アルマムーン for that reason.

    Position, in order: the measured camp cell (origin); the latest departure
    of a general seen in this 砦 — the fort holds them together, so a mate's
    record locates it as well as his own; the home castle only when neither
    exists. Any own castle supplies him.
    """
    chapter = mem.get('chapter') or 0
    cells = chart.castles(chapter)
    owned = sorted(c for c in _owned(mem) if c in cells)
    if not owned:
        return None

    def nearest(x, y):
        return min(owned, key=lambda c: ((cells[c][0] - x) ** 2 + (cells[c][1] - y) ** 2, c))

    origin = state.get('origin')
    if (isinstance(origin, (list, tuple)) and len(origin) == 2
            and all(type(value) is int for value in origin)):
        return nearest(*origin)
    general = state.get('general')
    members = [name for name in (state.get('fort_members') or ()) if isinstance(name, str)]
    if isinstance(general, str) and general not in members:
        members.append(general)
    sorties = [s for s in _sorties(mem).values()
               if isinstance(s, dict) and s.get('general') in members
               and s.get('source') in cells and type(s.get('tick')) is int]
    if sorties:
        return nearest(*cells[max(sorties, key=lambda s: s['tick'])['source']])
    home = chart.home_castle(chapter)
    return home if home in owned else owned[0]


BOSS_TARGET_CANCEL_LIMIT = 4   # the boss sortie is worth more retries


def _garrison_move(mem, general, source=None, target=None):
    """A general left ``source`` and/or now holds ``target`` (known lists only)."""
    from ..os_layer.roster import dirty_status
    dirty_status(mem, [general] if general else None, fields=('hp', 'location'))
    garrison = _world(mem).setdefault('garrison', {})
    # Movement-derived roster changes are not new observations.
    observed = _world(mem).setdefault('garrison_observed_at', {})
    observed.pop(source, None)
    observed.pop(target, None)
    if source and garrison.get(source) is not None:
        garrison[source] = [g for g in garrison[source] if g != general]
    if target and general:
        _world(mem)['general_location_unknown'] = [g for g in _world(mem).get('general_location_unknown') or () if g != general]
        here = [g for g in garrison.get(target) or () if g != general]
        garrison[target] = [*here, general]


LAST_CASTLE_HOLD_TICKS = 300   # observations before the last castle's list is read again


def _staffed_elsewhere(mem, source) -> bool:
    """Another castle of ours is known to hold a general who is not marching.

    A castle whose list was never read (or was cleared by a recruit) is not
    counted: the guard below errs towards keeping someone home.
    """
    busy, _ = _en_route(mem)
    garrison = _world(mem).get('garrison') or {}
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
    sorties = _world(mem).get('sorties') or {}
    retired = _world(mem).get('retired_sorties')
    retired = retired if isinstance(retired, dict) else {}
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
    launched = (_world(mem).get('launched') or {}).get(castle) or {}
    step = _long_march(sorties, castle, general, now, retired)
    if step is not None:
        return step, 'matched'
    if launched.get('general') == general and launched.get('step') not in sorties:
        return launched.get('step'), 'legacy'
    return None, 'none'


def _arrival_limit(mem, sortie):
    """Match the OS-layer busy-general deadline to the strategy's retry bound."""
    return march_arrival_limit(mem, sortie)


def _long_march(sorties, castle, general, now, retired=None):
    """A march longer than SORTIE_BUSY_TICKS: the general's newest sortie.

    g1075 2026-10-29 12:45: ポセイドン took 677 observations from グリン to リグア.
    The entry fell outside the bound, so the battle had no step: it ran as
    side 'enemy' without the carried egg-drop cards, ヴィーナス summoned and
    ポセイドン retreated at 43/85 HP. A general is one unit, so only his newest
    sortie can still be marching; older unread records never compete here.
    A sortie whose target was left unconfirmed counts by its planned target.
    A retired boss attempt stays here for the longer site window, so the
    general who enters after the step was sent again still matches its site.
    """
    mine = [(key, other) for key, other in sorties.items()
            if isinstance(other, dict) and other.get('general') == general]
    for key, rows in (retired or {}).items():
        mine.extend((key, other) for other in rows or ()
                    if isinstance(other, dict) and other.get('general') == general)
    if not mine or any(type(other.get('tick')) is not int for _, other in mine):
        return None
    newest = max(other['tick'] for _, other in mine)
    latest = [(key, other) for key, other in mine if other['tick'] == newest]
    if len(latest) != 1:
        return None
    step, sortie = latest[0]
    target = sortie.get('target')
    boss = chart.is_boss_castle(castle)
    site = castle in chart.boss_sites(4)
    statuses = ('en_route', 'launched_unconfirmed', 'overdue') if boss else ('en_route', 'launched_unconfirmed')
    limit = SITE_LATE_ENTRY_TICKS if site else LONG_MARCH_TICKS
    if (sortie.get('status') not in statuses
            or not (target == castle or (target is None and sortie.get('planned_target') == castle))
            or not 0 <= now - newest < limit):
        return None
    return step


def _promote_retired_sortie(mem, step, castle, general):
    """Make a retired attempt the step's current record before binding it.

    The newest launch may still be marching while an older shot at the same
    site enters. Promoting the retired record keeps the arriving general, his
    source and his site together; the replaced attempt stays retired so its
    own late entry can still bind.
    """
    retired = _world(mem).get('retired_sorties')
    rows = retired.get(step) if isinstance(retired, dict) else None
    if not isinstance(rows, list):
        return None
    for index, old in enumerate(rows):
        if not isinstance(old, dict) or old.get('general') != general:
            continue
        target = old.get('target') or old.get('planned_target')
        if target not in (None, castle):
            continue
        current = (_world(mem).setdefault('sorties', {})).pop(step, None)
        rows.pop(index)
        if isinstance(current, dict):
            rows.append(current)
            del rows[:-RETIRED_SORTIES_PER_STEP]
        _world(mem).setdefault('sorties', {})[step] = old
        return old
    return None


def _bind_sortie(mem, step, castle, general=None):
    sortie = _sorties(mem).get(step)
    if general and (sortie is None or sortie.get('general') != general):
        sortie = _promote_retired_sortie(mem, step, castle, general) or sortie
    if sortie:
        if sortie.get('target') is None:
            _record(mem, 'sortie_arrival_confirmed', chart_step=step, general=sortie['general'],
                    castle=castle, reason='将軍名の一致する入城表示で未確定だった出撃と行先を確定')
            _garrison_move(mem, sortie['general'], source=sortie.get('source'))
            _plan_region(mem).setdefault('orders', {})[step] = 'launched'
            if _plan_region(mem).get('active') == step:
                _plan_region(mem)['active'] = None
            if (_flow(mem).get('sortie_attempt') or {}).get('step') == step:
                _flow(mem).pop('sortie_attempt', None)
        sortie.update(status='arrived', target=castle)
        if chart.is_boss_castle(castle):
            # A measured boss entry re-arms the relaunch budget: the cap
            # counts cycles that never produced an entry, not attempts that
            # actually reached the castle and fought.
            (mem.get('boss_relaunch') or {}).pop(step, None)


def _transit_sorties(mem, castle, names):
    """In-flight sorties whose general is listed inside ``castle`` right now.

    The game stops a marching general inside any of our own castles on the
    route and announces nothing (owner 2026-10-14: only an enemy entry or an
    enemy attack prints a message), so the general list of that castle is the
    only measurement. A general still recorded as marching to another castle
    while his name is on this list has arrived here instead.
    """
    listed = set(names or ())
    out = []
    # No sortie-age bound (owner 2026-11-02): a roster is only read when this
    # castle is visited, so a sortie older than SORTIE_BUSY_TICKS whose
    # general stopped inside was never continued — g1097 held 26 stale
    # en_route marches (up to 40k ticks) whose generals sat garrisoned at
    # テラビル/バスカビル/オトラント while their targets still needed them.
    for step, sortie in _sorties(mem).items():
        source = sortie.get('source')
        if (sortie.get('status') not in ('en_route', 'launched_unconfirmed')
                or sortie.get('general') not in listed
                or sortie.get('purpose') in ('house', 'move')
                or sortie.get('target') in (None, castle)
                # The castle he left is not a transit: a sortie still listed at
                # its own source never departed, and the source verification
                # owns that case. An unrecorded source is not route evidence.
                or not source or source == castle
                or sortie.get('tick') is None):
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
        sortie = _sorties(mem).get(step)
        planned = sortie.get('target')
        _garrison_move(mem, sortie['general'], source=sortie.get('source'), target=castle)
        sortie.update(status='arrived', target=castle, transit_to=planned)
        _plan_region(mem).setdefault('source_override', {})[step] = castle
        _plan_region(mem).setdefault('orders', {})[step] = 'pending'
        if _plan_region(mem).get('active') == step:
            _plan_region(mem)['active'] = None
        if (_flow(mem).get('sortie_attempt') or {}).get('step') == step:
            _flow(mem).pop('sortie_attempt', None)
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
             for step, sortie in _sorties(mem).items()
             if sortie.get('general') == general
             and sortie.get('target') == castle
             and sortie.get('status') in ('en_route', 'arrived')]
    return max(march)[1] if march else None
