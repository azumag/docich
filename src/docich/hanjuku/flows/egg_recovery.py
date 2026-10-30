"""手順層: the monthly egg-recovery (卵回復) transaction."""
from __future__ import annotations

import re

from ...hanjuku_egg_reference import general_max_hp
from ...hanjuku_egg_stock import observed_uses
from ..decision_log import _record
from ..judgment.battle import _own_egg_empty
from ..os_layer.commands import menu_to, pad
from ..os_layer.readers import _current, _options, month_menu_ready
from ..os_layer.world import NAME
from ..strategy.economy import (EGG_RECOVER_COST, EGG_RITUAL_LIMIT, WAGE_SPEND_HOLD,
                                _depleted_eggs, _egg_measured_cost, _egg_recovery_targets,
                                _month_key)


def _hero_only_egg_recovery(screen, mem, sub):
    """Recover only the hero's egg when the full recovery is unaffordable.

    g604 3-11: どうし had 0 uses, the full quote was 3こで150G against 68G, so
    nothing was recovered. Two battles later a defended castle (no たいきゃく)
    met チコリ's ダークエルフ (ひとだまくん = instant death, gcgx evil_egg) and
    the eggless hero died -> game over. The list shows one row per general
    (ぜんかいふく at y=47, then names at x=32); the quote must bind the
    selected owner and show its actual 50G fee before anything is paid.
    """
    if NAME not in _depleted_eggs(mem):
        return False
    shop = mem.get('shop') or {}
    if shop.get('key') == mem.get('month') and NAME in (shop.get('egg_paid_actors') or []):
        return False
    if not any(x == 32 and word == NAME for x, y, word in _options(screen)):
        return False
    gold = sub.get('gold_before')
    repair = (mem.get('shop') or {}).get('hero_repair_reserve', 0)
    return (type(gold) is int and gold >= EGG_RECOVER_COST + repair + WAGE_SPEND_HOLD
            and (sub.get('full_quote_short') or gold < _egg_measured_cost(mem) + repair + WAGE_SPEND_HOLD))


def _individual_egg_selected(mem, sub):
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    actor = sub.get('individual_actor')
    return (identity is not None and sub.get('individual_identity') == identity
            and type(mem.get('chapter')) is int and 1 <= mem['chapter'] <= 12
            and sub.get('individual_chapter') == mem['chapter']
            and sub.get('individual_selected') is True and isinstance(actor, str) and actor not in (NAME, 'しゅじんこう')
            and general_max_hp(actor) is not None and sub.get('key') == mem.get('month'))


def _observe_recovery_list_quantities(screen, mem, sub):
    """Retain each unique actual named row, including unpaid low-use eggs."""
    from ..os_layer.roster import run_identity
    if (run_identity(mem.get('_run_identity')) is None or sub.get('key') != mem.get('month')
            or (mem.get('shop') or {}).get('key') != sub.get('key')):
        return
    rows = {}
    for line in screen.lines:
        cells = dict(line.spans())
        actor = cells.get(32)
        if (47 < line.y <= 199 and actor
                and (actor == NAME or general_max_hp(actor) is not None)):
            rows.setdefault(actor, []).append(cells.get(104))
    for actor, readings in rows.items():
        if len(readings) != 1 or readings[0] not in ('0', '1', '2', '3', '4'):
            continue
        from ..os_layer.roster import observed_member
        observed_member(mem, [actor])
        quantity = int(readings[0])
        mem.setdefault('egg_uses', {})[actor] = quantity
        if quantity == 0:
            mem['egg_zero_owners'] = sorted(set(mem.get('egg_zero_owners') or []) | {actor})[:32]
        else:
            _own_egg_empty(mem, {'ally': actor})


