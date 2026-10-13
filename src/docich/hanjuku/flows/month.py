"""手順層: the monthly castle menu and its sub-transactions.

Walks the month menu, starts purchases, recruits and chikujou according to
the economy plan, and verifies each transaction against the visible receipt.
"""
from __future__ import annotations

import re

from ..os_layer.screen import Screen
from ..os_layer.font import UNKNOWN
from ... import hanjuku_chart as chart
from ...hanjuku_egg_stock import observed_uses
from ..decision_log import _record
from .egg_recovery import MONTH_SUB_EXIT_KINDS, _egg_recovery_step, _individual_egg_selected
from .shop import quantity_step
from ..judgment.battle import _egg_quantities_unknown, _own_egg_empty
from ..os_layer.commands import MENU_NAV_PRESS_LIMIT, guarded_menu_to, menu_to, pad
from ..os_layer.readers import (RECRUIT_GOODBYE, RECRUIT_INTRO, STATUS_NAMES, _current, _month_dialog_body,
                                _month_dialog_unread, _month_menu_quiet, _options, month_menu_ready)
from ..os_layer.world import NAME
from ..strategy.economy import (EGG_RECOVER_COST, EGG_RITUAL_LIMIT, MONTH_SUB_ABORT_B_LIMIT, MONTH_SUB_LIMIT,
                                MONTH_SUB_MENU_WAIT, RECRUIT_CANDIDATE_LIMIT, RECRUIT_COST, SOLDIER_ENOUGH,
                                WAGE_RESERVE, _army_count_increment, _chikujou_affordable, _chikujou_candidates, _chikujou_reserve,
                                _defer_recruit_cash, _defer_recruit_for_egg, _depleted_eggs, _egg_before_soldiers,
                                _egg_estimate, _egg_measured_cost, _egg_recovery_targets, _egg_recovery_wage_floor,
                                _hero_egg_wage_floor, _house_repair_reserve, _limit_soldier_refill, _soldier_spendable,
                                _month_held_reserve, _month_key, _plan, _prepare_recruit_egg_check,
                                _prioritise_recruit, _recruit_cash_floor, _recruit_dialog_cash_ready,
                                _recruit_egg_ready, _recruit_fee_observed, _recruit_spare_after_necessities,
                                _refresh_egg_priority, _soldier_count_current, _soldier_count_scope,
                                _soldiers_enough, _stop_uneconomic_recruit, _stop_unneeded_recruit,
                                _zero_army_refill)


MONTH_TEXT_ADVANCE_LIMIT = 6    # A presses per unread message over the month menu


def army_count_owned(mem):
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    state = mem.get('army_count_check')
    shop = mem.get('shop') or {}
    return (identity is not None and isinstance(state, dict) and not mem.get('month_sub')
            and type(mem.get('chapter')) is int and 1 <= mem['chapter'] <= 12
            and isinstance(mem.get('month'), str)
            and re.fullmatch(r'[1-9][0-9]*-(?:[1-9]|1[0-2])', mem['month']) is not None
            and state.get('scope') == [identity, mem.get('chapter'), mem.get('month')]
            and (state.get('stage') not in ('measured', 'unverified')
                 or (state.get('stage') == 'measured' and state.get('refill_requested') is True
                     and not shop.get('soldier_receipt') and not shop.get('soldiers_done'))))


def army_count_pending_step(screen, mem):
    """Unrecognized frames in this free check must never borrow a paid A."""
    if not army_count_owned(mem) or screen.kind == 'month_menu':
        return None
    state = mem['army_count_check']
    if screen.kind not in ('text', 'unknown'):
        state.update(stage='unverified', result='unverified')
        if isinstance(mem.get('shop'), dict) and not mem['shop'].get('soldier_receipt'):
            mem['shop']['soldiers_done'] = True
        return None  # a recognized battle/event keeps its own input owner
    if state.get('stage') != 'closing':
        if _army_count_increment(state, 'unread_frames', 4) < 4:
            return []
        state.update(stage='closing', result='unverified')
    if (state.get('result') == 'unverified' and isinstance(mem.get('shop'), dict)
            and not mem['shop'].get('soldier_receipt')):
        mem['shop']['soldiers_done'] = True
    return [pad('b')] if _army_count_increment(state, 'exit_presses', 4) <= 3 else []


def _month_army_count_check(screen, mem, shop):
    """Read the stock even when the purchase plan is zero; never pay here."""
    scope = _soldier_count_scope(mem, screen.header)
    if scope is None or mem.get('month_sub') or (shop or {}).get('soldier_receipt'):
        return None
    intro = (screen.selected == 'へいしほじゅう' and re.fullmatch(
        r'げんざいわがぐんのへいしすうは[0-9０-９]+めいですがなんめいほじゅうされますかな\?',
        _month_dialog_body(screen)) is not None)
    known = _soldier_count_current(mem, screen.header)
    state = mem.get('army_count_check')
    if not isinstance(state, dict) or state.get('scope') != scope:
        if known and not intro:
            return None
        state = mem['army_count_check'] = {'scope': scope, 'stage': 'opening', 'observations': 0}
        if not known:
            mem.pop('soldiers_seen_key', None)  # an old total is history, not this scope
        _record(mem, 'army_count_check', month=scope[-1],
                observed_metric={'planned_refill': (shop or {}).get('soldiers')},
                reason='兵数未確認のため補充予定0でも実人数を確認する。確認入力では購入しない')
    if state.get('stage') == 'measured' and not known:
        state.update(stage='closing', result='unverified')
        if shop:
            shop['soldiers_done'] = True
        _record(mem, 'army_count_unverified', month=scope[-1],
                reason='確認後の実兵数が未読になったため補充を止める。確認予算を増やさない')
    if (state.get('stage') == 'measured' and state.get('refill_requested') is True
            and not intro and month_menu_ready(screen) and _month_menu_quiet(screen)):
        if _army_count_increment(state, 'refill_waits', 4) >= 4:
            state.update(stage='closing', result='unverified')
            if shop:
                shop['soldiers_done'] = True
            return [pad('b')]
        return []  # the A that opens the editor must not be sent again
    if state.get('stage') in ('measured', 'unverified'):
        return None
    if state.get('stage') == 'closing':
        if month_menu_ready(screen) and _month_menu_quiet(screen):
            state['stage'] = ('measured' if state.get('result') == 'measured'
                              and _soldier_count_current(mem, screen.header) else 'unverified')
            return None
        return [pad('b')] if _army_count_increment(state, 'exit_presses', 4) <= 3 else []
    if state.get('stage') == 'quantity_requested':
        # The real stock question needs one A to reach the digit editor.
        # Repeating that A on a delayed editor could confirm its default 01.
        if _army_count_increment(state, 'waits', 4) >= 4:
            state.update(stage='closing', result='unverified')
            return [pad('b')]
        return []
    if _soldier_count_current(mem, screen.header):
        if intro:
            state.update(stage='quantity_requested', result='measured')
            return [pad('a')]  # native g7417458->7460: opens editor without payment
        state.update(stage='closing', result='measured')
        return [pad('b')]  # the count dialogue/quantity editor is still open
    if _army_count_increment(state, 'observations', 24) >= 24:
        state.update(stage='closing', result='unverified')
        _record(mem, 'army_count_unverified', month=scope[-1],
                reason='兵数の確認が有限上限に達したため購入せず戻る。未読を0や充足と扱わない')
        return [pad('b')]
    if state.get('stage') == 'requested':
        if _army_count_increment(state, 'waits', 4) >= 4:
            state.update(stage='closing', result='unverified')
            return [pad('b')]
        return []  # do not repeat A over a still-visible parent menu
    move = _month_menu_move(screen, mem, 'へいしほじゅう')
    if move == 'here':
        state['stage'] = 'requested'
        return [pad('a')]
    return move if isinstance(move, list) else [move] if move else []


def _month_menu_move(screen: Screen, mem, label: str):
    move = guarded_menu_to(screen, mem, label, key='month_nav')
    if move == []:
        return _month_text_advance(screen, mem, label) or move
    return move


