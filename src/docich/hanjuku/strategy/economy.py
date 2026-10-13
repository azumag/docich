"""戦略層: the monthly budget (soldiers, eggs, recruits, chikujou, merchants).

Price knowledge, wage/cash reserves, egg-recovery and repair targets, recruit
priority and the chikujou reserve. These functions decide amounts and
priorities; the month/shop flows carry them out through the menus.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart
from ...hanjuku_egg_reference import general_max_hp
from ..decision_log import _record
from ..os_layer.readers import STATUS_NAMES
from ..os_layer.world import NAME, _owned
from .orders import _hero_egg_broken


# ---------------------------------------------------------------- month
def _month_key(header):
    return f"{header['year']}-{header['month']}" if header else None


# Measured cart prices. グリンボー/ミックミー/ブラッキー come from the base
# charts (2.md -66G/11個・-80G/2個, 3.md -63G/21個); the rest are measured.
KNOWN_PRICES = {'イッテツーン': 1, 'ノリウツール': 18, 'クースカン': 24, 'ゼンマイン': 32,
                'グリンボー': 6, 'ミックミー': 40, 'ブラッキー': 3}

# 兵士は1G=1人で2桁入力が上限。チャート計画の無い月の残金はここまでの補充に使う。
SOLDIER_CAP = 99
SOLDIER_ENOUGH = 50   # owner 2026-10-11: with this many on hand, buy no more soldiers


def _soldier_count_scope(mem, header):
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    key = _month_key(header)
    chapter = mem.get('chapter')
    return ([identity, chapter, key] if identity is not None
            and type(chapter) is int and 1 <= chapter <= 12
            and key is not None and key == mem.get('month') else None)


def _soldier_count_current(mem, header):
    scope = _soldier_count_scope(mem, header)
    count = mem.get('soldiers_seen')
    return (scope is not None and mem.get('soldiers_seen_scope') == scope
            and mem.get('soldiers_seen_key') == scope[-1]
            and type(count) is int and count >= 0)


def _army_count_increment(state, field, limit):
    value = state.get(field, 0)
    value = value + 1 if type(value) is int and 0 <= value < limit else limit
    state[field] = value
    return value


def _zero_army_refill(mem, shop, header):
    """A measured empty army takes spare cash before a new recruitment fee."""
    if (not _soldier_count_current(mem, header) or mem['soldiers_seen'] != 0
            or not shop or shop.get('key') != _month_key(header)
            or shop.get('soldier_receipt') or mem.get('month_sub')
            or shop.get('recruit') in ('opened', 'unverified')):
        return
    gold = (header or {}).get('gold')
    cards = [] if shop.get('merchant_done') else shop.get('items') or []
    repair, egg = _house_repair_reserve(shop), shop.get('reserve', 0)
    if (type(gold) is not int or any(type(n) is not int or n < 0 for n in (repair, egg))
            or any(name not in KNOWN_PRICES or type(qty) is not int or qty < 0
                   for name, qty in cards)):
        return
    reserve = repair + egg
    available = max(0, gold - _egg_recovery_wage_floor(mem) - reserve
                    - sum(KNOWN_PRICES[name] * qty for name, qty in cards))
    target = min(SOLDIER_CAP, _soldier_target(mem, shop, header))
    amount = min(target, available)
    if amount <= 0:
        return  # no permission to spend wages, recovery or repair money
    if shop.get('recruit') != 'done':
        shop['recruit'] = 'deferred_army'
    shop.update(recruit_priority=False, recruit_reserve=0)
    previous = shop.get('soldiers')
    shop.update(soldiers=amount, soldiers_done=False, soldiers_target=target,
                soldier_total_target=target)
    if previous != amount or not shop.get('zero_army_refill_recorded'):
        shop['zero_army_refill_recorded'] = True
        _record(mem, 'zero_army_refill_plan', month=shop['key'],
                observed_metric={'soldiers': 0, 'gold': gold, 'refill': amount,
                                 'target': target, 'held': gold - available},
                reason='実人数0のため調整チャートの補充0を見直し、卵・修理・全賃金を残した資金で兵士を先に補充する')


def _soldier_target(mem, shop, header):
    """Normally 50 on hand; about 100 only after necessities and spare cash."""
    gold = (header or {}).get('gold')
    if type(gold) is not int:
        return SOLDIER_ENOUGH
    cards = [] if shop.get('merchant_done') else shop.get('items') or []
    if any(name not in KNOWN_PRICES for name, _ in cards):
        return SOLDIER_ENOUGH
    repair = _house_repair_reserve(shop)
    if type(repair) is not int:
        return SOLDIER_ENOUGH
    egg = (0 if shop.get('egg') in ('done', 'not_needed') else
           max(shop.get('reserve', 0), EGG_RECOVER_COST * len(_egg_recovery_targets(mem))))
    held = (egg + repair + WAGE_RESERVE + shop.get('recruit_reserve', 0)
            + sum(KNOWN_PRICES[name] * qty for name, qty in cards))
    key = _month_key(header) if header else None
    count = mem.get('soldiers_seen')
    known = (key is not None and mem.get('soldiers_seen_key') == key
             and type(count) is int and count >= 0)
    extra = max(0, 100 - count) if known else 100
    return 100 if gold - held >= extra + RECRUIT_COST else SOLDIER_ENOUGH


def _limit_soldier_refill(mem, shop, header):
    # Never rewrite a dispatched purchase or its pending payment receipt.
    if (shop.get('soldier_receipt') or shop.get('soldiers_done') is True
            or mem.get('month_sub')):
        return
    target = _soldier_target(mem, shop, header)
    key = _month_key(header) if header else None
    count = mem.get('soldiers_seen')
    known = (key is not None and key == shop.get('key')
             and mem.get('soldiers_seen_key') == key
             and type(count) is int and count >= 0)
    cap = min(SOLDIER_CAP, max(0, target - count) if known else target)
    payroll = _egg_recovery_wage_floor(mem)
    gold = (header or {}).get('gold')
    if payroll > WAGE_RESERVE and type(gold) is int:
        cap = min(cap, max(0, gold - payroll - _month_held_reserve(shop)
                           - shop.get('recruit_reserve', 0)))
    for field in ('soldiers', 'soldiers_target', 'soldiers_before_recruit'):
        value = shop.get(field)
        if type(value) is int and value >= 0:
            shop[field] = min(value, cap)
    if not shop.get('soldiers'):
        shop['soldiers_done'] = True
        if known and count >= target:
            _record(mem, 'soldier_refill_skip', month=shop.get('key'),
                    observed_metric={'soldiers': count, 'enough': target},
                    reason='当月の実兵士数が予算に応じた目標人数を満たすため資金を残す')
    shop['soldier_total_target'] = target


def _soldiers_enough(mem, shop, header):
    key = _month_key(header) if header else None
    count = mem.get('soldiers_seen')
    return (key is not None and key == shop.get('key') and mem.get('soldiers_seen_key') == key
            and type(count) is int and count >= _soldier_target(mem, shop, header))
# The month boundary pays 収入 − 総賃金; ending a month at 0G forced a
# dismissal in g358 (-1G) and g407 (-3G, 6 generals lost). Keep this much
# gold unspent so a shortfall month cannot push the balance negative
# (owner 2026-09-28: そもそも将軍解雇はしないで欲しい).
WAGE_RESERVE = 30
# 月一の たまごのかいふく / しょうぐんぼしゅう (gcgx: どちらも50G)。
EGG_RECOVER_COST = 50
RECRUIT_COST = 50
# 月イチイベント「ゴニンジャー」(gcgx event.html: 50Gで敵将軍の暗殺を依頼できる。
# 成功率は低い)。隠密戦隊ごにんじゃーへの依頼料。
GONINJA_COST = 50
# Owner (2026-10-06): 1000G以上ある時だけ依頼する。募集の閾値には使わない。
GONINJA_MIN_GOLD = 1000
MONTH_SUB_LIMIT = 8              # A presses through an unmeasured sub-screen
# The month menu stays on screen for a few observations after the A press that
# opens a sub (g462 18:06:02-05); only that stale frame may not end the sub.
MONTH_SUB_MENU_WAIT = 4
RECRUIT_CANDIDATE_LIMIT = 32     # paid candidate introductions outlast the generic 8 observations
EGG_RITUAL_LIMIT = 64            # measured paid recovery includes a long chant
MONTH_SUB_ABORT_B_LIMIT = 12     # ignored Bs (~18 s) before plain text is advanced with A (g662)


def _depleted_eggs(mem):
    # Ordinary eggs recover to four, while one-shot/king eggs hold only one.
    types = mem.get('egg_types') or {}
    counts = mem.get('egg_uses') or {}
    return {name for name, uses in counts.items()
            if type(uses) is int and 0 <= uses < (
                1 if types.get(name) in {'いっぱつエッグ', 'キングエッグ'} else 4)}


def _egg_measured_cost(mem):
    """Price one recovery per egg whose count was actually read below full.

    This is the price the month menu gates on: a recheck name is a question,
    not a debt, and pricing it there would refuse a check the game may quote at
    nothing (g574 2-9: one unread name priced 50G against 55G on hand, the
    screen was never opened; 3-2: two names priced 100G, the quote was 50G).
    The monthly hold in ``_extras_reserve`` stays an upper bound and the quote
    screen keeps its own wage floor, so opening cheap can never pay less than
    the reservation.
    """
    return EGG_RECOVER_COST * len(_depleted_eggs(mem))


def _egg_recovery_targets(mem):
    targets = _depleted_eggs(mem)
    # An attempted summon makes the sortie count stale. Check the recovery
    # screen; this flag is not proof of consumption and never decrements stock.
    targets.update(mem.get('egg_recheck') or [])
    from ..os_layer.roster import completed_survey
    survey = completed_survey(mem)
    if survey is not None:
        targets.update(name for name, fields in survey.get('dirty', {}).items() if 'egg' in fields)
    shop = mem.get('shop') or {}
    paid = (shop.get('egg_paid_actors') or []) if shop.get('key') == mem.get('month') else []
    return sorted(targets.difference(paid))


def _extras_reserve(mem, header):
    """Reserve the cost of all observed depleted eggs before soldiers.

    Owner rule (2026-09-28): with 50+ soldiers on hand (read from the
    「へいしすうはNめい」 line), egg recovery wins even when the gold is
    short of the full cost: hold ALL of it for the egg and buy no soldiers,
    so the balance grows until the recovery can run.

    The hero's depleted egg reserves first even without the soldier count:
    a castle defense cannot be retreated from and an eggless hero cannot
    answer a summon (g458 15:45: どうし 90 vs シェーブル 27, the enemy's egg
    summon came, the hero's egg was spent and he died -> game over).
    """
    targets = _egg_recovery_targets(mem)
    # The hold is an upper bound (recheck names included): it is money kept
    # from the soldiers and released in full once the real quote is paid, so a
    # possibly consumed egg must still own its fifty (a stale full sortie count
    # after an attempted summon). The gate that opens the screen prices the
    # measured eggs only - see _egg_estimate.
    cost = EGG_RECOVER_COST * len(targets)
    gold = (header or {}).get('gold')
    # Owner (2026-10-12): depleted eggs take their money before troops,
    # including armies below fifty. An unknown count cannot waive recovery.
    if type(gold) is not int or gold < 0 or not cost:
        reserve = 0
    else:
        # 卵へ温存（兵士は今月見送り）。賃金リザーブは守る。
        reserve = cost if gold >= cost else max(0, gold - WAGE_RESERVE)
    return reserve, not _charted_purchase_ahead(mem, header)


def _egg_recheck(mem):
    ally = (mem.get('battle') or {}).get('ally')
    if ally:
        from ..os_layer.roster import dirty_status
        dirty_status(mem, [ally], fields=('egg',))
        pending = mem.setdefault('egg_recheck', [])
        if ally not in pending:
            pending.append(ally)


def _refresh_egg_priority(mem, shop, header):
    """Keep depleted eggs ahead of a cached troop purchase."""
    key = _month_key(header) if header else None
    if (key is None or key != shop.get('key')
            or shop.get('egg') in ('done', 'not_needed')):
        return
    targets = _egg_recovery_targets(mem)
    if not targets:
        return
    if not shop.get('egg_priority'):
        shop['egg_priority'] = True
        if shop.get('egg') not in ('opened', 'unverified'):
            shop['egg'] = 'pending'
        _record(mem, 'egg_priority_replan', month=key,
                observed_metric={'soldiers': mem.get('soldiers_seen'), 'gold': (header or {}).get('gold'),
                                 'targets': targets, 'soldiers_planned': shop.get('soldiers')},
                reason='卵の回復対象が残るため、保存済みの購入計画も兵士補充より回復を優先する')
    shop['egg_cost'] = _egg_measured_cost(mem)
    reserve, _ = _extras_reserve(mem, header)
    shop['reserve'] = max(shop.get('reserve', 0), reserve)


def _egg_before_soldiers(shop):
    return bool((shop.get('egg_priority') or shop.get('egg_before_recruit'))
                and shop.get('egg') not in ('done', 'not_needed'))


def _prepare_recruit_egg_check(mem, shop, gold):
    """Recruitment may spend only after this month's recovery is resolved.

    Empty caches are unknown, so probe the game's recovery screen even when
    no named depleted egg was read. A saved failed check is reconsidered once
    on adoption of this guard; an unverified payment is never retried.
    """
    if (shop.get('recruit') not in ('pending', 'check')
            or shop.get('egg') in ('done', 'not_needed')
            or shop.get('egg_before_recruit')):
        return
    shop['egg_before_recruit'] = True
    if shop.get('egg') in (None, 'check', 'skipped'):
        shop['egg'] = 'pending'
    elif shop.get('egg') == 'opened' and not mem.get('month_sub'):
        shop['egg'] = 'unverified'
    shop['egg_cost'] = _egg_measured_cost(mem)
    cost = EGG_RECOVER_COST * len(_egg_recovery_targets(mem))
    if type(gold) is int:
        shop['reserve'] = max(shop.get('reserve', 0), min(cost, max(0, gold - WAGE_RESERVE)))


def _defer_recruit_for_egg(mem, shop):
    if shop.get('recruit') in ('pending', 'check'):
        shop['recruit'] = 'deferred_egg'
        _record(mem, 'recruit_deferred_egg', month=shop.get('key'),
                observed_metric={'egg_status': shop.get('egg'), 'gold': mem.get('gold')},
                reason='当月の卵回復完了・回復不要を確認できないため募集を行わず次月へ持ち越す')


def _recruit_spare_after_necessities(mem, shop, gold):
    """Once each owned castle can be staffed, only real spare cash recruits.

    Keep the existing demand/economy checks. This extra gate reserves pending
    purchases, soldiers, repair and wages; it never borrows the goninja floor.
    """
    from ..os_layer.roster import fresh
    seen = fresh(mem)
    owned = {STATUS_NAMES.get(c, c) for c in _owned(mem)}
    known = {STATUS_NAMES.get(c, c) for c in chart.CASTLE_NAMES.get(mem.get('chapter'), ())}
    evidence = {'generals': len(seen['names']) if seen and seen.get('complete') is True else None,
                'owned_castles': len(owned) if owned and owned <= known else None,
                'gold': gold, 'spare': None}
    if evidence['generals'] is None or evidence['owned_castles'] is None:
        return None, evidence
    if evidence['generals'] < evidence['owned_castles']:
        return True, evidence  # the existing shortage cash rules still apply
    cards = [] if shop.get('merchant_done') else shop.get('items') or []
    completed = (shop.get('soldiers_before_recruit_done', shop.get('soldiers_done')) is True
                 or (shop.get('soldier_receipt') or {}).get('status') == 'paid')
    soldiers = (0 if completed else shop.get('soldiers_before_recruit', shop.get('soldiers')))
    repair = _house_repair_reserve(shop)
    if (type(gold) is not int or any(name not in KNOWN_PRICES for name, _ in cards)
            or type(soldiers) is not int or soldiers < 0
            or type(repair) is not int or repair < 0):
        return None, evidence
    held = (WAGE_RESERVE + soldiers + repair
            + sum(KNOWN_PRICES[name] * quantity for name, quantity in cards))
    evidence.update(held=held, spare=gold - held)
    return evidence['spare'] >= RECRUIT_COST, evidence


def _recruit_cash_floor(shop):
    """Keep the menu's fee, wage and necessary reserves before any payment."""
    repair = _house_repair_reserve(shop)
    egg = (shop.get('reserve', 0) if shop.get('recruit_priority')
           and (shop.get('egg') in ('pending', 'check') or _egg_before_soldiers(shop)) else 0)
    if any(type(value) is not int or value < 0 for value in (repair, egg)):
        return None
    return RECRUIT_COST + WAGE_RESERVE + repair + egg


def _recruit_dialog_cash_ready(mem, shop, gold):
    _reserve_general_repair(mem, shop, gold)
    spare, evidence = _recruit_spare_after_necessities(mem, shop, gold)
    floor = _recruit_cash_floor(shop)
    evidence['minimum_gold'] = floor
    if type(gold) is not int or floor is None:
        return None, evidence
    if gold < floor:
        return False, evidence
    return spare, evidence


def _defer_recruit_cash(mem, shop, gold, evidence):
    """Restore only an affordable pre-recruit plan; zero budget is not a receipt."""
    original = shop.get('soldiers_before_recruit')
    completed = (shop.get('soldiers_before_recruit_done', shop.get('soldiers_done')) is True
                 or (shop.get('soldier_receipt') or {}).get('status') == 'paid')
    cards = [] if shop.get('merchant_done') else shop.get('items') or []
    if not completed and type(original) is int and original >= 0:
        affordable = (max(0, gold - WAGE_RESERVE - _month_held_reserve(shop)
                         - sum(KNOWN_PRICES[name] * qty for name, qty in cards))
                      if type(gold) is int and all(name in KNOWN_PRICES for name, _ in cards) else 0)
        shop['soldiers'] = min(original, affordable)
        shop['soldiers_done'] = shop['soldiers'] == 0  # no purchase claimed
    shop.update(recruit='deferred_cash', recruit_priority=False, recruit_reserve=0)
    _record(mem, 'recruit_cash_held', month=shop.get('key'), observed_metric=evidence,
            reason='募集費・卵・修理・賃金予備を支払い直前にも確認し、城を守る人数がいる場合は必要兵士の予算も温存する')


def _recruit_target(mem):
    from ..os_layer.roster import roles
    chapter = mem.get('chapter')
    expected = set(chart.CASTLE_NAMES.get(chapter, ())) - {chart.boss_castle(chapter)}
    owned = _owned(mem)
    # Unknown ownership/chapters are not zero castles; aliases count once.
    owned = {STATUS_NAMES.get(c, c) for c in owned}
    expected = {STATUS_NAMES.get(c, c) for c in expected}
    if not owned or not owned <= expected or type(chapter) is not int:
        return None
    raw = (mem.get('chart_plan') or {}).get('recruitment')
    if raw is None:
        raw = chart.RECRUITMENT_BY_CHAPTER.get(chapter, {'per_castle': 2, 'attack': 3})
    try:
        spec = roles(raw)
    except ValueError:
        return None
    target = spec['per_castle'] * len(owned) + spec['attack']
    from ..os_layer.roster import MAX_GENERALS
    return min(target, MAX_GENERALS) if target > 0 else None


def _recruit_sufficient(mem):
    from ..os_layer.roster import fresh
    seen, target = fresh(mem), _recruit_target(mem)
    return seen is not None and target is not None and len(seen['names']) >= target


def _stop_unneeded_recruit(mem, shop):
    from ..os_layer.roster import fresh
    if shop.get('recruit') in ('opened', 'done', 'unverified'):
        return True  # finish the existing transaction; do not invent a refund
    seen, target = fresh(mem), _recruit_target(mem)
    if seen is not None and target is not None and len(seen['names']) < target and seen.get('complete') is True:
        return False
    sufficient = _recruit_sufficient(mem)
    status = 'not_needed' if sufficient else 'deferred_roster'
    if shop.get('recruit') != status:
        _record(mem, 'recruit_not_needed' if sufficient else 'recruit_roster_unknown',
                month=shop.get('key'),
                observed_metric={'roster_lower_bound': len(seen['names']) if seen else None,
                                 'target': target, 'complete': seen.get('complete') if seen else False},
                reason=('新鮮な同一scanの実一覧で必要人数以上を確認したため募集を見送る' if sufficient
                        else '実在人数または必要人数が不明のため募集を保留し、実一覧を再確認する'))
    shop['recruit'] = status
    shop['recruit_priority'] = False
    mem['recruit_hold'] = {'chapter': mem.get('chapter'), 'month': shop.get('key'),
                          'reason': ('enough_observed' if sufficient
                                     else 'roster_incomplete' if seen and target is not None
                                     else 'roster_or_demand_unknown'), 'status': status}
    if sufficient:
        shop['recruit_reserve'] = 0
    else:
        mem['recruit_roster_recheck'] = True
    return True


def _recruit_shortage(mem):
    from ..os_layer.roster import fresh
    seen, target = fresh(mem), _recruit_target(mem)
    if (seen is None or target is None or seen.get('complete') is not True
            or len(seen['names']) >= target):
        return None
    return {'available_observed': list(seen['names']), 'count_lower_bound': len(seen['names']),
            'target': target, 'total_roster': len(seen['names']),
            'garrison': mem.get('garrison') or {}, 'placement': 'observed_only'}


def _stop_uneconomic_recruit(mem, shop):
    from ..os_layer.roster import economics
    if shop.get('recruit') in ('opened', 'done', 'unverified'):
        return False  # finish the existing conversation without guessing refunds
    owned = {STATUS_NAMES.get(c, c) for c in _owned(mem)}
    economy = economics(mem, owned)
    if economy is None:
        # Unread castles only add income: a covering lower bound suffices
        # (g684 2-2: ゴーメン unread, recruitment held every month).
        bound = economics(mem, owned, lower_bound=True)
        if bound and bound['castles_read'] and bound['income'] >= bound['wages'] + bound['additional_wage_max']:
            economy = bound
    # Owner approved ordinary observed income plus the existing cash reserves.
    # Do not silently apply an unapproved permanent quarter-income factor.
    sustainable = economy and economy['income'] >= economy['wages'] + economy['additional_wage_max']
    if sustainable:
        mem.pop('recruit_hold', None)
        return False
    status = 'deferred_economy' if economy is None else 'not_affordable'
    if shop.get('recruit') != status:
        _record(mem, 'recruit_economy_held', month=shop.get('key'),
                observed_metric={'economy': economy,
                                 'pending_employees': mem.get('recruit_payroll_pending') or []},
                expected_metric='募集後も賃金を収入で支払えることを確認',
                reason=('実収入・同一scan総賃金が不明のため募集を保留し確認する' if economy is None
                        else '募集後の賃金を安全側の収入で賄えないため募集を見送る'))
    shop['recruit'] = status
    shop['recruit_priority'] = False
    mem['recruit_hold'] = {'chapter': mem.get('chapter'), 'month': shop.get('key'),
                          'reason': ('pending_employees' if mem.get('recruit_payroll_pending')
                                     else 'income_or_payroll_unknown' if economy is None else 'wages_exceed_income'),
                          'status': status}
    mem['recruit_roster_recheck'] = economy is None
    return True


def _house_repair_reserve(shop):
    values = (shop.get('hero_repair_reserve', 0), shop.get('general_repair_reserve', 0))
    return sum(values) if all(type(v) is int and v >= 0 for v in values) else None


def _month_held_reserve(shop):
    return shop.get('reserve', 0) + _house_repair_reserve(shop)


def _general_repair_targets(mem):
    """Named current castle statuses that the house route can safely recheck."""
    from ..os_layer.world import HOUSE_VIEW, _castle_status_after_sortie, _repair_hp_ready
    if mem.get('chapter') not in HOUSE_VIEW:
        return []
    owned = _owned(mem)
    if not owned:
        return []
    targets = []
    for name, info in (mem.get('house_eggs') or {}).items():
        if (name == NAME or not isinstance(info, dict) or info.get('general') != name
                or general_max_hp(name) is None or info.get('broken') is not True
                or info.get('location') != 'castle' or info.get('location_observed') is not True
                or info.get('observed_chapter') != mem.get('chapter')
                or not mem.get('month') or info.get('month') != mem.get('month')
                or type(info.get('observed_tick')) is not int or type(mem.get('tick')) is not int
                or not 0 <= info['observed_tick'] <= mem['tick'] or not _repair_hp_ready(info)):
            continue
        if any(s.get('general') == name and s.get('status') in ('en_route', 'launched_unconfirmed')
               and not _castle_status_after_sortie(info, s, mem)
               for s in (mem.get('sorties') or {}).values()):
            continue
        if len(owned) == 1 and not any(
                name in (mem.get('garrison') or {}).get(c, [])
                and len((mem.get('garrison') or {}).get(c, [])) > 1 for c in owned):
            continue  # keep the last castle's sole defender; dispatch rechecks it too
        targets.append(name)
    return sorted(targets)


def _reserve_general_repair(mem, shop, gold):
    targets = _general_repair_targets(mem)
    reserve = 50 if targets else 0  # one affordable next trip, not a fee for every unread general
    changed = (shop.get('general_repair_reserve', 0) != reserve
               or shop.get('general_repair_targets', []) != targets)
    shop.update(general_repair_reserve=reserve, general_repair_targets=targets)
    if reserve and not shop.get('soldiers_done') and type(gold) is int:
        cards = [] if shop.get('merchant_done') else shop.get('items') or []
        if all(n in KNOWN_PRICES for n, _ in cards):
            card_cost = sum(KNOWN_PRICES[n] * qty for n, qty in cards)
            shop['soldiers'] = min(shop.get('soldiers', 0), max(0, gold - card_cost
                - _month_held_reserve(shop) - WAGE_RESERVE - shop.get('recruit_reserve', 0)))
            shop['soldiers_done'] = shop['soldiers'] == 0
    if changed:
        _record(mem, 'general_repair_funds_reserved', month=shop.get('key'),
                observed_metric={'generals': targets, 'reserve': reserve, 'gold': gold,
                                 'soldiers': shop.get('soldiers')},
                reason='在城・卵破損を確認した将軍の次の家修復費を募集・兵士・築城より先に確保する')


def _reserve_hero_repair(mem, shop, gold):
    if not _hero_egg_broken(mem) or shop.get('hero_repair_reserve'):
        return
    shop['hero_repair_reserve'] = 50  # cheapest measured house gift; wage is separate
    items = [] if shop.get('merchant_done') else shop.get('items') or []
    card_cost = (sum(KNOWN_PRICES[n] * qty for n, qty in items)
                 if all(n in KNOWN_PRICES for n, _ in items) else None)
    if not shop.get('soldiers_done') and type(gold) is int and card_cost is not None:
        shop['soldiers'] = min(shop.get('soldiers', 0), max(0, gold - card_cost
                               - _month_held_reserve(shop) - WAGE_RESERVE
                               - shop.get('recruit_reserve', 0)))
        shop['soldiers_done'] = shop['soldiers'] == 0
    _record(mem, 'hero_repair_funds_reserved', month=shop.get('key'),
            observed_metric={'gold': gold, 'reserve': 50, 'soldiers': shop.get('soldiers')},
            reason='主人公の壊れた卵の家修復費を募集・兵士・築城へ使い切らず温存する')


def _prioritise_recruit(mem, shop, gold):
    """Once per month, reserve a recruitment fee before optional soldiers.

    Includes existing cached shops on hotload. Original card purchases and
    the existing egg/wage reserves are kept; unconfirmed deaths are not used.
    """
    if shop.get('recruit') in ('deferred_egg', 'deferred_cash', 'deferred_army') or _stop_unneeded_recruit(mem, shop):
        return
    if 'soldiers_before_recruit' not in shop:
        shop['soldiers_before_recruit'] = (None if shop.get('recruit_budget_version') == 2
                                           else shop.get('soldiers'))
        shop['soldiers_before_recruit_done'] = (None if shop.get('recruit_budget_version') == 2
                                               else shop.get('soldiers_done') is True)
    if shop.get('recruit_budget_version') == 2 and shop.get('recruit') == 'check':
        return
    shortage = _recruit_shortage(mem)
    if not shortage or _stop_uneconomic_recruit(mem, shop):
        return
    shop['recruit_budget_version'] = 2
    shop['recruit_priority'] = True
    if shop.get('recruit') not in ('opened', 'done', 'unverified'):
        shop['recruit'] = 'check'
    items = shop.get('items') or []
    card_cost = (sum(KNOWN_PRICES[n] * qty for n, qty in items)
                 if all(n in KNOWN_PRICES for n, _ in items) else None)
    # Only subtract pending card purchases. A cached post-merchant menu
    # already shows the remaining balance.
    if shop.get('merchant_done'):
        card_cost = 0
    available = (max(0, gold - card_cost - _month_held_reserve(shop) - WAGE_RESERVE)
                 if type(gold) is int and card_cost is not None else 0)
    budget = 0 if shop.get('recruit') == 'done' else min(RECRUIT_COST, available)
    shop['recruit_reserve'] = budget
    if not shop.get('soldiers_done'):
        if card_cost is not None:
            shop['soldiers'] = min(shop.get('soldiers', 0), max(0, available - budget))
        if shop['soldiers'] == 0:
            shop['soldiers_done'] = True
    _record(mem, 'recruit_priority_plan', month=shop.get('key'),
            observed_metric={**shortage, 'gold': gold, 'reserved': budget,
                             'soldiers': shop.get('soldiers')},
            reason='確認できる将軍が不足しているため募集費を兵士補充より先に確保する。総人数や死亡は未確定')


def _plan_extras(mem, shop, reserve, recruit):
    shop['reserve'] = reserve
    egg_cost = _egg_measured_cost(mem)
    shop['egg'] = 'pending' if reserve else ('check' if _egg_recovery_targets(mem) else None)
    shop['egg_cost'] = egg_cost
    shop['egg_priority'] = bool(_egg_recovery_targets(mem))
    shop['recruit'] = 'check' if recruit else None
    shop['chikujou'] = 'check'
    _reserve_hero_repair(mem, shop, shop.get('gold_start'))
    _reserve_general_repair(mem, shop, shop.get('gold_start'))
    _prioritise_recruit(mem, shop, shop.get('gold_start'))
    _limit_soldier_refill(mem, shop, {'year': int(shop['key'].split('-')[0]),
        'month': int(shop['key'].split('-')[1]), 'gold': shop.get('gold_start')})
    if reserve or recruit:
        _record(mem, 'month_extras_plan', chart_step='1-month', month=shop['key'],
                strategy_variant=shop.get('variant', 'chart'),
                observed_metric={'egg_uses': dict(mem.get('egg_uses') or {})},
                plan={'egg_recover': bool(reserve), 'egg_recover_budget': reserve,
                      'egg_recover_estimate': egg_cost,
                      'egg_recover_targets': _egg_recovery_targets(mem),
                      'recruit_if_left': RECRUIT_COST if recruit else None,
                      'recruit_before_soldiers': bool(shop.get('recruit_priority')),
                      'recruit_reserve': shop.get('recruit_reserve', 0)},
                reason=('将軍不足時は募集費を兵士より先に確保し、卵回復・賃金予備も維持する'
                        if shop.get('recruit_priority') else
                        '使用回数が減った卵の全回復費を兵士より先に確保し、必要な兵士の補充の後に余りがあれば将軍を募集'))


def _adjusted_plan(mem, header, key):
    """Month purchases from an adopted adjusted chart (#1085 L2).

    Cards reuse the merchant flow (unlisted/unaffordable items are skipped
    and recorded there). An adjusted chart may buy cards whose price was never
    measured, so the soldier count here is only a provisional estimate: after
    the merchant, ``month_step`` recomputes it from the gold read on screen
    (``_recalc_soldiers``) before opening the refill. General recruitment has
    no measured menu yet, so it is recorded as a deviation and not attempted.
    """
    spec = (mem.get('chart_plan') or {}).get('purchases')
    if not spec or key != f'{spec["month"][0]}-{spec["month"][1]}':
        return None
    gold = header['gold']
    items = [list(i) for i in spec['cards']]
    target = min(spec['soldiers'], 99)
    unpriced = sorted({name for name, _ in items if name not in KNOWN_PRICES})
    reserve, recruit = _extras_reserve(mem, header)
    left = gold - sum(KNOWN_PRICES.get(name, 0) * qty for name, qty in items) - reserve
    soldiers = max(0, min(target, left - WAGE_RESERVE))
    shop = mem['shop'] = {'key': key, 'items': items, 'soldiers': soldiers, 'merchant_done': False,
                          'variant': 'chart_adjusted', 'soldiers_done': target == 0,
                          'soldiers_target': target, 'soldiers_from_gold': True,
                          'gold_start': gold}
    _plan_extras(mem, shop, reserve, recruit)
    _record(mem, 'month_plan', chart_step='adjusted-month', strategy_variant='chart_adjusted',
            month=key, gold=gold,
            plan={'cards': items, 'soldiers_provisional': soldiers, 'unpriced_cards': unpriced},
            deviation_reason=('recruit_owner_rule' if spec.get('generals') else None),
            expected_metric={'adjusted_cards': [list(i) for i in spec['cards']],
                             'adjusted_soldiers': spec['soldiers'],
                             'adjusted_generals': spec.get('generals', 0)},
            reason=spec.get('note') or '調整チャートの月次購入')
    return shop


def _charted_purchase_ahead(mem, header):
    """True while a charted month purchase still lies ahead.

    Its budget stays reserved: the chart's own soldier count caps the refill
    there, and a month the chart does not cover buys nothing at all.
    """
    if not header:
        return False
    here = (header['year'], header['month'])
    planned = [tuple(p.get('month') or ()) for p in chart.purchases(mem.get('chapter') or 0)]
    adjusted = ((mem.get('chart_plan') or {}).get('purchases') or {}).get('month')
    if adjusted:
        planned.append(tuple(adjusted))
    return any(plan > here for plan in planned)


def _plan(mem, header):
    key = _month_key(header)
    shop = mem.get('shop')
    if shop and shop.get('key') == key:
        _reserve_hero_repair(mem, shop, (header or {}).get('gold'))
        _reserve_general_repair(mem, shop, (header or {}).get('gold'))
        _limit_soldier_refill(mem, shop, header)
        return shop
    adjusted = _adjusted_plan(mem, header, key) if header else None
    if adjusted:
        return adjusted
    spec = None
    if header:
        spec = chart.purchase_for(mem.get('chapter') or 0, header['year'], header['month'])
    ahead = _charted_purchase_ahead(mem, header)
    if not spec:
        if ahead:
            if _soldier_count_current(mem, header) and mem['soldiers_seen'] == 0:
                return _soldier_refill_plan(mem, header, key)
            reserve, _ = _extras_reserve(mem, header)
            if not reserve and not _recruit_shortage(mem):
                return None
            shop = mem['shop'] = {'key': key, 'items': [], 'soldiers': 0,
                'merchant_done': True, 'soldiers_done': True, 'gold_start': header['gold'],
                'variant': 'egg_recovery_only'}
            _plan_extras(mem, shop, reserve, False)
            return shop
        return _soldier_refill_plan(mem, header, key)
    gold = header['gold']
    if gold >= spec['chart_gold']:
        items = [list(i) for i in spec['cards']]
        variant, deviation = 'chart', None
        # Unpriced cards would overstate the gold left for soldiers.
        leftover = (gold - sum(KNOWN_PRICES[name] * qty for name, qty in items)
                    if all(name in KNOWN_PRICES for name, _ in items) else None)
    else:
        items, left = [], gold
        for name, qty in spec['priority']:
            if name not in KNOWN_PRICES:
                break
            n = min(qty, left // KNOWN_PRICES[name])
            if n:
                items.append([name, n])
                left -= n * KNOWN_PRICES[name]
        leftover = left
        variant = 'budget_boss_kit_first'
        deviation = f"所持金{gold}Gがチャート想定{spec['chart_gold']}G未満"
    # With a later charted purchase ahead, its budget wins: the chart's own
    # soldier count is the ceiling. Otherwise the gold left goes to soldiers.
    limit = spec['soldiers'] if ahead else SOLDIER_CAP
    reserve, recruit = _extras_reserve(mem, header)
    if leftover is None:
        reserve = 0          # unpriced cards: no measured room for the egg
    soldiers = (spec['soldiers'] if leftover is None
                else max(0, min(limit, leftover - reserve - WAGE_RESERVE)))
    shop = mem['shop'] = {'key': key, 'items': items, 'soldiers': soldiers, 'merchant_done': False,
                          'variant': variant,
                          'soldiers_done': soldiers == 0, 'gold_start': gold}
    _plan_extras(mem, shop, reserve, recruit)
    _record(mem, 'month_plan', chart_step='1-month', strategy_variant=variant, month=key, gold=gold,
            plan={'cards': items, 'soldiers': shop['soldiers']}, deviation_reason=deviation,
            expected_metric={'chart_cards': [list(i) for i in spec['cards']],
                             'chart_soldiers': spec['soldiers'], 'chart_gold': spec['chart_gold']},
            reason=spec['note'])
    return shop


def _soldier_refill_plan(mem, header, key):
    """Months the chart has no purchase for: spend the gold left on soldiers.

    The owner policy (2026-10-12) aims at fifty, or about a hundred
    with surplus after recovery and other necessities.
    """
    if not header:
        return None
    gold = header['gold']
    reserve, recruit = _extras_reserve(mem, header)
    soldiers = min(SOLDIER_CAP, max(0, gold - reserve - WAGE_RESERVE))
    shop = mem['shop'] = {'key': key, 'items': [], 'soldiers': soldiers, 'merchant_done': False,
                          'variant': 'soldier_refill_only', 'soldiers_done': soldiers == 0,
                          'gold_start': gold}
    _plan_extras(mem, shop, reserve, recruit)
    _record(mem, 'month_plan', chart_step='1-month', strategy_variant='soldier_refill_only',
            month=key, gold=gold, plan={'cards': [], 'soldiers': shop['soldiers']},
            deviation_reason='chart_month_uncovered',
            expected_metric={'soldier_cap': SOLDIER_CAP},
            reason='チャートに当月の購入計画がないため残金で兵士を補充')
    return shop


def _egg_estimate(shop):
    """What the month menu may gate on: measured eggs, never a recheck guess.

    Zero is a real estimate (only unread recheck names remain) and must not
    fall back to a fee the game has not asked for; the quote screen carries its
    own wage floor, so an optimistic estimate can open but never underpay.
    """
    return shop['egg_cost'] if type(shop.get('egg_cost')) is int else EGG_RECOVER_COST


CHIKUJOU_SPARE = 1             # gold beyond the necessities before a castle's quote is read


def _chikujou_affordable(gold, reserve):
    """Any gold above the necessities may buy a level; the quote decides.

    Owner (2026-10-11): アルマムーン alone was upgraded. A level costs
    20 - the defending general's 内政 (gcgx info.html), at most 20G, but a
    castle was only opened with 40G spare beyond the reserve, so the home
    upgrade used up the margin of every other holding. The reserve already
    holds the wage, egg, hero repair, recruit, soldier and card money, and
    the real NGかかりますが quote is still checked against it before うむッ.
    """
    return type(gold) is int and reserve is not None and gold >= reserve + CHIKUJOU_SPARE


def _no_empty_egg_to_recover(mem):
    """Every egg awaiting recovery was read with uses left (none at 0, none unread)."""
    uses = mem.get('egg_uses') or {}
    targets = _egg_recovery_targets(mem)
    return bool(targets) and all(type(uses.get(name)) is int and uses[name] >= 1 for name in targets)


def _recruit_egg_ready(mem, shop):
    """The menu and each unpaid dialogue share the same recovery permission."""
    if shop.get('egg') in ('done', 'not_needed'):
        return True
    return (shop.get('egg') == 'skipped' and bool(shop.get('recruit_priority'))
            and _no_empty_egg_to_recover(mem)
            and not set(_egg_recovery_targets(mem)).intersection(mem.get('egg_recheck') or []))


def _chikujou_reserve(mem, shop):
    """Only money left after unfinished necessities may fund optional building."""
    def amount(value):
        return value if type(value) is int and value > 0 else 0

    # Optional building yields to the worst case while this month's recovery
    # is unresolved: the estimate is a lower bound (measured counts only),
    # while an unread recheck name may still turn into a real quote.
    # Owner (2026-10-11): 145G on hand and no castle was built. A recovery
    # already skipped as unaffordable this month spends nothing more now, so
    # it keeps only the hero's egg (g604: an eggless hero in a defense is game
    # over), not 50G per general that had summoned once against a level
    # costing at most 20G.
    status = shop.get('egg')
    if status in ('done', 'not_needed'):
        egg = 0
    elif status == 'skipped':
        # The hero awaiting a re-read after a summon (egg_recheck / dirty
        # roster egg, old count 4 or none) is not proof his egg is full.
        egg = EGG_RECOVER_COST if NAME in _egg_recovery_targets(mem) else 0
    else:
        egg = max(amount(shop.get('reserve')), amount(shop.get('egg_cost')),
                  EGG_RECOVER_COST * len(_egg_recovery_targets(mem)))
    repair = (max(amount(shop.get('hero_repair_reserve')), 50 if _hero_egg_broken(mem) else 0)
              + (50 if _general_repair_targets(mem) else 0))
    soldiers = 0 if shop.get('soldiers_done') else amount(shop.get('soldiers'))
    # A recruit stopped as unneeded/unaffordable no longer holds its fee; a
    # general shortage (recruit_priority) still saves it for the next month.
    recruit = (amount(shop.get('recruit_reserve'))
               if shop.get('recruit') != 'done' and (
                   shop.get('recruit') in ('pending', 'check', 'opened')
                   or shop.get('recruit_priority')) else 0)
    if shop.get('recruit_priority') and shop.get('recruit') not in ('done', 'opened', 'unverified'):
        # g684 2-9: deferral zeroed recruit_reserve, so building spent the
        # fee every month while generals stayed short (7 of 13).
        recruit = max(recruit, RECRUIT_COST)
    cards = [] if shop.get('merchant_done') else shop.get('items') or []
    if any(name not in KNOWN_PRICES for name, _ in cards):
        return None  # an unpriced required purchase is not spare money
    return (max(WAGE_RESERVE, _egg_recovery_wage_floor(mem)) + egg + repair + recruit + soldiers
            + sum(KNOWN_PRICES[name] * quantity for name, quantity in cards))


def _chikujou_candidates(mem):
    """Home, staffed holdings, then unmeasured holdings; never an enemy/empty castle."""
    chapter = mem.get('chapter') or 1
    home = chart.home_castle(chapter)
    owned = _owned(mem)
    if mem.get('home_lost'):
        owned.discard(home)  # also protect a hotloaded stale captured-home record
    garrison = mem.get('garrison') or {}
    names = [c for c in chart.CASTLE_NAMES.get(chapter, ())
             if c in owned and not (c in garrison and garrison[c] == [])]
    names.sort(key=lambda c: (c != home, not bool(garrison.get(c))))
    return [STATUS_NAMES.get(c, c) for c in names]


def _recruit_fee_observed(sub):
    before, receipt = sub.get('gold_before'), sub.get('recruit_paid_gold')
    return (sub.get('kind') == 'recruit' and type(before) is int and type(receipt) is int
            and before - receipt == RECRUIT_COST)


def _hero_egg_wage_floor(mem):
    """Wage floor for the hero's own egg: this month's measured payroll.

    WAGE_RESERVE (30G) is the unknown-payroll fallback that keeps generals from being
    fired. g604 3-11 had 68G and a measured payroll of 16G (0+4+4+5+3), so the
    blind floor refused the 50G that would have kept the hero alive. Only a
    fresh, complete roster with every wage read lowers it; otherwise 30G.
    """
    from ..os_layer.roster import MAX_RECRUIT_WAGE, fresh
    receipt = fresh(mem)
    wages = (receipt or {}).get('wages')
    if (not receipt or receipt.get('complete') is not True or mem.get('recruit_payroll_pending')
            or not isinstance(wages, dict) or set(wages) != set(receipt.get('names') or ())
            or any(type(v) is not int or not 0 <= v <= MAX_RECRUIT_WAGE for v in wages.values())):
        return WAGE_RESERVE
    return sum(wages.values())


def _egg_recovery_wage_floor(mem):
    """Use measured full current payroll for recovery, never a partial walk.

    g713 had a real 100G quote, 112G, and the four-member payroll was 8G.
    A blind 30G floor declined the affordable recovery. Large known payrolls
    still reserve their entire sum instead of capping it at the fallback.
    """
    from ..os_layer.roster import MAX_RECRUIT_WAGE, fresh, run_identity
    receipt = fresh(mem)
    identity = run_identity(mem.get('_run_identity'))
    wages = (receipt or {}).get('wages')
    if (identity is None or not receipt or receipt.get('identity') != identity
            or receipt.get('complete') is not True or mem.get('recruit_payroll_pending')
            or not isinstance(wages, dict) or set(wages) != set(receipt.get('names') or ())
            or any(type(v) is not int or not 0 <= v <= MAX_RECRUIT_WAGE for v in wages.values())):
        return WAGE_RESERVE
    return sum(wages.values())


def _merchant_spendable(mem, shop, header):
    """Cash after the same payroll and unpaid necessities used by recovery.

    A chart/adjusted/cached card plan is not authority to spend the wage or
    egg reserve. Unknown/malformed month, money or reserves cannot confirm.
    Soldier refills remain optional and are recalculated after the merchant.
    """
    if (not isinstance(header, dict)
            or any(type(header.get(k)) is not int for k in ('year', 'month', 'gold'))
            or header['year'] < 1 or not 1 <= header['month'] <= 12 or header['gold'] < 0
            or shop.get('key') != _month_key(header)
            or mem.get('month') not in (None, shop.get('key'))):
        return None
    repair = _house_repair_reserve(shop)
    egg, recruit = shop.get('reserve', 0), shop.get('recruit_reserve', 0)
    if repair is None or any(type(v) is not int or v < 0 for v in (egg, recruit)):
        return None
    return max(0, header['gold'] - _egg_recovery_wage_floor(mem) - egg - repair - recruit)


def _goninja_budget(mem, header):
    """Readable cash and the owner's explicit floor, else None.

    Owner (2026-10-06): 所持金1000G以上なら依頼し、未満・読取不明は断る。
    This supersedes the old chart-purchase/reserve-based spare calculation.
    Never substitute remembered funds or borrow this floor for recruitment.
    """
    gold = (header or {}).get('gold')
    if type(gold) is not int or gold < 0:
        return None
    return {'gold': gold, 'minimum_gold': GONINJA_MIN_GOLD}