def _individual_egg_candidate(screen, mem, sub):
    """Actual visible low-use non-hero rows, only when full recovery is short."""
    from ..os_layer.roster import run_identity
    identity = run_identity(mem.get('_run_identity'))
    gold = sub.get('gold_before')
    repair = (mem.get('shop') or {}).get('hero_repair_reserve', 0)
    floor = WAGE_SPEND_HOLD
    if (identity is None or sub.get('key') != mem.get('month')
            or type(mem.get('chapter')) is not int or not 1 <= mem['chapter'] <= 12
            or type(gold) is not int or gold < EGG_RECOVER_COST + floor + repair
            or (not sub.get('full_quote_short') and gold >= _egg_measured_cost(mem) + floor + repair)):
        return None
    shop = mem.get('shop') or {}
    paid = (shop.get('egg_paid_actors') or []) if shop.get('key') == mem.get('month') else []
    candidates = []
    for line in screen.lines:
        cells = dict(line.spans())
        actor, quantity = cells.get(32), cells.get(104)
        if (actor in (None, NAME, 'しゅじんこう') or actor in paid or not 47 < line.y <= 199
                or general_max_hp(actor) is None or quantity not in ('0', '1')
                or (quantity == '1' and (mem.get('egg_types') or {}).get(actor) in ('いっぱつエッグ', 'キングエッグ'))):
            continue
        candidates.append((int(quantity), line.y, actor))
    return min(candidates)[2] if candidates else None


def resume_egg_ritual_step(screen, mem):
    """Cold restored priest dialogue owns no remembered purchase receipt."""
    owner = mem.get('resume_egg_ritual')
    if not isinstance(owner, dict):
        return None
    if screen.priest_ritual_scene and not screen.priest_ritual_dialogue:
        return []
    if screen.kind in MONTH_SUB_EXIT_KINDS or month_menu_ready(screen):
        mem.pop('resume_egg_ritual', None)
        return None
    header = screen.header or {}
    if (all(type(header.get(k)) is int for k in ('year', 'month'))
            and _month_key(header) != owner.get('month')):
        mem.pop('resume_egg_ritual', None)
        return None
    readings = owner.get('readings')
    if type(readings) is not int or readings < 0 or readings >= EGG_RITUAL_LIMIT:
        return []
    owner['readings'] = readings + 1
    if screen.has('うむッ') or screen.has('いかんッ'):
        return []  # no purchase ownership/fee authority survives restoration
    current = _current(screen)
    if screen.hand:
        return [pad('b')] if current and current[0] == 32 and 47 <= current[1] <= 199 else []
    if screen.has('どのしょうぐんのたまごを') and screen.has('おはらいしますか?'):
        return []
    if screen.kind != 'text' or not screen.text:
        return []
    _record(mem, 'resume_egg_ritual_dialogue', observed_metric={'readings': readings + 1},
            reason='実儀式準備から復元したカーソル・確認のない会話だけ上限付きAで進める。旧支払い・数量・章は引き継がない')
    return [pad('a')]