def _month_text_advance(screen: Screen, mem, label: str):
    """A message box over the month menu takes the input; only A closes it.

    g-1-11 (2026-10-11 13:0x): after the おねだり gift was bought the month
    menu stayed behind a message box. Down and the cancel B changed nothing,
    the guarded navigation then held for 150 s and the bot fallback's B kept
    the screen frozen. The normal month menu has no text in its dialogue
    window (y>=175), so once navigation is stuck, unread plain text there is
    advanced with A - never a うむッ/いかんッ choice, never inside a tracked
    egg/recruit/building flow, and a bounded number of times per message.
    """
    body = _month_dialog_body(screen)
    # Any unread glyph in the window may be a half-read うむッ!/いかんッ! choice
    # (the known-only body would hide it): only fully read text is plain.
    unread = _month_dialog_unread(screen)
    if (not body or unread or mem.get('month_sub') or screen.has('うむッ') or screen.has('いかんッ')
            or body in (RECRUIT_INTRO, RECRUIT_GOODBYE) or screen.has('じゅうじキー')):
        return None
    key = [mem.get('month'), body[-40:]]
    state = mem.get('month_text_advance')
    if not isinstance(state, dict) or state.get('key') != key:
        state = mem['month_text_advance'] = {'key': key, 'presses': 0}
    if state['presses'] >= MONTH_TEXT_ADVANCE_LIMIT:
        return None
    state['presses'] += 1
    mem.pop('month_nav', None)   # re-read the menu once the box is gone
    _record(mem, 'month_text_advance', screen=screen.kind, choice=label,
            observed_metric={'presses': state['presses'], 'text': body[-40:]},
            reason='月初メニューの前面に残った会話文でカーソルが動かないため、選択肢のない文だけAで送る')
    return [pad('a')]


def _recalc_soldiers(screen, mem, shop):
    """Adjusted plans: size the refill from the gold actually left after the
    merchant (1G per soldier, as in the base budget plan). Unreadable gold
    holds input rather than guessing."""
    gold = (screen.header or {}).get('gold')
    if type(gold) is not int:
        return False
    available = _soldier_spendable(mem, shop, screen.header)
    shop['soldiers'] = min(shop.get('soldiers_target', 0), available if available is not None else 0)
    shop['soldiers_done'] = shop['soldiers'] == 0
    shop['soldiers_recalculated'] = True
    _record(mem, 'soldier_plan_recalc', chart_step='adjusted-month',
            strategy_variant=shop.get('variant', 'chart_adjusted'), month=shop.get('key'),
            expected_metric={'soldiers_target': shop.get('soldiers_target')},
            observed_metric={'gold_after_merchant': gold, 'soldiers': shop['soldiers']},
            reason='商人での実購入後の所持金から兵士補充数を再計算')
    return True


MONTH_EXIT_REFUSAL_LIMIT = 3   # refused month exits per month before leaving anyway

def month_step(screen: Screen, mem):
    shop = _plan(mem, screen.header)
    body = _month_dialog_body(screen)
    if shop:
        _refresh_egg_priority(mem, shop, screen.header)
        _zero_army_refill(mem, shop, screen.header)
        _prioritise_recruit(mem, shop, (screen.header or {}).get('gold'))
        _limit_soldier_refill(mem, shop, screen.header)
    if body == RECRUIT_GOODBYE:
        _record(mem, 'month_recruit_goodbye', reason='実測した募集終了文を閉じて本メニューへ戻る')
        return [pad('a')]
    if (body == RECRUIT_INTRO
            and all(UNKNOWN not in line.span(8, 248) for line in screen.lines if line.y in (183, 199))):
        # g496: the real background hand remained visible, so a hand alone
        # could neither prove menu return nor rule out this measured dialogue.
        gold = (screen.header or {}).get('gold')
        if shop and not _recruit_egg_ready(mem, shop):
            if shop.get('recruit') != 'deferred_egg':
                shop['recruit'] = 'deferred_egg'
                _record(mem, 'recruit_deferred_egg', month=shop.get('key'),
                        reason='募集導入の復帰でも回復未確認のまま新たな募集費を払わず戻る')
            return [pad('b')]
        spare, evidence = _recruit_dialog_cash_ready(mem, shop, gold) if shop else (None, None)
        if shop and spare is not True:
            _defer_recruit_cash(mem, shop, gold, evidence)
            return [pad('b')]
        if (shop and shop.get('recruit') == 'unverified' and type(gold) is int
                and gold >= RECRUIT_COST and (shop.get('recruit_priority')
                                             or shop.get('soldiers', 0) >= SOLDIER_ENOUGH or _soldiers_enough(mem, shop, screen.header))):
            mem['month_sub'] = {'kind': 'recruit', 'gold_before': gold, 'presses': 0,
                                'key': shop['key'], 'left_menu': False}
            shop['recruit'] = 'opened'
            _record(mem, 'month_sub_resumed', choice='しょうぐんぼしゅう',
                    reason='実測した募集導入文と費用条件が一致したため失われた会話追跡を再開')
            return month_sub_step(screen, mem)
        return []
    if screen.has('じゅうじキー'):
        return quantity_step(screen, mem, soldiers=True)
    if ((mem.get('shop') or {}).get('soldier_receipt') or {}).get('status') == 'pending':
        # observe_events settles the receipt before another paid command can
        # change the balance. A few unchanged menu frames are not a receipt.
        return []
    if screen.has('うむッ') and screen.has('いかんッ'):
        # "よろしいですかな?" after も〜おしまい!: confirm only our own exit.
        choice = 'うむッ!' if mem.get('month_exit') or mem.get('month_sub') else 'いかんッ!'
        scope = _soldier_count_scope(mem, screen.header)
        check = mem.get('army_count_check')
        check = check if isinstance(check, dict) else {}
        unchecked = (not _soldier_count_current(mem, screen.header)
                     and (check.get('scope') != scope or check.get('stage') not in ('measured', 'unverified')))
        if (scope is not None and mem.get('month_exit') and not mem.get('month_sub')
                and (unchecked or (shop and not shop.get('soldiers_done')))):
            # g747 2-4 (18:40-19:45): the refill never reached its quantity
            # screen, so every exit was refused and the menu looped for an
            # hour. Refuse a bounded number of times per month, then leave.
            month_key = (shop or {}).get('key') or str(scope)
            refusals = mem.get('month_exit_refusals')
            refusals = refusals if isinstance(refusals, dict) and refusals.get('key') == month_key else {
                'key': month_key, 'count': 0}
            refusals['count'] = int(refusals.get('count') or 0) + 1
            mem['month_exit_refusals'] = refusals
            if refusals['count'] <= MONTH_EXIT_REFUSAL_LIMIT:
                choice = 'いかんッ!'
                mem['month_exit'] = False
            else:
                if shop:
                    shop['soldiers_done'] = True
                _record(mem, 'month_exit_refusal_bound', month=month_key,
                        observed_metric={'refusals': refusals['count'] - 1, 'unchecked': bool(unchecked),
                                         'soldiers_planned': (shop or {}).get('soldiers')},
                        reason='兵士補充・兵数確認が月初メニュー内で完了しないため、上限回数で打ち切って月を進める')
        move = _month_menu_move(screen, mem, choice)
        if isinstance(move, list):
            return move
        if move == 'here':
            if choice == 'うむッ!':
                mem['month_exit'] = False
            _record(mem, 'month_confirm', choice=choice, reason='月初メニュー終了の確認')
            return [pad('a')]
        return [move] if move else [pad('b')]
    count_check = _month_army_count_check(screen, mem, shop)
    if count_check is not None:
        return count_check
    if shop and not shop['merchant_done'] and shop['items']:
        move = _month_menu_move(screen, mem, 'しょうにん')
        if isinstance(move, list):
            return move
        return [pad('a')] if move == 'here' else [move] if move else []
    if (mem.get('month_sub', {}).get('kind') == 'egg'
            and screen.has('おはらいのひつような') and screen.has('たまごはありませんぞ')):
        mem.pop('month_sub')
        from ..os_layer.roster import dirty_status
        dirty_status(mem, _egg_recovery_targets(mem), fields=('egg',))
        mem.pop('egg_recheck', None)
        _egg_quantities_unknown(mem)  # reobserve each owner's quantity
        if shop:
            shop['egg'] = 'not_needed'
            if shop.get('egg_priority') or shop.get('egg_before_recruit'):
                shop['reserve'] = 0
        _record(mem, 'egg_recover_not_needed', reason='ゲームが回復不要と表示したため支払わず説明を閉じる')
        return [pad('a')]
    # Finish the tracked egg/recruit flow before opening another one;
    # after an egg-cost deferral recruitment is still pending here.
    if mem.get('month_sub') and not _finish_month_sub(screen, mem, shop):
        return []
    if shop:
        _limit_soldier_refill(mem, shop, screen.header)
    if shop:
        _prepare_recruit_egg_check(mem, shop, (screen.header or {}).get('gold'))
    if (shop and shop.get('recruit_priority') and not shop.get('recruit_measured_budget')
            and not mem.get('month_sub')):
        gold = (screen.header or {}).get('gold')
        if type(gold) is not int:
            return []
        shop['recruit_reserve'] = (0 if shop.get('recruit') == 'done' else
                                   min(RECRUIT_COST, max(0, gold - _month_held_reserve(shop) - WAGE_RESERVE)))
        if not shop.get('soldiers_done'):
            shop['soldiers'] = min(shop.get('soldiers', 0),
                                   max(0, gold - _month_held_reserve(shop)
                                       - shop['recruit_reserve'] - WAGE_RESERVE))
            shop['soldiers_done'] = shop['soldiers'] == 0
        shop['recruit_measured_budget'] = True
    if (shop and shop.get('soldiers_from_gold') and not shop.get('soldiers_recalculated')
            and not shop['soldiers_done']):
        if not _recalc_soldiers(screen, mem, shop):
            return []
    if shop and _egg_before_soldiers(shop):
        extra = _month_extra(screen, mem, shop, egg_only=True)
        if extra is not None:
            return extra
        # Too little money, unreadable recovery, or an unverified attempt:
        # preserve the recovery money instead of spending it on more troops.
        shop['soldiers'] = 0
        shop['soldiers_done'] = True
    if (shop and shop.get('recruit_priority')
            and shop.get('recruit') in ('check', 'pending')):
        extra = _month_extra(screen, mem, shop, recruit_only=True)
        if extra is not None:
            return extra
    if mem.get('month_sub') and mem['month_sub'].get('kind') == 'recruit':
        if not _finish_month_sub(screen, mem, shop):
            return []
    if shop and not shop['soldiers_done']:
        move = _month_menu_move(screen, mem, 'へいしほじゅう')
        if isinstance(move, list):
            return move
        if move == 'here':
            check = mem.get('army_count_check')
            if (isinstance(check, dict) and check.get('stage') == 'measured'
                    and check.get('scope') == _soldier_count_scope(mem, screen.header)):
                check['refill_requested'] = True
        return [pad('a')] if move == 'here' else [move] if move else []
    if mem.get('month_sub') and not _finish_month_sub(screen, mem, shop):
        # g462 18:06:04: the menu frame still predates the game's reaction to
        # the A press that opened the sub. Pressing anything here (も〜おしまい!
        # navigation) races the flow that is starting, so hold instead.
        return []
    extra = _month_extra(screen, mem, shop)
    if extra is not None:
        return extra
    if shop and not shop.get('closed') and _await_month_roster_scan(mem, shop):
        return []
    if shop and not shop.get('closed'):
        shop['closed'] = True
        _record(mem, 'month_done', month=shop['key'], gold_after=(screen.header or {}).get('gold'),
                observed_metric={'bought': shop.get('bought', []),
                                 'gold_start': shop['gold_start'],
                                 'gold_end': (screen.header or {}).get('gold')},
                reason='月一の購入を終了')
    move = _month_menu_move(screen, mem, 'も〜おしまい!')
    if isinstance(move, list):
        return move
    if move == 'here':
        mem['month_exit'] = True
        return [pad('a')]
    return [move] if move else []


def _month_extra(screen, mem, shop, *, recruit_only=False, egg_only=False):
    """Recruit before soldiers when short; otherwise keep the old extras order."""
    if not shop:
        return None
    gold = (screen.header or {}).get('gold')
    if not _stop_unneeded_recruit(mem, shop):
        _stop_uneconomic_recruit(mem, shop)
    _prepare_recruit_egg_check(mem, shop, gold)
    for sub, label, cost in (('egg', 'たまごのかいふく', shop.get('reserve') or EGG_RECOVER_COST),
                             ('recruit', 'しょうぐんぼしゅう', RECRUIT_COST)):
        if (recruit_only and sub != 'recruit') or (egg_only and sub != 'egg'):
            continue
        if sub == 'egg' and (shop.get('egg_priority') or shop.get('egg_before_recruit')):
            # Before the refill the screen's own quote is the only exact
            # price: one recovery's fee is the probe (g498: estimate 150G,
            # quote 50G), and an unread recheck name probes at nothing.
            cost = min(_egg_estimate(shop), EGG_RECOVER_COST)
        low_egg = sub == 'egg' and any(observed_uses((mem.get('egg_uses') or {}).get(n)) in (0, 1)
                                     for n in _egg_recovery_targets(mem))
        if low_egg:
            cost = min(_egg_estimate(shop), EGG_RECOVER_COST)
        status = shop.get(sub)
        if status not in ('pending', 'check'):
            continue
        if sub == 'recruit' and not _recruit_egg_ready(mem, shop):
            # A recovery already skipped as unaffordable spends nothing this
            # month; with generals short the recruit keeps its own cash gate
            # (g684 2-9: egg skipped at 75G deferred recruitment again).
            if shop.get('egg') in ('pending', 'check'):
                actions = _month_extra(screen, mem, shop, egg_only=True)
                if actions is not None:
                    return actions
            _defer_recruit_for_egg(mem, shop)
            continue
        if sub == 'egg' and status == 'check':
            # After the soldiers: recover the eggs only from what is left.
            cost = _egg_estimate(shop)
            floor = WAGE_RESERVE
            if NAME in _depleted_eggs(mem):
                # Hero-only recovery (g604): 50G over this month's real payroll.
                cost, floor = min(cost, EGG_RECOVER_COST), _hero_egg_wage_floor(mem)
            elif any(observed_uses((mem.get('egg_uses') or {}).get(n)) in (0, 1)
                     for n in _egg_recovery_targets(mem)):
                cost, floor = min(cost, EGG_RECOVER_COST), _egg_recovery_wage_floor(mem)
            if type(gold) is not int or gold < cost + floor + shop.get('hero_repair_reserve', 0):
                shop[sub] = 'skipped'
                _record(mem, 'egg_recover_skip', month=shop.get('key'), gold=gold,
                        observed_metric={'cost': cost, 'gold': gold},
                        reason='兵士補充の後の残金が卵の回復費と賃金リザーブに足りないため見送る')
                continue
        elif sub == 'recruit':
            spare, evidence = _recruit_spare_after_necessities(mem, shop, gold)
            if spare is not True:
                _defer_recruit_cash(mem, shop, gold, evidence)
                continue
            # Owner rule (2026-09-27): recruit only when the soldiers got
            # their full 99 and the fee plus the wage reserve is still left.
            floor = _recruit_cash_floor(shop)
            if (type(gold) is not int or (not shop.get('recruit_priority') and shop.get('soldiers', 0) < SOLDIER_ENOUGH
                    and not _soldiers_enough(mem, shop, screen.header))
                    or floor is None or gold < floor):
                if (shop.get('recruit_priority') and type(gold) is int
                        and gold >= RECRUIT_COST + WAGE_RESERVE + _house_repair_reserve(shop)
                        and shop.get('egg') in ('pending', 'check')):
                    # g498: estimate 150G, actual recovery only 50G; the
                    # early check must not permanently skip recruitment.
                    if not shop.get('recruit_deferred_egg'):
                        shop['recruit_deferred_egg'] = True
                        _record(mem, 'recruit_deferred_egg', month=shop.get('key'),
                                observed_metric={'gold': gold, 'egg_reserve': shop.get('reserve')},
                                reason='卵回復費は見積もりのため、回復後の実残金で募集を再判定する')
                    continue
                shop[sub] = 'skipped'
                _record(mem, 'recruit_skip', month=shop.get('key'), gold=gold,
                        observed_metric={'soldiers': shop.get('soldiers'), 'gold': gold},
                        reason=('募集費と卵回復・賃金リザーブが足りないため資金を温存して次月へ待つ'
                                if shop.get('recruit_priority') else
                                '必要な兵士の補充と賃金リザーブの後に募集費が残っていないため将軍を募集しない'))
                continue
        elif (type(gold) is not int or gold < cost + shop.get('hero_repair_reserve', 0)
              + (_egg_recovery_wage_floor(mem) if low_egg else
                 WAGE_RESERVE if shop.get('hero_repair_reserve') or shop.get('egg_priority')
                 or shop.get('egg_before_recruit') else 0)):
            shop[sub] = 'skipped'
            _record(mem, 'egg_recover_skip', month=shop.get('key'), gold=gold,
                    reason='所持金が卵の回復費に足りないため見送る')
            continue
        move = _month_menu_move(screen, mem, label)
        if isinstance(move, list):
            return move
        if move == 'here':
            shop[sub] = 'opened'
            mem['month_sub'] = {'kind': sub, 'gold_before': gold, 'presses': 0, 'key': shop.get('key'),
                                'left_menu': False}
            if sub == 'recruit':
                mem['month_sub']['generals_before'] = sorted({
                    g for gs in (mem.get('garrison') or {}).values() for g in gs or ()})
            _record(mem, 'month_sub_open', month=shop.get('key'), gold=gold, choice=label,
                    reason=f'{label}を選択')
            return [pad('a')]
        if move is None:
            if sub == 'egg' and shop.get('egg_before_recruit'):
                waits = int(shop.get('egg_menu_wait') or 0) + 1
                shop['egg_menu_wait'] = waits
                if waits <= 6:
                    return []  # a blinking/unread hand is not an absent recovery menu
                shop[sub] = 'unverified'
                _record(mem, 'egg_recover_menu_unconfirmed', month=shop.get('key'),
                        observed_metric={'waits': waits},
                        reason='回復欄・カーソルを有限回待っても読めないため回復を未確認とし募集を行わない')
                continue
            if sub == 'recruit' and shop.get('recruit_priority'):
                # g508 1-8: the first menu/cursor reading was incomplete;
                # skipping immediately sent all optional money to soldiers.
                waits = int(shop.get('recruit_menu_wait') or 0) + 1
                shop['recruit_menu_wait'] = waits
                if waits <= 6:
                    if waits == 1:
                        _record(mem, 'recruit_menu_wait', month=shop.get('key'),
                                reason='将軍不足時の募集欄・カーソルを有限回待ち、兵士補充を先に始めない')
                    return []
                shop[sub] = 'unverified'
                _record(mem, 'recruit_menu_unconfirmed', month=shop.get('key'),
                        observed_metric={'waits': waits},
                        reason='募集欄を有限回待っても読めないため実行を未確認とし、募集費は温存する')
                continue
            shop[sub] = 'skipped'
            _record(mem, 'situation_held', screen=screen.kind, choice=label,
                    reason=f'月一メニューに{label}が読めないため見送る')
            continue
        if sub == 'recruit':
            shop.pop('recruit_menu_wait', None)
        elif sub == 'egg':
            shop.pop('egg_menu_wait', None)
        return [move]
    return None if recruit_only or egg_only else _month_chikujou(screen, mem, shop)