def _egg_recovery_step(screen, mem, sub):
    """Measured full-recovery list and NこでNG confirmation; never guess a row."""
    if screen.priest_ritual_scene and not screen.priest_ritual_dialogue:
        return []
    if not screen.text:
        # The paid ritual fades back into the month menu. A on that blank
        # frame can be buffered and open the newly focused merchant row.
        return []
    if (sub.get('stage') == 'recovering'
            and (screen.has('うむッ') or screen.has('いかんッ'))):
        return []  # paid quote, including a partial fade: no second confirmation
    full_rows = [(x, y, word) for x, y, word in _options(screen)
                 if x == 32 and y == 47 and word == 'ぜんかいふく']
    current = _current(screen)
    recovery_cursor = (screen.hand is not None and screen.hand[2] <= 32
                       and current is not None and current[0] == 32
                       and 47 <= current[1] <= 199
                       and any(x == 32 and y == current[1] for x, y, _ in _options(screen)))
    # g725: the ritual preparation dialogue retains the list in the
    # background, without its hand. Only an interactive left list can be
    # closed; its background is not a payment/return receipt.
    if sub.get('stage') == 'recovering' and full_rows and recovery_cursor:
        # g719 UID2981: an individual priest ritual returns to this list,
        # with the recovered actor omitted and the cursor on full recovery.
        # The old dialogue A reopened its quote and then waited to the cap.
        # Close the list; only the existing quiet month-menu receipt may
        # establish payment. Absence of an actor never establishes quantity.
        from ..os_layer.roster import run_identity
        identity = run_identity(mem.get('_run_identity'))
        header = screen.header or {}
        gold, before, cost = header.get('gold'), sub.get('gold_before'), sub.get('quoted_cost')
        full = sub.get('full_selected') is True
        hero = sub.get('hero_selected') is True
        individual = sub.get('individual_selected') is True
        owner = ((full and not hero and not individual)
                 or (hero and not full and not individual and sub.get('hero_identity') == identity
                     and sub.get('hero_chapter') == mem.get('chapter'))
                 or (individual and not full and not hero and _individual_egg_selected(mem, sub)))
        actor = 'ぜんかいふく' if full else NAME if hero else sub.get('individual_actor')
        bound = (identity is not None and owner and sub.get('confirm_identity') == identity
                 and sub.get('kind') == 'egg' and sub.get('confirm_actor') == actor
                 and type(mem.get('chapter')) is int and 1 <= mem['chapter'] <= 12
                 and sub.get('confirm_chapter') == mem['chapter'])
        same = (all(type(header.get(k)) is int for k in ('year', 'month'))
                and _month_key(header) == sub.get('key') == mem.get('month')
                == (mem.get('shop') or {}).get('key'))
        debit = (same and type(gold) is int and type(before) is int
                 and type(cost) is int and cost > 0 and before - gold == cost)
        owner_still_listed = (not full and isinstance(actor, str)
                              and any(47 < line.y <= 199 and dict(line.spans()).get(32) == actor
                                      for line in screen.lines))
        if (not bound or len(full_rows) != 1
                or owner_still_listed
                or (all(type(header.get(k)) is int for k in ('year', 'month')) and not same)
                or (same and type(gold) is int and not debit)):
            sub['payment_interrupted'] = True
        # Named current quantities remain evidence even when a debit cannot
        # belong to this actor. An omitted different row is never paid for.
        if bound and same and len(full_rows) == 1:
            _observe_recovery_list_quantities(screen, mem, sub)
        if not sub.get('recovery_list_return'):
            sub['recovery_list_return'] = True
            _record(mem, 'egg_recover_list_return', month=sub.get('key'),
                    observed_metric={'gold_before': before, 'gold_after': gold,
                                     'quoted_cost': cost, 'debit_matches': bool(bound and debit),
                                     'owner_still_listed': owner_still_listed},
                    reason='回復儀式後の実一覧をBで閉じる。支払いは静かな同月メニュー復帰で照合し、消えた本人の残数は推測しない')
        return [pad('b')]
    if screen.has('うむッ') and screen.has('いかんッ'):
        body = ''.join(line.known.replace(' ', '') for line in screen.lines if line.y >= 140)
        quote = re.search(r'(\d+)こで(\d+)Gになりまんな', body)
        gold = (screen.header or {}).get('gold')
        hero_only = bool(sub.get('hero_selected'))
        individual = _individual_egg_selected(mem, sub)
        quoted_count = int(quote[1]) if quote else None
        quoted_cost = int(quote[2]) if quote else None
        count_basis = 'screen' if quote else None
        if quote is None and sub.get('full_selected') is not True:
            from ..os_layer.roster import run_identity
            identity = run_identity(mem.get('_run_identity'))
            hero_bound = (hero_only and identity is not None
                          and sub.get('hero_identity') == identity
                          and type(mem.get('chapter')) is int and 1 <= mem['chapter'] <= 12
                          and sub.get('hero_chapter') == mem.get('chapter'))
            fees = re.findall(r'(\d+)Gかかりますがよろしいですかな', body)
            # g713 UID18041: an individually selected egg shows a priest's
            # flat fee, not the full-recovery NこでNG wording. One egg comes
            # from the bound actual actor, never from an invented screen count.
            flat_owner_bound = hero_bound if hero_only else individual
            if (flat_owner_bound and sub.get('kind') == 'egg'
                    and isinstance(sub.get('key'), str)
                    and sub.get('key') == mem.get('month') == (mem.get('shop') or {}).get('key')
                    and all(type((screen.header or {}).get(k)) is int for k in ('year', 'month'))
                    and _month_key(screen.header) == sub.get('key')
                    and type(gold) is int and gold == sub.get('gold_before')
                    and body.count('おはらいしをよぶのですな?') == 1 and len(fees) == 1):
                quoted_count, quoted_cost = 1, int(fees[0])
                count_basis = 'bound_actor_flat_fee'
        if (sub.get('full_selected') is True and sub.get('stage') != 'recovering' and quoted_cost is not None and type(gold) is int
                and sub.get('key') == mem.get('month') == _month_key(screen.header)
                and quoted_count > 1 and quoted_cost == quoted_count * EGG_RECOVER_COST
                and gold < quoted_cost + WAGE_SPEND_HOLD
                           + (mem.get('shop') or {}).get('hero_repair_reserve', 0)
                and gold >= EGG_RECOVER_COST + WAGE_SPEND_HOLD
                            + (mem.get('shop') or {}).get('hero_repair_reserve', 0)
                and not sub.get('full_quote_short')
                and any(observed_uses((mem.get('egg_uses') or {}).get(n)) in (0, 1)
                        for n in _egg_recovery_targets(mem))):
            sub.update(full_selected=False, full_quote_short=True, gold_before=gold, stage='choosing')
            _record(mem, 'egg_recover_partial_recheck', observed_metric={'gold': gold, 'full_cost': quoted_cost},
                    reason='全回復費は不足するが1個分の実費と修理予約を保持できるため、実一覧の残数0・少数の卵へ戻る')
            return [pad('b')]
        if (not (sub.get('full_selected') or hero_only or individual) or quoted_cost is None
                or quoted_count <= 0 or quoted_cost != quoted_count * EGG_RECOVER_COST
                or ((hero_only or individual) and quoted_count != 1)
                or (individual and _month_key(screen.header) != sub.get('key'))
                or type(gold) is not int or gold < quoted_cost
                + (mem.get('shop') or {}).get('hero_repair_reserve', 0)
                # Check the real quote and repair reservation before paying.
                # Necessary recovery has no wage hold (Issue #2367).
                + WAGE_SPEND_HOLD):
            sub['aborted'] = True
            _record(mem, 'egg_recover_skip', observed_metric={'gold': gold, 'quote_read': quoted_cost is not None},
                    reason='全回復の選択・費用・所持金を確認できないか、費用が足りないため戻る')
            from ..strategy import goals
            goals.note_attempt(mem, 'egg_recover', 'skipped')
            return [pad('b')]
        move = menu_to(screen, 'うむッ!')
        if move == 'here':
            sub.update(quoted_cost=quoted_cost, stage='recovering')
            from ..os_layer.roster import run_identity
            sub.update(confirm_identity=run_identity(mem.get('_run_identity')),
                       confirm_chapter=mem.get('chapter'),
                       confirm_actor=NAME if hero_only else sub['individual_actor'] if individual else 'ぜんかいふく')
            _record(mem, 'egg_recover_confirm', choice=NAME if hero_only else sub['individual_actor'] if individual else 'ぜんかいふく',
                    observed_metric={'eggs': quoted_count, 'cost': quoted_cost, 'gold': gold,
                                         'count_basis': count_basis},
                    reason=('主人公1個分の表示費用が所持金以内なので決定。回復完了は未確定'
                            if hero_only else '残数の少ない本人1個の表示費用と修理予約を確認して決定。回復完了は未確定'
                            if individual else
                            '全回復の表示費用が所持金以内なので決定。回復完了は未確定'))
            return [pad('a')]
        return [move] if move else []
    if sub.get('stage') != 'recovering' and any(
            x == 32 and y == 47 and word == 'ぜんかいふく' for x, y, word in _options(screen)):
        _observe_recovery_list_quantities(screen, mem, sub)
        if sub.get('hero_selected') or _hero_only_egg_recovery(screen, mem, sub):
            move = menu_to(screen, NAME)
            if move == 'here':
                from ..os_layer.roster import run_identity
                sub['hero_selected'] = True
                sub['full_selected'] = False
                for key in ('individual_selected', 'individual_actor',
                            'individual_identity', 'individual_chapter'):
                    sub.pop(key, None)
                sub.pop('hero_identity', None)
                sub.pop('hero_chapter', None)
                readings = [dict(l.spans()).get(104) for l in screen.lines
                            if dict(l.spans()).get(32) == NAME and 47 < l.y <= 199]
                if len(readings) == 1 and readings[0] in ('0', '1', '2', '3', '4'):
                    sub['hero_identity'] = run_identity(mem.get('_run_identity'))
                    sub['hero_chapter'] = mem.get('chapter')
                _record(mem, 'egg_recover_select', choice=NAME,
                        observed_metric={'gold': sub.get('gold_before'),
                                         'full_estimate': _egg_measured_cost(mem)},
                        reason=('全回復費と修理予約を所持金で賄えないが、防衛戦で退却できず'
                                '卵のない主人公は召喚獣の即死攻撃でゲームオーバーになる'
                                '(g604 ダークエルフ)ため、主人公の卵だけを回復する'))
                return [pad('a')]
            return [move] if move else []
        actor = sub.get('individual_actor') if _individual_egg_selected(mem, sub) else _individual_egg_candidate(screen, mem, sub)
        if actor:
            move = menu_to(screen, actor)
            if move == 'here':
                from ..os_layer.roster import run_identity
                readings = [cells.get(104) for line in screen.lines
                            if (cells := dict(line.spans())).get(32) == actor
                            and 47 < line.y <= 199]
                if len(readings) != 1 or readings[0] not in ('0', '1'):
                    return []  # duplicate/unread named rows cannot bind an individual quote
                # This is the actual list quantity, not a planned use/payment.
                quantity = int(readings[0])
                mem.setdefault('egg_uses', {})[actor] = quantity
                if quantity == 0:
                    mem['egg_zero_owners'] = sorted(set(mem.get('egg_zero_owners') or []) | {actor})[:32]
                else:
                    _own_egg_empty(mem, {'ally': actor})  # named actual positive may release a prior zero
                sub.update(individual_actor=actor, individual_selected=True,
                           individual_identity=run_identity(mem.get('_run_identity')),
                           individual_chapter=mem['chapter'], full_selected=False)
                _record(mem, 'egg_recover_select', choice=actor,
                        observed_metric={'gold': sub.get('gold_before'), 'quantity_seen': quantity,
                                         'cost_pending_quote': EGG_RECOVER_COST},
                        reason='全回復を払えないため、実一覧の残数0・少数の将軍の卵だけを選び費用を確認する')
                return [pad('a')]
            return [move] if move else []
        if sub.get('full_quote_short'):
            sub['aborted'] = True
            return [pad('b')]  # no measured eligible row; never repeat the full quote
        move = menu_to(screen, 'ぜんかいふく')
        if move == 'here':
            sub['full_selected'] = True
            _record(mem, 'egg_recover_select', choice='ぜんかいふく',
                    reason='残数が0でない卵も含めて全回復を選択し、次画面で費用を確認')
            return [pad('a')]
        return [move] if move else []
    if screen.hand:
        return []  # unknown option, including paid fades: never default A
    if (sub.get('stage') == 'recovering' and full_rows
            and screen.has('どのしょうぐんのたまごを') and screen.has('おはらいしますか?')):
        return []  # returned-list question with an unread hand: do not buy again
    return [pad('a')]  # bounded introduction/paid ritual dialogue


MONTH_SUB_EXIT_KINDS = frozenset({'map', 'map_target', 'battle', 'battle_menu', 'egg_battle_menu', 'egg_choice_menu',
                                  'monster_menu', 'attack_started', 'defense_started',
                                  'boss_attack_started', 'name_entry', 'castle_menu'})