CHIKUJOU_SUB_LIMIT = 64        # bounded castle navigation, quote, result and return
CHIKUJOU_WAIT_LIMIT = 6        # no repeated A while a selection/result frame persists


MONTH_ROSTER_WAIT = 4   # menu observations held for the free roster scan to start


def _await_month_roster_scan(mem, shop):
    """Hold the month open briefly so a deferred roster recruit can be read.

    g684 2-10 15:24:44: recruit was deferred_roster, the egg sub ended and
    month_done fired in the same observation, before the monthly roster
    scan (house.step) could start; the scan ran on the field instead and the
    month passed without recruitment at 103G. Bounded: the month still
    closes if the scan never starts.
    """
    if shop.get('recruit') != 'deferred_roster' or mem.get('month_sub'):
        return False
    attempts = mem.get('recruit_month_scan_attempts') or {}
    if attempts.get('scope') == [mem.get('chapter'), mem.get('month')] and attempts.get('count', 0) >= 1:
        return False
    waits = int(shop.get('roster_scan_wait') or 0)
    if waits >= MONTH_ROSTER_WAIT:
        return False
    shop['roster_scan_wait'] = waits + 1
    if waits == 0:
        _record(mem, 'month_roster_scan_wait', month=shop.get('key'),
                reason='募集が一覧確認待ちのため、月一メニューを閉じる前に一覧確認の開始を短く待つ')
    return True


def _chikujou_wait(screen, mem, sub, reason):
    sub['build_wait'] = int(sub.get('build_wait') or 0) + 1
    if sub['build_wait'] < CHIKUJOU_WAIT_LIMIT:
        return []
    sub['declined'] = True
    sub['aborted'] = True
    _record(mem, 'chikujou_held', deviation_reason=reason,
            observed_metric={'castle': sub.get('chosen'), 'text': screen.text[-60:]},
            reason='築城の画面遷移を有限回待っても確認できないため、追加確定せずBで戻る')
    return [pad('b')]


def _chikujou_scroll(screen, mem, sub):
    """Reveal off-screen holdings in the observed four-row castle list.

    g684: all four visible castles were already tried, but ゴーメン and
    スペンソニア remained below them. A missing menu_to target is not the end
    of this list. Move down only from a named row in the real list, and
    bound both ignored inputs and an entire traversal (including wrap).
    """
    chapter = mem.get('chapter') or 1
    known = {STATUS_NAMES.get(c, c) for c in chart.CASTLE_NAMES.get(chapter, ())}
    rows = sorted((x, y, w) for x, y, w in _options(screen) if w in known)
    cur = _current(screen)
    if (len(rows) != 4 or len({r[2] for r in rows}) != 4
            or len({r[0] for r in rows}) != 1
            or any(b[1] - a[1] != 16 for a, b in zip(rows, rows[1:]))
            or cur not in rows):
        return _chikujou_wait(screen, mem, sub, 'castle_list_unread')
    signature = [list(r) for r in rows] + [list(cur)]
    state = sub.setdefault('build_scroll', {})
    stalled = int(state.get('stalled') or 0) + 1 if state.get('signature') == signature else 0
    presses = int(state.get('presses') or 0)
    if stalled >= MENU_NAV_PRESS_LIMIT or presses >= len(known) + 4:
        sub['declined'] = True
        sub['aborted'] = True
        _record(mem, 'chikujou_held', deviation_reason='castle_scroll_no_progress',
                observed_metric={'visible': [r[2] for r in rows], 'scrolls': presses,
                                 'stalled': stalled},
                reason='築城一覧の移動が効かないか探索上限に達したため、城を推測して確定せず戻る')
        return [pad('b')]
    state.update(signature=signature, stalled=stalled, presses=presses + 1)
    sub['build_wait'] = 0
    _record(mem, 'chikujou_scroll',
            observed_metric={'visible': [r[2] for r in rows], 'selected': cur[2],
                             'scrolls': presses + 1},
            reason='未試行の所有城が画面外に残るため、4行の築城一覧を下へ送って確認する')
    return [pad('down')]


def _month_chikujou(screen, mem, shop):
    """Owner (2026-09-29): with money to spare, ちくじょう raises a castle's level.

    wikiwiki 城: a defender's egg monster gains +level defense and speed and a
    defending general +level charge speed. Measured (isolated probe, chapter
    2): ちくじょう lists our castles (the first row is the home castle),
    「NGかかりますがよろしいですかな」 asks うむッ!/いかんッ!. gcgx info.html:
    one level per castle per month. Owner (2026-10-04): upgrade other holdings
    too when affordable. Verify each result and actual debit in the native
    castle list, then continue without closing it when another castle is affordable.
    """
    from ..os_layer.roster import run_identity
    if shop.get('chikujou') != 'check':
        return None
    tried = shop.setdefault('chikujou_tried', [])
    if not any(c not in tried for c in _chikujou_candidates(mem)):
        shop['chikujou'] = 'done' if shop.get('chikujou_upgrades') else 'skipped'
        return None
    gold = (screen.header or {}).get('gold')
    reserve = _chikujou_reserve(mem, shop)
    if not _chikujou_affordable(gold, reserve):
        shop['chikujou'] = 'done' if shop.get('chikujou_upgrades') else 'skipped'
        return None
    # Bounded like every other month row (g684 2-8 14:52: a soldier-count
    # dialogue stayed in front of the menu and the unguarded walk pressed
    # down at an unmoving hand every observation).
    move = _month_menu_move(screen, mem, 'ちくじょう')
    if isinstance(move, list):
        return move
    if move is None:
        shop['chikujou'] = 'skipped'
        return None
    if move != 'here':
        return [move]
    shop['chikujou'] = 'opened'
    mem['month_sub'] = {'kind': 'chikujou', 'gold_before': gold, 'presses': 0, 'key': shop.get('key'),
                        'left_menu': False, 'chikujou_version': 2, 'rows_tried': list(tried),
                        'chikujou_identity': run_identity(mem.get('_run_identity')),
                        'chikujou_chapter': mem.get('chapter')}
    _record(mem, 'month_sub_open', month=shop.get('key'), gold=gold, choice='ちくじょう',
            observed_metric={'reserve': reserve, 'tried': list(tried)},
            reason='必要資金を残して、当月まだ増築していない所有城の防衛力を上げる')
    return [pad('a')]


def _chikujou_visible_maxed(screen, mem):
    """Read level 5 only from this native castle-list window, never a saved cap."""
    options = _options(screen)
    known = chart.CASTLE_NAMES.get(mem.get('chapter') or 1, ())
    rows = sorted((x, y, word) for x, y, word in options if word in known)
    if (not 1 <= len(rows) <= 4 or len({r[2] for r in rows}) != len(rows)
            or any(x != 168 or y != 39 + i * 16 for i, (x, y, _) in enumerate(rows))
            or _current(screen) not in rows):
        return []
    levels = []
    for _, y, name in rows:
        values = [word for x, row_y, word in options if x == 232 and row_y == y]
        if len(values) != 1 or values[0] not in ('1', '2', '3', '4', '5'):
            return []
        if values[0] == '5':
            levels.append(name)
    return levels


def _chikujou_continue_list(screen, mem, sub):
    """Continue only after this castle's result and exact native-list debit."""
    from ..os_layer.roster import run_identity
    shop = mem.get('shop') or {}
    identity = run_identity(mem.get('_run_identity'))
    key = sub.get('key')
    header = screen.header or {}
    known = {STATUS_NAMES.get(c, c) for c in chart.CASTLE_NAMES.get(mem.get('chapter') or 1, ())}
    rows = sorted((x, y, word) for x, y, word in _options(screen) if word in known)
    if (identity is None or identity != run_identity(sub.get('chikujou_identity'))
            or sub.get('chikujou_chapter') != mem.get('chapter')
            or key is None or key != mem.get('month') or key != shop.get('key')
            or any(type(header.get(k)) is not int for k in ('year', 'month', 'gold'))
            or _month_key(header) != key or sub.get('payment_interrupted')
            or 'ぞうちくなさいます' not in screen.text
            or screen.has('うむッ') or screen.has('いかんッ')
            or not 1 <= len(rows) <= 4 or len({r[2] for r in rows}) != len(rows)
            or any(x != 168 or y != 39 + i * 16 for i, (x, y, _) in enumerate(rows))
            or _current(screen) not in rows):
        return None
    cost, before, gold = sub.get('quoted_cost'), sub.get('confirm_gold'), header['gold']
    if not (sub.get('confirm_sent') and sub.get('upgraded') and sub.get('chosen')
            and type(cost) is int and cost >= 0 and type(before) is int and before - gold == cost):
        return _chikujou_wait(screen, mem, sub, 'list_payment_unconfirmed')
    tried = set(sub.get('rows_tried') or []) | set(shop.get('chikujou_tried') or []) | {sub['chosen']}
    if (not _chikujou_affordable(gold, _chikujou_reserve(mem, shop))
            or not any(c not in tried for c in _chikujou_candidates(mem))):
        return None  # one final B returns for the existing final receipt
    presses = sub.get('presses', 0)
    sub['receipt_on_list'] = True
    if not _finish_month_sub(screen, mem, shop):
        return []
    if shop.get('chikujou') != 'check':
        return [pad('b')]
    next_sub = {'kind': 'chikujou', 'gold_before': gold, 'presses': presses,
                'key': key, 'left_menu': True, 'chikujou_version': 2,
                'rows_tried': list(shop.get('chikujou_tried') or []),
                'chikujou_identity': identity, 'chikujou_chapter': mem.get('chapter')}
    shop['chikujou'] = 'opened'
    mem['month_sub'] = next_sub
    _record(mem, 'chikujou_continue', month=key,
            observed_metric={'gold': gold, 'tried': next_sub['rows_tried'], 'presses': presses},
            reason='実一覧で増築結果と実費減少を照合したため、月メニューへ戻らず次の城を選ぶ')
    return _chikujou_step(screen, mem, next_sub)


def _chikujou_step(screen, mem, sub):
    from ..os_layer.roster import run_identity
    if (sub.get('chikujou_identity') is not None and (
            run_identity(sub['chikujou_identity']) != run_identity(mem.get('_run_identity'))
            or sub.get('chikujou_chapter') != mem.get('chapter'))):
        sub['declined'] = True
        sub['payment_interrupted'] = True
        return [pad('b')]
    text = screen.text
    header = screen.header or {}
    current = (_month_key(header) if header.get('year') is not None
               and header.get('month') is not None else None)
    key = sub.get('key')
    if key and any(value is not None and value != key for value in
                   (current, mem.get('month'), (mem.get('shop') or {}).get('key'))):
        if not sub.get('stale_month'):
            sub['stale_month'] = True
            _record(mem, 'chikujou_declined', month=key,
                    observed_metric={'current_month': current, 'shop_month': (mem.get('shop') or {}).get('key')},
                    reason='前月の築城状態が残っているため支払いを確定せずBで戻る')
        sub['declined'] = True
        return [pad('b')]
    if sub.get('chikujou_version') != 2:
        # Old memory wrote quoted_cost immediately before A, without a latch.
        # It may already have paid: never reissue that confirmation on hotload.
        sub['chikujou_version'] = 2
        if sub.get('quoted_cost') is not None:
            sub['confirm_sent'] = True
    if sub.get('aborted'):
        return [pad('b')]
    if sub.get('upgraded'):
        if 'になりましたぞ' in text:
            return _chikujou_wait(screen, mem, sub, 'result_frame_unchanged')
        continuation = _chikujou_continue_list(screen, mem, sub)
        return continuation if continuation is not None else [pad('b')]
    if 'になりましたぞ' in text:
        chosen = sub.get('chosen')
        if not (sub.get('confirm_sent') and chosen
                and f'{chosen}じょうのレベルが' in text):
            return _chikujou_wait(screen, mem, sub, 'result_castle_unconfirmed')
        sub['upgraded'] = True
        sub['build_wait'] = 0
        return [pad('a')]
    if sub.get('confirm_sent'):
        # A paid result may have been missed. A readable list permits a safe
        # return for receipt checking, never another selection/confirmation.
        if 'ぞうちくなさいます' in text or 'おかねがたりませんぞ' in text:
            return [pad('b')]
        return _chikujou_wait(screen, mem, sub, 'confirmation_result_unconfirmed')
    if screen.has('うむッ') and screen.has('いかんッ'):
        if sub.get('decline_sent'):
            return [pad('b')]
        quote = re.search(r'(\d+)Gかかりますが', text)
        gold = (screen.header or {}).get('gold')
        cost = int(quote[1]) if quote else None
        reserve = _chikujou_reserve(mem, mem.get('shop') or {})
        if (sub.get('declined') or cost is None or type(gold) is not int
                or reserve is None or gold - cost < reserve):
            if (not sub.get('declined') and cost is not None and type(gold) is int
                    and reserve is not None and sub.get('chosen')
                    and f"{sub['chosen']}じょうですな" in text):
                sub['cost_declined'] = True  # this castle's price only
            sub['declined'] = True
            move = menu_to(screen, 'いかんッ!')
            if not sub.get('decline_recorded'):
                sub['decline_recorded'] = True
                _record(mem, 'chikujou_declined',
                        observed_metric={'cost': cost, 'gold': gold, 'reserve': reserve},
                        reason='築城の実費が不明か必要資金を割るため、支払わず見送る')
            if move == 'here':
                sub['decline_sent'] = True
            return [pad('a')] if move == 'here' else [move] if move else [pad('b')]
        chosen = sub.get('chosen')
        if not (chosen in _chikujou_candidates(mem) and f'{chosen}じょうですな' in text):
            return _chikujou_wait(screen, mem, sub, 'quote_castle_unconfirmed')
        move = menu_to(screen, 'うむッ!')
        if move == 'here':
            sub['quoted_cost'] = cost
            sub['confirm_gold'] = gold
            sub['confirm_sent'] = True
            sub['build_wait'] = 0
            _record(mem, 'chikujou_confirm',
                    observed_metric={'castle': chosen, 'cost': cost, 'gold': gold, 'reserve': reserve},
                    reason=f'{chosen}を{cost}Gで増築し、実際の支払いを照合して次の城へ進む')
            return [pad('a')]
        return [move] if move else _chikujou_wait(screen, mem, sub, 'quote_cursor_unread')
    if sub.get('declined'):
        return [pad('b')]
    if 'これいじょう' in text or 'しょうぐんがおりませなんだ' in text:
        # A refusal applies to the chosen castle, not every castle. Never
        # infer a permanent level cap (it can also mean upgraded this month).
        chosen = sub.get('chosen')
        if (sub.get('rejected_frame') == text
                and chosen not in _chikujou_visible_maxed(screen, mem)):
            if chosen:
                # Input may not have taken effect yet. Do not attribute the
                # previous castle's unchanged refusal to our next candidate.
                return _chikujou_wait(screen, mem, sub, 'refusal_frame_unchanged')
        elif chosen:
            tried = sub.setdefault('rows_tried', [])
            if chosen not in tried:
                tried.append(chosen)
            sub['rejected_frame'] = text
            sub.pop('chosen', None)
            sub.pop('quoted_cost', None)
            sub['build_wait'] = 0
            _record(mem, 'chikujou_castle_unavailable',
                    observed_metric={'castle': chosen},
                    reason='選択した城が増築不可のため、その城を今回の候補から外して次を探す')
        elif 'ぞうちくなさいます' in text and screen.hand is not None:
            # g684 1-10/1-11 13:11:05: reopening after アルマムーン's upgrade
            # showed the refusal line above a readable castle list with no
            # selection of ours. Declining there ended every month after the
            # home castle; pick the next untried castle from the list instead.
            if not sub.get('leftover_refusal_recorded'):
                sub['leftover_refusal_recorded'] = True
                _record(mem, 'chikujou_leftover_refusal',
                        observed_metric={'tried': list(sub.get('rows_tried') or []),
                                         'text': text[-60:]},
                        reason='前回の増築不可の文が残った一覧のため、未試行の所有城を選び直す')
        else:
            # Lost selection context cannot identify which castle was refused.
            sub['declined'] = True
            return [pad('b')]
    else:
        sub.pop('rejected_frame', None)
        sub.pop('rejected_wait', None)
    if 'ぞうちくなさいます' in text:
        # g438 03:37: the first row was ジョンリギ with nobody inside
        # (「しょうぐんがおりませなんだ」) and the upgrade never happened. Pick the
        # home castle, else a castle a general is known to hold.
        if sub.get('chosen'):
            return _chikujou_wait(screen, mem, sub, 'castle_selection_unconfirmed')
        tried = sub.setdefault('rows_tried', [])
        maxed = [c for c in _chikujou_visible_maxed(screen, mem) if c not in tried]
        if maxed:
            # gcgx info.html / rrpg basic.html: maximum level 5. An unchanged
            # refusal is otherwise indistinguishable from ignored input.
            # Skip observed capped rows before A, only in this month's substate.
            tried.extend(maxed)
            _record(mem, 'chikujou_max_level_seen',
                    observed_metric={'castles': maxed, 'level': 5},
                    reason='実一覧でレベル5を確認した城は当月の選択を省き、未表示の増築候補へ進む')
        names = [c for c in _chikujou_candidates(mem) if c not in tried]
        for name in names:
            move = menu_to(screen, name)
            if move is None:
                continue
            if move == 'here':
                sub['chosen'] = name
                sub['build_wait'] = 0
                return [pad('a')]
            return [move]
        if names:
            return _chikujou_scroll(screen, mem, sub)
        sub['declined'] = True
        _record(mem, 'chikujou_declined', observed_metric={'tried': tried},
                reason='当月未試行で増築候補となる所有城がなくなったため、築城を終える')
        return [pad('b')]
    return _chikujou_wait(screen, mem, sub, 'build_screen_unclassified')


def interrupt_month_payment(mem):
    """An unrelated event cannot supply the old monthly action's fee receipt."""
    sub = mem.get('month_sub')
    if (not isinstance(sub, dict) or sub.get('kind') not in ('egg', 'recruit', 'chikujou')
            or sub.get('payment_interrupted')):
        return
    sub['payment_interrupted'] = True
    _record(mem, 'month_payment_interrupted', month=sub.get('key'),
            observed_metric={'kind': sub['kind'], 'recruit_fee_observed': _recruit_fee_observed(sub)},
            reason='別イベントを確認したため旧月初取引の残金差を支払証拠にせず、既確認の募集費だけ保持する')


def _finish_month_sub(screen, mem, shop) -> bool:
    """Back on the month menu: judge the sub-action by the gold it cost.

    Returns False while the sub must stay tracked. g462 18:06:02-18:11:11: the
    menu frame was still on screen when ちくじょう opened, so the sub ended
    before the game reacted; the castle list and its confirm then ran through
    the generic paths and the 「これいじょうのぞうちく」 exit screen repeated A
    for 300 s until the run watchdog ended the corner. Defer while the menu is
    unchanged (nothing judged yet): finish once the flow left the menu, the
    payment is readable, or MONTH_SUB_MENU_WAIT stale menu frames passed.
    """
    sub = mem.get('month_sub')
    if sub is None:
        return True
    if (sub['kind'] == 'chikujou' and shop and sub.get('key') is not None
            and shop.get('key') is not None and sub['key'] != shop['key']):
        mem.pop('month_sub')
        _record(mem, 'chikujou', month=sub.get('key'), deviation_reason='month_changed',
                observed_metric={'castle': sub.get('chosen'), 'menu_month': shop.get('key')},
                reason='保存中の築城と現在の月が異なるため、前月の費用や試行城を今月へ持ち越さない')
        return True
    gold = (screen.header or {}).get('gold')
    before = sub.get('gold_before')
    if sub['kind'] == 'chikujou':
        before = sub.get('confirm_gold', before)
    cost = sub.get('quoted_cost') if sub['kind'] in ('egg', 'chikujou') else RECRUIT_COST
    paid = (not sub.get('payment_interrupted')
            and type(cost) is int and cost > 0 and type(gold) is int and type(before) is int
            and before - gold == cost)
    if sub['kind'] == 'egg':
        receipt = (paid and (sub.get('full_selected') is True
                            or (sub.get('hero_selected') is True and cost == EGG_RECOVER_COST)
                            or (_individual_egg_selected(mem, sub) and cost == EGG_RECOVER_COST
                                and sub.get('stage') == 'recovering'))
                   and sub.get('left_menu') is True and not sub.get('aborted')
                   and all(type((screen.header or {}).get(k)) is int for k in ('year', 'month'))
                   and sub.get('key') == (shop or {}).get('key') == _month_key(screen.header))
        # g700: the paid ritual returned at 34G (134G - quoted 100G)
        # before the upper cursor appeared. Keep this flow
        # until a ready menu can confirm it; do not reserve its cost twice.
        # Only a quiet, same-month return from our confirmed ritual gets
        # this bound. A different dialogue/debit/event remains unverified.
        if (receipt and sub.get('stage') == 'recovering' and screen.kind == 'month_menu'
                and screen.hand is None and _month_menu_quiet(screen)):
            sub['receipt_wait'] = int(sub.get('receipt_wait') or 0) + 1
            if sub['receipt_wait'] < MONTH_SUB_MENU_WAIT:
                _record(mem, 'egg_recover_receipt_wait', month=sub.get('key'),
                        observed_metric={'gold_before': before, 'gold_after': gold,
                                         'quoted_cost': cost, 'waits': sub['receipt_wait']},
                        reason='卵回復の実費減少が一致したため、月一カーソルの復帰を上限付きで再観測する')
                return False
        paid = bool(receipt and month_menu_ready(screen) and _month_menu_quiet(screen))
    if sub['kind'] == 'recruit':
        # g498: the 235 ->185 fee was observed in the candidate dialogue;
        # the final menu was 183, so later deductions cannot erase that fee.
        paid = paid or _recruit_fee_observed(sub)
    if sub['kind'] == 'chikujou':
        paid = paid or (not sub.get('payment_interrupted') and type(cost) is int and cost == 0
                        and sub.get('confirm_sent') and sub.get('upgraded')
                        and type(gold) is int and gold == before)
        if (sub.get('confirm_sent') or sub.get('upgraded')) and not paid and not sub.get('payment_interrupted'):
            # The header can lag behind a result. Keep ownership of the flow
            # while waiting for the receipt; no further purchase may start.
            sub['receipt_wait'] = int(sub.get('receipt_wait') or 0) + 1
            if sub['receipt_wait'] < MONTH_SUB_MENU_WAIT:
                return False
    if (sub.get('left_menu') is False and not paid and not sub.get('declined')
            and not sub.get('payment_interrupted')):
        wait = int(sub.get('menu_wait', 0)) + 1
        sub['menu_wait'] = wait
        if wait < MONTH_SUB_MENU_WAIT:
            return False
    sub = mem.pop('month_sub')
    if sub['kind'] == 'egg' and sub.get('key') == mem.get('month'):
        from ..os_layer.roster import dirty_status
        actor = (NAME if sub.get('hero_selected') is True and sub.get('full_selected') is not True
                 else sub['individual_actor'] if _individual_egg_selected(mem, sub) else None)
        dirty_status(mem, [actor] if actor
                     else _egg_recovery_targets(mem), fields=('egg',))
    if shop and shop.get(sub['kind']) == 'opened':
        shop[sub['kind']] = 'done' if paid else 'unverified'
    if sub['kind'] == 'recruit':
        from ..os_layer.roster import invalidate
        invalidate(mem)  # payment/return never proves an extra general
        if shop and paid:
            shop['recruit_reserve'] = 0
        # A recruit joins the hero's castle, which need not be home.
        # Invalidate its old list; observe the candidate there before claiming
        # admission. Payment alone is not a roster increase.
        garrison = mem.get('garrison') or {}
        placements = {c for c, gs in garrison.items() if NAME in (gs or ())}
        placements.add(chart.home_castle(mem.get('chapter') or 0))
        for c in placements:
            garrison.pop(c, None)
        if paid:
            mem['recruit_verification'] = {
                'month': sub.get('key'), 'candidates': sorted(set(sub.get('candidate_names', []))
                                                           | set(sub.get('joined_names', []))),
                'generals_before': sub.get('generals_before', []), 'placement': 'unclassified'}
    if (shop and paid and sub['kind'] == 'egg'
            and (shop.get('egg_priority') or shop.get('egg_before_recruit'))):
        # The next clamp uses the actual balance after recovery. Its old
        # estimate is already paid and must not be reserved a second time.
        shop['reserve'] = 0
    if (shop and type(gold) is int and not shop.get('soldiers_done')
            and (_house_repair_reserve(shop) or (paid and sub['kind'] == 'recruit'))):
        # g508: a confirmed 50G fee plus a further 12G deduction left 94G;
        # the original 76 soldiers then spent the wage reserve down to 18G.
        # All paid recruits need the actual balance, not only broken heroes.
        previous_soldiers = shop.get('soldiers', 0)
        shop['soldiers'] = min(previous_soldiers, max(0, gold
                               - _month_held_reserve(shop) - _egg_recovery_wage_floor(mem)
                               - shop.get('recruit_reserve', 0)))
        shop['soldiers_done'] = shop['soldiers'] == 0
        if paid and sub['kind'] == 'recruit':
            _record(mem, 'soldier_budget_after_recruit', month=sub.get('key'),
                    observed_metric={'gold_after': gold, 'soldiers_before': previous_soldiers,
                                     'soldiers': shop['soldiers'], 'wage_reserve': _egg_recovery_wage_floor(mem)},
                    reason='募集後の実残金から兵士予算を再計算し、追加出費で賃金予備を使い込まない')
    if paid and sub['kind'] == 'egg':
        actor = (NAME if sub.get('hero_selected') is True and sub.get('full_selected') is not True
                 else sub.get('individual_actor') if _individual_egg_selected(mem, sub) else None)
        if actor:
            # Only this actor's real payment was observed. Other generals'
            # counts/rechecks and every known zero retain their evidence.
            if _own_egg_empty(mem, {'ally': actor}):
                mem['egg_zero_owners'] = sorted(set(mem.get('egg_zero_owners') or []) | {actor})[:32]
            (mem.get('egg_uses') or {}).pop(actor, None)
            if actor in (mem.get('house_eggs') or {}):
                mem['house_eggs'][actor]['uses'] = None
            mem['egg_recheck'] = [n for n in mem.get('egg_recheck') or [] if n != actor]
            if shop:
                actors = shop.setdefault('egg_paid_actors', [])
                if actor not in actors:
                    actors.append(actor)
                remaining = _egg_recovery_targets(mem)
                shop.update(egg='check' if remaining else 'done',
                            reserve=EGG_RECOVER_COST * len(remaining),
                            egg_cost=_egg_measured_cost(mem))
        else:
            _egg_quantities_unknown(mem)  # zero is released only by a named positive reading
            mem.pop('egg_recheck', None)
        if shop and (shop.get('egg_priority') or shop.get('egg_before_recruit')):
            if not shop.get('soldiers_done'):
                shop['soldiers'] = min(shop.get('soldiers', 0), max(0, gold - _egg_recovery_wage_floor(mem) - shop.get('reserve', 0)
                    - _house_repair_reserve(shop) - shop.get('recruit_reserve', 0)))
                shop['soldiers_done'] = shop['soldiers'] == 0
    if sub['kind'] == 'chikujou':
        chosen = sub.get('chosen')
        verified = bool(paid and sub.get('confirm_sent') and sub.get('upgraded') and chosen)
        if shop:
            tried = shop.setdefault('chikujou_tried', [])
            for castle in [*(sub.get('rows_tried') or []), *([chosen] if chosen else [])]:
                if castle not in tried:
                    tried.append(castle)
            if verified:
                upgrades = shop.setdefault('chikujou_upgrades', [])
                if not any(item.get('castle') == chosen for item in upgrades):
                    upgrades.append({'castle': chosen, 'cost': cost,
                                     'gold_before': before, 'gold_after': gold})
                reserve = _chikujou_reserve(mem, shop)
                more = (sub.get('key') is not None and sub.get('key') == shop.get('key')
                        and not sub.get('aborted') and _chikujou_affordable(gold, reserve)
                        and any(c not in tried for c in _chikujou_candidates(mem)))
                shop['chikujou'] = 'check' if more else 'done'
            elif sub.get('declined') and type(gold) is int and gold == before:
                # A castle refused only on its own read price leaves the
                # others: each castle's price follows its own general's 内政.
                reserve = _chikujou_reserve(mem, shop)
                more = (sub.get('cost_declined') and chosen
                        and sub.get('key') is not None and sub.get('key') == shop.get('key')
                        and not sub.get('aborted') and _chikujou_affordable(gold, reserve)
                        and any(c not in tried for c in _chikujou_candidates(mem)))
                shop['chikujou'] = ('check' if more else
                                    'done' if shop.get('chikujou_upgrades') else 'skipped')
            else:
                shop['chikujou'] = 'unverified'
        _record(mem, 'chikujou', month=sub.get('key'),
                observed_metric={'castle': chosen, 'gold_before': before, 'gold_after': gold,
                                 'quoted_cost': cost, 'payment_verified': bool(paid),
                                 'upgrade_verified': verified,
                                 'receipt_screen': 'castle_list' if sub.get('receipt_on_list') else 'month_menu',
                                 'declined': sub.get('declined', False),
                                 'upgrades': list((shop or {}).get('chikujou_upgrades') or [])},
                deviation_reason=(None if verified or sub.get('declined') else
                                  'result_not_observed' if paid else 'cost_not_observed'),
                reason='増築結果と実残金の減少が実費に一致。未試行の所有城も残金で判定する' if verified
                else '築城を見送った／結果と支払いを照合できないため追加の増築は行わない')
        return True
    _record(mem, 'egg_recover' if sub['kind'] == 'egg' else 'recruit', month=sub.get('key'),
            strategy_variant='recruit_default_cursor' if sub['kind'] == 'recruit' else 'egg_recover',
            observed_metric={'gold_before': before, 'gold_after': gold, 'presses': sub.get('presses'),
                             'aborted': sub.get('aborted', False),
                             'full_selected': sub.get('full_selected'), 'quoted_cost': cost,
                             'individual_actor': sub.get('individual_actor'),
                             'payment_verified': bool(paid),
                             'menu_ready': month_menu_ready(screen),
                             'fee_receipt_gold': sub.get('recruit_paid_gold'),
                             'joined_announced': sub.get('joined_names', [])},
            deviation_reason=(None if paid else 'payment_interrupted'
                              if sub.get('payment_interrupted') else 'cost_not_observed'),
            reason=('月一メニュー復帰時の所持金で実行を確認' if paid else
                    '別イベント割込み後の残金差は旧取引の支払証拠にしない'
                    if sub.get('payment_interrupted') else
                    '月一メニュー復帰時の支払い・操作可能状態を照合できない'))
    return True


CHIKUJOU_LEFTOVER = ('これいじょうのぞうちく', 'ぞうちくなさいます')


def chikujou_leftover(screen, mem) -> bool:
    """An untracked ちくじょう overlay: only B leaves it.

    g462 18:06:10-18:11: the sub had been closed early, so the castle list
    (「これいじょうのぞうちくはできませんぞ!! どのしろをぞうちくなさいますか?」)
    fell through to legacy A presses that changed nothing for 300 s and the run
    watchdog ended the corner. Measured screens: kind text, phrase above.
    """
    if mem.get('month_sub'):
        return False
    if not any(phrase in screen.text for phrase in CHIKUJOU_LEFTOVER):
        mem.pop('chikujou_leftover', None)
        return False
    if not mem.get('chikujou_leftover'):
        mem['chikujou_leftover'] = True
        _record(mem, 'chikujou_leftover', screen=screen.kind,
                observed_metric={'text': screen.text[-60:]},
                deviation_reason='chikujou_state_lost',
                reason='ちくじょうの画面を方策状態なしで確認したためBで閉じる')
    return True


def _paid_recruit_candidate(screen, sub):
    """Measured candidate biography + paid recruitment receipt, not a blank fade."""
    if sub.get('payment_interrupted') and not _recruit_fee_observed(sub):
        return None
    gold = (screen.header or {}).get('gold')
    before = sub.get('gold_before')
    if sub.get('kind') != 'recruit' or type(gold) is not int or type(before) is not int or before - gold != RECRUIT_COST:
        return None
    body = ''.join(line.span(8, 248).replace(' ', '') for line in screen.lines if line.y == 151)
    match = re.fullmatch(r'「わたしのなは([^\ufffd]+)ともうします。', body)
    words = ''.join(line.known.replace(' ', '') for line in screen.lines if line.y >= 167)
    if (screen.kind != 'text' or not match or not all(w in words for w in ('HP', 'たまご', 'せんとう', 'ないせい', 'ちんぎん'))):
        return None
    return match[1]


def month_sub_step(screen: Screen, mem):
    """Screens inside measured egg recovery / unmeasured general recruitment.

    Recruitment advances with A (first audition candidate, owner
    rule) and うむッ! on confirmations, at most MONTH_SUB_LIMIT presses; then
    B out and record. A map or battle means the month is over: drop it.
    """
    sub = mem['month_sub']
    kind = screen.kind
    if kind != 'month_menu':
        sub['left_menu'] = True     # the flow really left the month menu
    if kind in MONTH_SUB_EXIT_KINDS:
        mem.pop('month_sub')
        _record(mem, 'month_sub_lost', screen=kind, observed_metric=sub,
                reason='月一の実行中に月一メニューへ戻らず別画面になったため追跡をやめる')
        return None
    if sub.get('payment_interrupted') and not _recruit_fee_observed(sub):
        if not sub.get('aborted'):
            _record(mem, 'month_sub_abort', screen=kind,
                    reason='別イベントで旧取引の費用帰属が未確認になったため追加支払いせず戻る')
        sub['aborted'] = True
        return [pad('b')]
    candidate = _paid_recruit_candidate(screen, sub)
    if candidate:
        sub['recruit_paid_gold'] = screen.header['gold']
    if sub['kind'] == 'recruit':
        header = screen.header or {}
        key = (f"{header['year']}-{header['month']}" if type(header.get('year')) is int
               and type(header.get('month')) is int else None)
        before, gold, receipt = sub.get('gold_before'), header.get('gold'), sub.get('recruit_paid_gold')
        scope_ok = (sub.get('key') == key if key is not None else
                    mem.get('month') is None or sub.get('key') == mem.get('month'))
        paid = bool(candidate) or (type(before) is int and scope_ok
                and type(receipt) is int and before - receipt == RECRUIT_COST)
        paid = paid or (not sub.get('payment_interrupted')
                        and type(before) is int and key is not None and sub.get('key') == key
                        and type(gold) is int and before - gold == RECRUIT_COST)
        if (paid and sub.get('aborted') and not sub.get('paid_receipt_recovered')
                and not sub.get('payment_interrupted')):
            # g662 1-10 02:41: うむッ paid 50G (144->94G), the next fade had no
            # header, the unread gold was judged "no spare cash" and aborted;
            # the trio's 「おめにかかれて こうえいです」 introduction (not the
            # measured biography) then took 109 B presses for 300 s until the
            # run watchdog ended the corner. An observed fee is progress.
            sub['paid_receipt_recovered'] = True
            sub['presses'] = 0
            sub.pop('aborted', None)
            sub.pop('abort_backs', None)
            if mem.get('shop'):
                mem['shop']['recruit'] = 'opened'
            _record(mem, 'recruit_paid_receipt', month=sub.get('key'),
                    observed_metric={'gold_before': before, 'gold': gold, 'receipt': receipt},
                    reason='募集費50Gの実減少を確認したため離脱を取り消し、候補紹介を上限付きAで進める')
        if sub.get('aborted'):
            pass  # already leaving; the bounded abort handler below owns B
        elif not paid and not _recruit_egg_ready(mem, mem.get('shop') or {}):
            sub['aborted'] = True
            if mem.get('shop'):
                mem['shop']['recruit'] = 'deferred_egg'
            _record(mem, 'recruit_deferred_egg', month=sub.get('key'),
                    reason='追跡中の未払い募集導入でも卵回復を先に確認するため戻る')
        elif not paid:
            shop = mem.get('shop') or {}
            spare, evidence = _recruit_dialog_cash_ready(mem, shop, gold)
            if spare is not True and type(gold) is not int:
                # An unread header is not "no spare cash" (g662). Never confirm
                # a payment blind; other dialogue advances within the limit.
                if kind == 'yes_no' or (screen.has('うむッ') and screen.has('いかんッ')):
                    return []
            elif spare is not True:
                sub['aborted'] = True
                if shop:
                    _defer_recruit_cash(mem, shop, gold, evidence)
    if sub['kind'] == 'recruit' and sub.get('recruit_paid_gold') is not None:
        for line in screen.lines:
            joined = re.fullmatch(r'([^�]+)がはいかにくわわった!', line.known.replace(' ', '').replace('！', '!'))
            if joined and joined[1] not in sub.setdefault('joined_names', []):
                sub['joined_names'].append(joined[1])
                from ..os_layer.roster import invalidate
                invalidate(mem)
                _record(mem, 'recruit_join_announced', general=joined[1], month=sub.get('key'),
                        observed_metric={'placement': 'unclassified'},
                        reason='募集の実加入告知を確認。配置は次の在城一覧で照合する')
    if candidate and candidate not in sub.setdefault('candidate_names', []):
        sub['candidate_names'].append(candidate)
    if candidate and not sub.get('paid_candidates'):
        # v16 may already have entered the old 8-observation abort. The
        # measured paid candidate screen is new progress, so recover once;
        # repeated/unknown screens can never reset this second bound again.
        sub['paid_candidates'] = True
        sub['presses'] = 0
        sub.pop('aborted', None)
        _record(mem, 'recruit_candidates_seen', general=candidate,
                reason='募集費50G支払い後の候補紹介を実測し、上限付き候補選択へ進む')
    sub['presses'] = int(sub.get('presses', 0)) + 1
    limit = (CHIKUJOU_SUB_LIMIT if sub['kind'] == 'chikujou' else
             EGG_RITUAL_LIMIT if sub.get('stage') == 'recovering' else
             RECRUIT_CANDIDATE_LIMIT if sub.get('paid_candidates') else MONTH_SUB_LIMIT)
    if sub.get('aborted') or sub['presses'] > limit:
        if not sub.get('aborted'):
            sub['aborted'] = True
            _record(mem, 'month_sub_abort', screen=kind, observed_metric={'text': screen.text[-40:]},
                    reason='月一の未測定画面が上限回数で終わらないためBで離脱')
        backs = int(sub.get('abort_backs') or 0) + 1
        sub['abort_backs'] = backs
        # B cannot close a plain message box (g662: 109 B, 300 s, corner
        # ended). After a bounded number of ignored Bs, a dialogue with no
        # menu cursor and no yes/no is advanced with A - A on plain text
        # cannot choose a purchase - then B is tried again.
        if (backs > MONTH_SUB_ABORT_B_LIMIT and screen.hand is None and kind == 'text'
                and not (screen.has('うむッ') or screen.has('いかんッ'))
                and backs % 2 == 0 and backs <= MONTH_SUB_ABORT_B_LIMIT + 2 * RECRUIT_CANDIDATE_LIMIT):
            if not sub.get('abort_text_advance'):
                sub['abort_text_advance'] = True
                _record(mem, 'month_sub_abort_text_advance', screen=kind,
                        observed_metric={'backs': backs, 'text': screen.text[-40:]},
                        reason='離脱のBで進まない会話文のため、カーソル・確認のない文だけAで送る')
            return [pad('a')]
        return [pad('b')]
    if sub['kind'] == 'egg':
        return _egg_recovery_step(screen, mem, sub)
    if sub['kind'] == 'chikujou':
        return _chikujou_step(screen, mem, sub)
    if kind == 'yes_no' or (screen.has('うむッ') and screen.has('いかんッ')):
        move = menu_to(screen, 'うむッ!')
        if move and move != 'here':
            return [move]
    _record(mem, 'month_sub_step', screen=kind, choice=screen.selected,
            observed_metric={'kind': sub['kind'], 'presses': sub['presses'], 'text': screen.text[-60:]},
            reason='月一の実行画面を既定カーソルのまま決定')
    return [pad('a')]
