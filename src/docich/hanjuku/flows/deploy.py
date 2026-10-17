"""手順層: the sortie menus (castle menu, general list, cards, confirmation).

Carries out a chart/interim order through the castle menu: verifies the
source castle and general, selects the planned cards and confirms the sortie,
holding or cancelling when the visible menu contradicts the plan.
"""
from __future__ import annotations

import re
import time

from ..os_layer.screen import Screen
from ..os_layer.font import UNKNOWN
from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ...hanjuku_egg_reference import general_max_hp
from ..decision_log import _record
from ..os_layer.commands import menu_to, pad
from ..os_layer.readers import (CASTLE_STATUS, STATUS_NAMES, _empty_sortie_inventory, _general_visible,
                                _measured_card_select, _options, _present_generals, _sortie_inventory)
from ..os_layer.world import NAME, _en_route, _owned
from ..strategy.boss_cards import UNSEEN, boss_card_reserved, boss_kit
from ..strategy.kit import (CARD_IDS, CARD_MISS_LIMIT, CARD_SCROLL_LIMIT, CARD_UNREADABLE_LIMIT, CARRY_SLOTS,
                            ENEMY_EGG_CARD_ID_SUM, RARE_SCAN_LIMIT, SORTIE_CONFIRM_LIMIT, _cap_card_ids,
                            _deploy_cards, _is_boss_order, _observe_card_stock, _strict_boss_cards)
from ..strategy.orders import (SOURCE_LOCATION_RECHECK_LIMIT, _boss_egg_depleted, _broken_hero_order,
                               _continue_transit_sorties, _few_castles,
                               _finish_order, _garrison_move, _hero_source_alternative, _order,
                               _order_hero_alternatives, _reserve_source_guard, _retake_reserved, _sortie_general,
                               _source, _source_location_recheck, _source_spare_missing, _staffed_elsewhere,
                               _unplaced_castle_generals)


def _cancel_broken_hero_sortie(mem, order):
    _record(mem, 'hero_broken_egg_sortie_held', chart_step=order['step'],
            observed_metric={'target': order.get('target'), 'repair': 'unconfirmed'},
            reason='主人公の卵破損を観測済みで修復未確認のため攻撃出撃を保留する')
    mem.pop('sortie_attempt', None)
    _finish_order(mem, 'pending', reason='主人公の卵修復確認を待つ')
    return [pad('b')]


def _verify_sortie_source(screen, mem, order):
    """Never select a replacement while checking an interrupted departure.

    Presence proves cancellation. Only the explicit empty-list message proves
    absence here: a nonempty panel can be partial or scrolled, so missing a
    name on it is not evidence of departure.
    """
    step = order['step']
    sortie = mem['sorties'][step]
    present = _present_generals(screen)
    if present and sortie['general'] in present and not _general_visible(screen, sortie['general']):
        present = None
    if present is None or (present and sortie['general'] not in present):
        sortie['verification_reads'] = sortie.get('verification_reads', 0) + 1
        if sortie['verification_reads'] >= 3:
            sortie['verification_unavailable'] = True
            mem['active'] = None
            _record(mem, 'sortie_verification_held', chart_step=step,
                    reason='一覧で在城・不在を確定できないため出撃未確定を維持して別の指示へ進む')
            return [pad('b'), pad('b')]
        return _hold_deploy(screen, mem, order, '出撃未確定の将軍の在城・不在を一覧で確定できないため保留')
    cancelled = sortie['general'] in present
    sortie['status'] = 'cancelled' if cancelled else 'en_route'
    mem.setdefault('orders', {})[step] = 'pending' if cancelled else 'launched'
    if not cancelled:
        _garrison_move(mem, sortie['general'], source=sortie['source'])
    _record(mem, 'sortie_cancelled_observed' if cancelled else 'sortie_departed_observed',
            chart_step=step, general=sortie['general'], source=sortie['source'], target=None,
            observed_metric=present,
            reason='将軍一覧で本人の在城を確認したため再試行可能' if cancelled
            else '出撃元が無人と確認できたため出撃を確定、行先は未確認')
    mem.pop('sortie_attempt', None)
    mem['active'] = None
    mem['picked'] = []
    return [pad('b'), pad('b')]
BOSS_ABSENT_LIMIT = 10         # general-list readings without the boss general before giving up



def _chapter_two_balmung_inventory(mem, order, inventory):
    """Freeze the isolated-tested boss kit from this unselected live panel.

    g700: one イッテツーン plus two バルムンク, native carried confirmation,
    measured boss entry, first バルムンク selection then enemy HP0 / next
    chapter. The cheap card protects the event cards in road encounters.
    Never infer stock from an old global cache or alter a selected sortie.
    """
    step = order['step']
    if (mem.get('chapter') != 2 or step != '2-B1'
            or order.get('target') != chart.boss_castle(2)
            or (mem.get('orders') or {}).get(step) not in (None, 'pending')
            or (mem.get('rare_card_kit') or {}).get(step) is not None
            or (mem.get('kit_spent') or {}).get(step)
            or (mem.get('card_drop') or {}).get(step)):
        return None
    stocks = {r['card']: r['stock'] for r in inventory['rows']}
    if stocks.get('イッテツーン', 0) < 1 or stocks.get('バルムンク', 0) < 2:
        return None
    if inventory['remaining'] in (1, 2):
        # g706's latest stall checkpoint contains one selected chart card.
        # Do not rewrite that partial kit or infer its identity. Back out of
        # both panels, then a new live panel can establish the complete kit.
        actions = _hold_guarded_sortie(mem, order, 'boss_stock_kit_restart',
            '不足した予定札の途中選択を取り消し、実測装備を未選択の一覧から確認し直す',
            {'remaining': inventory['remaining'],
             'visible_stocks': {c: stocks[c] for c in ('イッテツーン', 'バルムンク')}})
        # Keep this already-started boss order ahead of a new field survey.
        # The restart backs out completely before selecting another actor.
        mem['active'] = step
        mem['boss_stock_restart'] = {'step': step, 'readings': 0}
        return actions
    if mem.get('picked') or inventory['remaining'] != CARRY_SLOTS:
        return None
    original = _deploy_cards(order, mem)
    kit = ['イッテツーン', 'バルムンク', 'バルムンク']
    mem.setdefault('rare_card_kit', {})[step] = kit
    mem.pop('rare_scan', None)
    mem.setdefault('strong_card_kit', {}).pop(step, None)
    _record(mem, 'rare_card_kit', **_deploy_context(order, mem), chart_step=step,
            observed_metric={'card': 'バルムンク', 'stock': stocks['バルムンク'],
                             'cards': kit, 'original_cards': original,
                             'id_sum': sum(CARD_IDS[c] for c in kit),
                             'stocks': {c: stocks[c] for c in set(kit)},
                             'inventory_evidence': inventory['inventory_evidence'],
                             'observed_rows': inventory['observed_rows']},
            reason='実在庫のイベント札2枚と道中用の安価な札を確認し、第2話ボスの実測装備を携行する')
    return []  # the following observation selects and verifies the actual kit


def _rare_card_inventory(screen, mem, order, inventory):
    """Discover event stock before picking; freeze a positive, measured kit.

    One bounded sweep per month reaches hidden event cards without declaring
    the first four rows a complete inventory. Rewind before normal selection.
    Never change an already picked/in-flight sortie or invent event stock.
    """
    if (mem.get('picked') or inventory['remaining'] == 0
            or (mem.get('rare_card_kit') or {}).get(order['step']) is not None):
        return None
    month = mem.get('month') or f"chapter-{mem.get('chapter')}:unknown"
    scan = mem.get('rare_scan') or {}
    if scan.get('rewind', 0) > 0:
        scan['rewind'] -= 1
        if not scan['rewind']:
            mem.pop('rare_scan', None)
        _record(mem, 'sortie_input', **_deploy_context(order, mem),
                screen='card_select', observed_metric={'rare_scan': 'rewind', 'remaining': scan['rewind']},
                reason='レア札在庫の有限探索後に選択カーソルを戻す')
        return [pad('up')]
    row = next((r for r in inventory['rows'] if r['card'] == 'キャトルミュー'), None)
    if row and row['stock'] > 0 and inventory['remaining'] > 0:
        original = _deploy_cards(order, mem)
        kit = _cap_card_ids(['キャトルミュー', *[c for c in original if c != 'キャトルミュー']])[:3]
        mem.setdefault('rare_card_kit', {})[order['step']] = kit
        mem['rare_scan_month'] = month
        mem.pop('rare_scan', None)
        _record(mem, 'rare_card_kit', **_deploy_context(order, mem), chart_step=order['step'],
                observed_metric={'card': row['card'], 'stock': row['stock'], 'cards': kit,
                                 'original_cards': original, 'id_sum': sum(CARD_IDS[c] for c in kit)},
                reason='実在庫と携行枠を確認し、イベント札を1枚携行して活用する')
        return []
    if not isinstance(month, str) or mem.get('rare_scan_month') == month:
        return None
    scan = mem.setdefault('rare_scan', {'step': order['step'], 'presses': 0, 'rows': None})
    if scan['step'] != order['step']:
        mem.pop('rare_scan', None)
        return None
    rows = [r['card'] for r in inventory['rows']]
    at_bottom = inventory['selected_y'] == inventory['rows'][-1]['y']
    if (len(rows) < 4 or scan['presses'] >= RARE_SCAN_LIMIT
            or (at_bottom and scan['rows'] == rows and scan.get('was_bottom'))):
        mem['rare_scan_month'] = month
        if scan['presses']:
            scan['rewind'] = scan['presses'] - 1
            if not scan['rewind']:
                mem.pop('rare_scan', None)
            _record(mem, 'sortie_input', **_deploy_context(order, mem),
                    screen='card_select', observed_metric={'rare_scan': 'rewind', 'remaining': scan['rewind']},
                    reason='レア札探索の末尾または上限に達したため選択カーソルを戻す')
            return [pad('up')]
        mem.pop('rare_scan', None)
        return None
    scan.update(presses=scan['presses'] + 1, rows=rows, was_bottom=at_bottom)
    _record(mem, 'sortie_input', **_deploy_context(order, mem), screen='card_select',
            observed_metric={'rare_scan': 'discover', 'presses': scan['presses'], 'rows': rows},
            reason='未選択の実在庫を有限回探索し、隠れたイベント札の有無を確認する')
    return [pad('down')]

def _strong_card_inventory(mem, order, inventory, wanted) -> bool:
    """Add one held strong card to a sortie that still has a carry slot free.

    owner 2026-10-03: 「強い切り札を偶然手に入れている時などは、強い将軍とたたかう
    ときに積極的に利用するようにして下さい」→ 携行はここで決めるが、使うのは
    戦闘で敵が強い将軍と判定されたときだけ (``reference.strong_general``)。

    予定札(チャート/調整)は外さず、追加後も携行札のID合計が48未満を維持する
    (gcgx: 48以上だと敵がエッグを使う)。レア札キットの出撃と残枠なしの出撃には
    追加しない。決定は一度だけ(order_start で破棄して次の出撃で選び直す)。
    在庫の根拠は直前に更新された ``card_stock`` の正数だけにする。
    追加した時だけ True を返し、呼び出し側が同じ観測の選択対象を並べ直す
    (予定札が先頭なので、強い切り札は残り枠が空いた時に選ばれる)。
    """
    step = order['step']
    if (mem.get('strong_card_kit') or {}).get(step) is not None:
        return False
    if (mem.get('rare_card_kit') or {}).get(step) is not None:
        return False                     # レア札キットが携行札の主導権を持つ
    # 1枚でも選ぶ前 (予定札の内訳がまだ揃っている時点) だけ決める。
    if mem.get('picked') or inventory['remaining'] - len(wanted) < 1:
        return False                     # 残り枠は予定札で埋まる
    stock = mem.get('card_stock') or {}
    used = sum(CARD_IDS[c] for c in wanted)
    held = [name for name in reference.STRONG_CARDS
            if name not in wanted and int(stock.get(name, 0) or 0) > 0
            and used + CARD_IDS.get(name, ENEMY_EGG_CARD_ID_SUM) < ENEMY_EGG_CARD_ID_SUM
            and len(wanted) + 1 <= CARRY_SLOTS]
    # Copies the pending base boss kit still needs are not spare (g825).
    card = next((name for name in held if not boss_card_reserved(mem, order, name)), None)
    reserved = held[:held.index(card)] if card is not None else held  # passed over
    marker = [mem.get('month'), step, reserved]
    if reserved and mem.get('strong_card_reserved') != marker:
        mem['strong_card_reserved'] = marker
        _record(mem, 'strong_card_reserved_for_boss', **_deploy_context(order, mem), chart_step=step,
                observed_metric={'reserved': reserved,
                                 'stock': {name: stock.get(name) for name in reserved},
                                 'boss_kit': dict(boss_kit(mem)[1]), 'chosen': card},
                reason='ボス注文に必要な枚数しかない切り札は、非ボスの出撃へ強い切り札として足さない')
    if card is None:
        return False
    mem.setdefault('strong_card_kit', {})[step] = card
    carried = _deploy_cards(order, mem)
    _record(mem, 'strong_card_kit', **_deploy_context(order, mem), chart_step=step, card=card,
            observed_metric={'stock': int(stock.get(card, 0) or 0), 'planned': list(wanted),
                             'cards': carried, 'id_sum': sum(CARD_IDS[c] for c in carried),
                             'remaining': inventory['remaining']},
            reason='実在庫の強い切り札を携行枠に加え、強い将軍との戦闘で開幕に使う')
    return True


def _drop_card(screen, mem, order, card, inventory):
    """Leave a planned card behind after it stays unselectable (non-boss only).

    A measured panel shows at most four rows, so a card absent from a full
    panel is not proven to be out of stock (it may be scrolled off), but
    holding forever is worse: g401 21:16 an adjusted-chart order wanted
    ダイチスイム and the sortie screen stayed open for minutes. After
    ``CARD_MISS_LIMIT`` readings the card is dropped with evidence; with no
    carry slot left every remaining card is dropped.
    """
    misses = mem.setdefault('card_miss', {})
    key = f"{order['step']}:{card}"
    misses[key] = misses.get(key, 0) + 1
    if misses[key] < CARD_MISS_LIMIT:
        return None
    misses.pop(key, None)
    wanted = _deploy_cards(order, mem)
    for picked in mem.get('picked') or ():
        if picked in wanted:
            wanted.remove(picked)
    # One entry per unselectable copy: with no carry slot left every remaining
    # planned copy goes, otherwise every copy of the missing card does.
    dropped = (list(wanted) if inventory['remaining'] == 0
               else [c for c in wanted if c == card])
    mem.setdefault('card_drop', {}).setdefault(order['step'], []).extend(dropped)
    context = {**_deploy_context(order, mem),
               'deviation_reason': f'{card}を選べないため携行せずに出撃する'}
    _record(mem, 'card_dropped', **context, card=card, dropped=dropped,
            observed_metric={'rows': [[r['card'], r['stock']] for r in inventory['rows']],
                             'remaining': inventory['remaining'],
                             # fewer than 4 rows is the whole stock; 4 may hide more
                             'complete_list': len(inventory['rows']) < 4,
                             'readings': CARD_MISS_LIMIT},
            reason='予定切り札の在庫・携行枠を確認できない状態が続いたため、その札を外して出撃を続ける')
    return []


def _card_proven_absent(card, inventory):
    """The readable panel proves this card is out of stock, not scrolled off.

    Rows are listed in card-ID order and depleted cards disappear. A card shown
    with stock 0, missing from a panel of fewer than four rows, or whose ID
    falls between two visible rows' IDs cannot be elsewhere in the list.
    g825 2-9 (2026-10-17 09:3x): 1-B1 wanted クースカン(13); the panel showed
    ブラッキー/フットバース/ノリウツール(11)/バルムンク(16) and the strict boss
    kit held for good.
    """
    rows = inventory['rows']
    if inventory['remaining'] == 0:
        return False
    shown = next((r for r in rows if r['card'] == card), None)
    if shown is not None:
        return shown['stock'] == 0
    ids = [CARD_IDS.get(r['card']) for r in rows]
    target = CARD_IDS.get(card)
    if target is None or None in ids or ids != sorted(ids):
        return False
    return len(rows) < 4 or min(ids) < target < max(ids)


def _selected_actor_guard(screen, mem, order):
    """A runtime sortie must show its actual named actor before confirmation.

    x16/80 y31 is the existing measured field/sortie status layout. A planned
    A on the list can miss; its stored name alone never authorizes card or
    departure confirmation. Untagged legacy calls acquire no scoped receipt.
    """
    from ..os_layer.roster import run_identity
    scoped = run_identity(mem.get('_run_identity')) is not None
    selected = (_sortie_general(order, mem) if scoped else
                (mem.get('sortie_general') or {}).get(order['step']))
    if order.get('source_probe') is True and (not selected or selected == NAME):
        (mem.get('general_override') or {}).pop(order['step'], None)
        (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
        return _hold_guarded_sortie(mem, order, 'sortie_actor_unconfirmed',
            '実一覧で選んだ将軍の証拠がないため配置確認の出撃を取り消す',
            {'selected': selected, 'observed': None, 'screen': screen.kind})
    if not scoped and (order['general'] != NAME and order.get('source_probe') is not True and not _is_boss_order(order, mem)
            or not selected or selected == NAME):
        return None
    row = next((line for line in screen.lines if line.y == 31), None)
    name = row.span(16, 80).strip() if row else None
    if (not row or row.span(80, 128) != 'しょうぐん' or not name
            or UNKNOWN in name or general_max_hp('しゅじんこう' if name == NAME else name) is None):
        misses = mem.setdefault('sortie_actor_miss', {})
        misses[order['step']] = misses.get(order['step'], 0) + 1
        if misses[order['step']] < 3:
            return _hold_deploy(screen, mem, order, '代役の出撃画面の本人名を読めないため保留')
        reason = '代役の出撃画面の本人名を3回で確認できず出撃を取り消す'
    elif name != selected:
        reason = f'選択予定の{selected}と実画面の{name}が違うため出撃を取り消す'
    else:
        (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
        return None
    (mem.get('general_override') or {}).pop(order['step'], None)
    (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
    return _hold_guarded_sortie(mem, order, 'sortie_actor_unconfirmed', reason,
                               {'selected': selected, 'observed': name, 'screen': screen.kind})


def _deploy_context(order, mem, *, expected_metric=None):
    general = _sortie_general(order, mem)
    context = {'general': general, 'planned_general': order['general'],
            'expected_metric': expected_metric,
            'strategy_variant': 'substitute_general' if general != order['general'] else mem.get('variant', 'chart'),
            'deviation_reason': (f"計画の{order['general']}に代わり{general}を出撃させる"
                                 if general != order['general'] else None)}
    retry = (mem.get('retry_context') or {}).get(order['step'])
    if retry:
        context.update(strategy_variant=retry.get('strategy_variant', 'retry_with_opening_cards'),
                       deviation_reason=retry.get('deviation_reason'),
                       expected_metric=retry.get('expected_metric'))
    rare_kit = (mem.get('rare_card_kit') or {}).get(order['step'])
    if rare_kit and 'キャトルミュー' in rare_kit:
        context.update(strategy_variant='rare_cattlemyu',
                       deviation_reason='実在庫のキャトルミューを活用するため携行札を変更')
    elif rare_kit and 'バルムンク' in rare_kit:
        context.update(strategy_variant='rare_balmung_boss',
                       deviation_reason='実在庫のバルムンクを第2話ボスの実測装備として携行')
    return context


def _deploy_input(screen, mem, order, actions, reason):
    # Retry navigation used to have no decision record, losing its context in
    # action_plan. Ordinary first-attempt records retain their existing shape.
    if (mem.get('retry_context') or {}).get(order['step']):
        _record(mem, 'sortie_input', **_deploy_context(order, mem), screen=screen.kind,
                observed_metric={'planned_buttons': [a.get('buttons') for a in actions]},
                reason=reason)
    return actions


def _hold_deploy(screen, mem, order, reason, *, card=None, carried=None):
    context = _deploy_context(order, mem, expected_metric={'cards': _deploy_cards(order, mem)})
    if not (mem.get('retry_context') or {}).get(order['step']):
        context.update(strategy_variant='sortie_cards_unclassified' if screen.kind == 'sortie_confirm'
                       else 'sortie_menu_unclassified', deviation_reason=reason)
    _record(mem, 'situation_held', **context, screen=screen.kind, card=card,
            observed_metric={'hand': screen.hand,
                             'candidates': [{'x': x, 'y': y, 'text': w[:80]} for x, y, w in _options(screen)[:32]],
                             'carried_cards_read': carried, 'confirmation': 'unclassified'},
            reason=reason)
    return []


def _move_general_pick(screen, mem, order):
    """Pick the general a move sends: never the donor's last one."""
    present = _present_generals(screen)
    if present is None:
        return _hold_deploy(screen, mem, order, '移動元の将軍一覧を読めないため保留')
    busy = _en_route(mem)[0]
    spare = sorted((g for g in present if g not in busy), key=lambda g: g == NAME)
    if len(present) < 2 or not spare:
        _finish_order(mem, 'failed', deviation_reason='移動元に残す将軍がいない',
                      observed_metric=present[:8], source=_source(order, mem),
                      reason='移動元の将軍が1人以下のため移動を取り消す（一覧は駐留として記録）')
        return [pad('b'), pad('b')]
    general = mem.get('general_override', {}).get(order['step'])
    if general not in spare:
        general = spare[0]
        mem.setdefault('general_override', {})[order['step']] = general
        _record(mem, 'move_general_picked', chart_step=order['step'], general=general,
                source=_source(order, mem), target=order['target'], observed_metric=present[:8],
                reason='移動元に1人以上残して主人公以外の将軍を空の城へ送る')
    move = menu_to(screen, general)
    if move is None:
        return _hold_deploy(screen, mem, order, '移動させる将軍へカーソルを合わせられないため保留')
    return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move],
                         '移動させる将軍を選択')


def _observe_garrison(screen, mem, order):
    """Remember who a castle's general list showed (``interim_candidates``)."""
    present = _present_generals(screen)
    castle = _source(order, mem)
    if present is None or not castle:
        return
    garrison = mem.setdefault('garrison', {})
    # Display-only evidence: decisions never consult this wall-clock field.
    mem.setdefault('garrison_observed_at', {})[castle] = time.time()
    mem['general_location_unknown'] = [g for g in mem.get('general_location_unknown') or ()
                                       if g not in present or not _general_visible(screen, g)]
    verification = mem.get('recruit_verification') or {}
    joined = (set(present) & set(verification.get('candidates') or ())) - set(
        verification.get('generals_before') or ())
    if joined:
        _record(mem, 'recruit_join_observed', castle=castle, month=verification.get('month'),
                observed_metric={'generals': sorted(joined)},
                reason='募集で紹介された将軍を在城一覧で確認し、採用後の配置を実測した')
        mem.pop('recruit_verification', None)
    if garrison.get(castle) != present:
        garrison[castle] = present
        _record(mem, 'garrison_seen', **_deploy_context(order, mem), castle=castle,
                observed_metric=present[:8], reason='出撃元の将軍一覧から駐留将軍を記録')
    if castle in _owned(mem):
        # A marching general the game stopped inside this castle is only
        # visible here: our own castles print no entry message. Continue his
        # order from this castle toward its original destination.
        _continue_transit_sorties(mem, castle, present)


def _check_source_castle(screen, mem, order):
    """Read the castle's name (ステータス) before sending anyone from it.

    g421 18:13: a Y jump to アルマムーン closed early, A opened the menu of
    the castle under the cursor - フーリック, where the hero stood - and the
    bot recorded どうし at アルマムーン and sent him to フーリック itself; the
    sortie went nowhere. Measured (isolated probe): ステータス shows
    「<name>じょう しゅうにゅう… しょうぐんNめい …」 beside the menu and B
    returns to it. ``None`` means verified: continue with しゅつげき.
    """
    step, source = order['step'], _source(order, mem)
    m = CASTLE_STATUS.search(screen.text)
    if m is None:
        if mem.get('castle_verified') == step:
            return None
        move = menu_to(screen, 'ステータス')
        if move is None:
            return None                   # unreadable menu: the old path holds with evidence
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move],
                             '出撃前に城のステータスで城名を確認')
    name = m.group(1)
    if name == source or STATUS_NAMES.get(source) == name:
        mem['castle_verified'] = step
        # Verification does not prove the status panel has closed. Its hand
        # is hidden while open, so attempting a sortie here can wait forever.
        # Keep closing only the positively identified panel until it is gone.
        return [pad('b')]
    mem.pop('castle_verified', None)
    cells = chart.castles(mem.get('chapter') or 0)
    label = next((k for k, v in STATUS_NAMES.items() if v == name and k in cells), name)
    if label in cells:
        mem['cursor'], mem['uncertain'] = list(cells[label]), False    # we know where we are now
    misses = mem.setdefault('source_miss', {})
    misses[step] = misses.get(step, 0) + 1
    _record(mem, 'source_castle_mismatch', chart_step=step, source=source,
            observed_metric={'castle': name, 'misses': misses[step]},
            reason=f'出撃元{source}のつもりで開いた城が{name}だったため出撃せず現在地を直して向かい直す')
    return [pad('b'), {'type': 'wait', 'ms': 500}, pad('b')]


def _keep_last_castle(screen, mem, order):
    """Never send out the last general standing in any castle of ours.

    g421 18:55: フーリック had fallen, アルマムーン was our only castle and どうし
    its only general; he was sent to ドミノーラ, the empty castle was taken at
    18:57 and with no castle left the game ended (title, game_over). The
    isolated probe then showed the same with two castles: エシャロット left
    アルマムーン and ゼウス left フーリック, both empty at once. A sortie whose
    list shows one general, while no other castle of ours is known to keep
    one, is cancelled and waits (bounded) for another general. ``None`` lets
    the sortie go on.
    """
    if _is_boss_order(order, mem) or order.get('purpose') == 'move':
        return None                        # the boss battle ends the chapter; a move keeps one (_move_general_pick)
    source = _source(order, mem)
    present = _present_generals(screen)
    if (present is None or len(present) != 1 or source not in _owned(mem)
            or not _few_castles(mem) or _staffed_elsewhere(mem, source)):
        return None
    mem['last_castle_hold'] = {'castle': source, 'tick': int(mem.get('tick') or 0)}
    mem['orders'][order['step']] = 'pending'
    mem['active'] = None
    mem['picked'] = []
    _record(mem, 'sortie_held_last_castle', chart_step=order['step'], source=source,
            observed_metric=present[:4],
            reason=f'{source}の将軍が{len(present)}人で他の自軍城に待機将軍がいないため、全城を空にして落城・ゲームオーバーにならないよう出撃しない')
    return [pad('b'), {'type': 'wait', 'ms': 300}, pad('b')]


def _hold_guarded_sortie(mem, order, decision, reason, observed):
    mem.setdefault('orders', {})[order['step']] = (
        'failed' if order['step'].startswith('I:') else 'pending')
    mem['active'] = None
    mem['picked'] = []
    mem.setdefault('sortie_general', {}).pop(order['step'], None)
    mem.setdefault('order_context', {}).pop(order['step'], None)
    _record(mem, decision, chart_step=order['step'], source=_source(order, mem),
            target=order['target'], observed_metric=observed, reason=reason)
    return [pad('b'), pad('b')]


def _keep_sortie_defender(screen, mem, order):
    if not _reserve_source_guard(order, mem):
        return None
    present = _present_generals(screen)
    if present is None:
        return _hold_deploy(screen, mem, order, '守備を残せる将軍一覧を読めないため出撃を保留')
    unavailable = _en_route(mem)[0] | set(mem.get('general_location_unknown') or ())
    available = set(present) - unavailable
    if len(available) >= 2:
        (mem.get('source_location_rechecks') or {}).pop(_source(order, mem), None)
        return None
    source = _source(order, mem)
    recheck = _source_location_recheck(mem, source)
    if recheck is not None:
        recheck['attempts'] = min(SOURCE_LOCATION_RECHECK_LIMIT, recheck['attempts'] + 1)
        mem.setdefault('source_location_rechecks', {})[source] = recheck
        return _hold_guarded_sortie(mem, order, 'sortie_held_source_defender',
            '所在未確認の将軍を守備人数に含めず取り消し、同じ一覧の再確認を上限付きで残す',
            {'present': present[:8], 'available': sorted(available),
             'location_recheck_attempts': recheck['attempts']})
    (mem.get('source_location_rechecks') or {}).pop(source, None)
    # The read list refutes "unplaced joiners stand here" for this castle
    # until the unplaced set changes (g684 3-1 16:09-16:10: the same order
    # was re-issued and cancelled every 10 s).
    refuted = mem.setdefault('unplaced_refuted_castles', {})
    previous = mem.get('unplaced_refuted') or {}
    if previous.get('castle'):
        refuted[previous['castle']] = dict(previous)
    mem['unplaced_refuted'] = {'castle': _source(order, mem), 'month': mem.get('month'),
                               'names': _unplaced_castle_generals(mem)}
    refuted[_source(order, mem)] = dict(mem['unplaced_refuted'])
    return _hold_guarded_sortie(mem, order, 'sortie_held_source_defender',
        '奪還・暫定攻撃の出撃元に待機将軍を1人残せないため出撃を取り消す',
        {'present': present[:8], 'available': sorted(available)})


def resume_sortie_step(screen: Screen, mem):
    """Close only the verified restored menu before fresh chapter/route survey."""
    pending = mem.get('resume_sortie_cancel')
    if not isinstance(pending, dict):
        return None
    if mem.get('chapter') and not mem.get('resume_chapter_pending'):
        mem.pop('resume_sortie_cancel', None)
        mem['world_map_due'] = True
        return None
    if screen.kind in {'battle', 'battle_menu', 'egg_battle_menu', 'egg_choice_menu',
                       'monster_menu', 'okunote_menu', 'attack_started', 'defense_started',
                       'field_encounter_started', 'month_menu'}:
        return None
    from ..os_layer.screen import resume_sortie_panel
    pending['readings'] = min(12, int(pending.get('readings') or 0) + 1)
    if screen.kind == 'castle_menu' and pending['readings'] < 12:
        move = menu_to(screen, 'ステータス')
        if move is not None:
            _record(mem, 'resume_sortie_cancel', screen=screen.kind,
                    observed_metric={'readings': pending['readings'], 'chapter': None,
                                     'read_only_choice': 'ステータス'},
                    reason='出撃を取り消した城の実ステータスを開いて章を確認する')
            return [pad('a')] if move == 'here' else [move]
    safe_cancel = (resume_sortie_panel(screen)
                   or screen.kind == 'general_list')
    if not safe_cancel or pending['readings'] >= 12:
        _record(mem, 'situation_held', screen=screen.kind,
                reason='復元後の出撃画面を閉じた証拠と章を確認できないため承認せず保留')
        return []
    _record(mem, 'resume_sortie_cancel', screen=screen.kind,
            observed_metric={'readings': pending['readings'], 'chapter': None},
            reason='章表示のない復元済み出撃メニューを承認せず閉じて再観測する')
    return [pad('b')]


def boss_stock_restart_step(screen: Screen, mem):
    """Keep ownership of a partly read sortie confirmation while backing out."""
    rows = {line.y: line for line in screen.lines}
    partial_confirmation = (screen.kind in {'yes_no', 'text', 'unknown'}
            and 31 in rows and rows[31].span(80, 128) == 'しょうぐん'
            and 47 in rows and rows[47].span(136, 176) == 'きりふだ'
            and 111 in rows and rows[111].span(160, 200) == 'うむッ!'
            and 127 in rows and rows[127].span(160, 208) == 'いかんッ!')
    if mem.get('boss_stock_cancelled'):
        if screen.kind == 'sortie_confirm' or partial_confirmation:
            _record(mem, 'situation_held', screen=screen.kind,
                    reason='取り消し上限後も同じ出撃確認が残るため承認せず保留')
            return []
        if screen.kind in {'map', 'map_target', 'castle_menu', 'general_list', 'card_select'}:
            mem.pop('boss_stock_cancelled', None)
    restarting = mem.get('boss_stock_restart')
    if not isinstance(restarting, dict):
        return None
    order = _order(mem)
    kind = screen.kind
    if order is None or restarting.get('step') != order['step']:
        mem.pop('boss_stock_restart', None)
        return None
    if kind == 'castle_menu':
        mem.pop('boss_stock_restart', None)  # full backout; normal source check resumes
        return None
    if kind not in {'general_list', 'card_select', 'sortie_confirm'}:
        # The action label can lose a glyph and classify as generic yes/no.
        # Retain only this measured status+carry+choice layout; unrelated
        # events, battle menus and monthly confirmations keep their owners.
        if not partial_confirmation:
            return None
    restarting['readings'] = int(restarting.get('readings') or 0) + 1
    if restarting['readings'] >= 12:
        mem.pop('boss_stock_restart', None)
        mem['boss_stock_cancelled'] = True
        return _hold_guarded_sortie(mem, order, 'boss_stock_restart_deferred',
            '選択画面の取り消し確認が上限に達したため、承認せず出撃を取り消す',
            {'screen': kind, 'readings': 12})
    return [pad('b')]


def deploy_step(screen: Screen, mem):
    restart = boss_stock_restart_step(screen, mem)
    if restart is not None:
        return restart
    order = _order(mem)
    kind = screen.kind
    if order is None:
        # Menus we did not open (e.g. confirm pressed by an earlier fallback).
        return [pad('b')]
    if kind == 'general_list' and (mem.get('orders') or {}).get(order['step']) != 'launched_unconfirmed':
        # A newly selected sortie may use a different actor. An interrupted
        # departure instead retains its evidence for source reconciliation.
        (mem.get('order_context') or {}).pop(order['step'], None)
        (mem.get('sortie_general') or {}).pop(order['step'], None)
    if (_broken_hero_order(order, mem)
            and kind != 'general_list'
            and not (kind == 'castle_menu' and _hero_source_alternative(order, mem))):
        return _cancel_broken_hero_sortie(mem, order)
    if _retake_reserved(order, mem):
        return _hold_guarded_sortie(mem, order, 'sortie_held_target_reserved',
            '同じ奪還先へ向かう部隊が既にいるため重複出撃を取り消す',
            {'target': order['target']})
    if kind != 'general_list' and _source_spare_missing(order, mem, allow_probe=kind == 'castle_menu'):
        return _hold_guarded_sortie(mem, order, 'sortie_held_source_defender',
            '出撃元の記録で待機将軍を1人残せないため出撃を取り消す',
            {'present': (mem.get('garrison') or {}).get(_source(order, mem))})
    if kind == 'castle_menu':
        checked = _check_source_castle(screen, mem, order)
        if checked is not None:
            return checked
        (mem.get('source_miss') or {}).pop(order['step'], None)
        move = menu_to(screen, 'しゅつげき')
        if move is None:
            # No hand (or unreadable menu): hold with evidence instead of a
            # silent empty plan that never advances and never explains itself.
            return _hold_deploy(screen, mem, order, '出撃メニューのしゅつげきをカーソルで判定できないため保留')
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move],
                             '出撃メニューを選択')
    if kind == 'general_list':
        _observe_garrison(screen, mem, order)
        if (mem.get('orders') or {}).get(order['step']) == 'launched_unconfirmed':
            return _verify_sortie_source(screen, mem, order)
        guard = _keep_sortie_defender(screen, mem, order)
        if guard is not None:
            return guard
        guard = _keep_last_castle(screen, mem, order)
        if guard is not None:
            return guard
        if order.get('purpose') == 'move':
            return _move_general_pick(screen, mem, order)
        # A readable list is the selection evidence. Boss orders used to
        # force the chart hero and discard the actual substitute identity.
        # Prefer an idle companion when the planned attacker is the hero;
        # preserve a chart's explicit non-hero role.
        if order['general'] == NAME or _is_boss_order(order, mem) or order.get('source_probe') is True:
            mem.setdefault('sortie_general', {}).pop(order['step'], None)
            mem.setdefault('order_context', {}).pop(order['step'], None)
            (mem.get('sortie_actor_miss') or {}).pop(order['step'], None)
            present = _present_generals(screen)
            if (present is None or len(present) != len(set(present))
                    or any(not _general_visible(screen, g) or general_max_hp(
                        'しゅじんこう' if g == NAME else g) is None for g in present)):
                return _hold_deploy(screen, mem, order, '出撃一覧の本人・カーソルを確認できないため保留')
            candidates = (_order_hero_alternatives(order, mem, present)
                          if order['general'] == NAME or _is_boss_order(order, mem)
                          or order.get('source_probe') is True else [])
            if _is_boss_order(order, mem) and not candidates:
                return _hold_guarded_sortie(mem, order, 'boss_companion_required',
                    '十分な能力の代役を実一覧で確認できないため主人公を出撃させず準備を待つ',
                    {'present': present[:8], 'hero_forbidden': True})
            general = candidates[0] if candidates else order['general']
            # A stale override is not a receipt, and a companion selected in
            # this list must not inherit the hero's depleted-egg guard.
            mem.setdefault('general_override', {}).pop(order['step'], None)
            if general != order['general']:
                mem['general_override'][order['step']] = general
            if general not in present or general in _en_route(mem)[0]:
                holds = mem.setdefault('boss_absent', {})
                holds[order['step']] = holds.get(order['step'], 0) + 1
                if holds[order['step']] >= BOSS_ABSENT_LIMIT:
                    holds.pop(order['step'], None)
                    (mem.get('target_cancel') or {}).pop(order['step'], None)
                    _finish_order(mem, 'failed', deviation_reason=f"{general}が出撃元で待機していない",
                                  observed_metric=present[:8], source=_source(order, mem),
                                  reason='出撃できる本人を有限回の一覧読取で確認できず指示を取り消す')
                    return [pad('b'), pad('b')]
                return _hold_deploy(screen, mem, order, '出撃できる本人が一覧にいないため保留')
            if _broken_hero_order(order, mem):
                return _cancel_broken_hero_sortie(mem, order)
            if _boss_egg_depleted(order, mem):
                return _hold_guarded_sortie(mem, order, 'boss_sortie_cancelled_for_egg',
                    '代役がいないため主人公の卵回復を待つ', {'general': NAME, 'egg_uses': mem['egg_uses'][NAME]})
            (mem.get('boss_absent') or {}).pop(order['step'], None)
            move = menu_to(screen, general)
            if move is None:
                return _hold_deploy(screen, mem, order, '選択本人へカーソルを合わせられないため保留')
            if move == 'here':
                mem['sortie_general'][order['step']] = general
                if general != order['general']:
                    _record(mem, 'hero_priority_selected', **_deploy_context(order, mem),
                            observed_metric={'present': present[:8], 'selected': general},
                            reason='主人公の敗北でゲームオーバーになる危険を避け、実在する待機将軍を先発にする')
                return _deploy_input(screen, mem, order, [pad('a')], '実一覧の出撃本人を選択')
            return _deploy_input(screen, mem, order, [move], '主人公以外の待機将軍を優先して選択カーソルを移動')
        empty_list = 'おりません' in screen.text
        if not screen.hand and not empty_list:
            return _hold_deploy(screen, mem, order, '将軍選択カーソルを判定できないため入力を保留')
        general = mem.get('general_override', {}).get(order['step'], order['general'])
        move = menu_to(screen, general) if screen.hand else None
        if move is None:
            # The chart's general is not at this castle (routed or lost).
            # Whoever is actually here goes instead, so the castle, later
            # steps and the boss condition stay reachable.
            # An empty list message is not a general name.
            ui = {'しゅつげき', 'ステータス', 'しょうぐんは', 'おりません', 'おりません……'}
            present = [] if empty_list else [w for x, y, w in _options(screen) if x > 100 and w not in ui
                                             and not re.search(r'\d', w) and 'おりません' not in w]
            present = [g for g in present if g not in _en_route(mem)[0]]
            # Never send the hero in place of a missing general: a lost
            # attack ends the game, and the hero alone at the source would
            # leave it empty (2-C1 in g421 sent どうし 22 times this way).
            # The source fallback or the order's failure takes over instead.
            if NAME in present:
                present.remove(NAME)
                _record(mem, 'hero_substitute_refused', chart_step=order['step'],
                        strategy_variant='hero_substitute_refused',
                        observed_metric={'present': [NAME, *present][:8]},
                        reason='計画の将軍がいないため、主人公を代役にせず他の将軍か出撃元の変更を待つ')
            if present and not mem.get('general_override', {}).get(order['step']):
                mem.setdefault('general_override', {})[order['step']] = present[0]
                _record(mem, 'order_substitute', strategy_variant='substitute_general',
                        deviation_reason=f"{order['general']}が出撃元にいないため{present[0]}が代わりに出撃",
                        observed_metric=present[:8], expected_metric=f"{order['target']}の占領",
                        reason='後続手順とボス条件を満たすため')
                move = menu_to(screen, present[0])
                return [pad('a')] if move == 'here' else [move] if move else []
            if (not mem.get('source_override', {}).get(order['step'])
                    and chart.home_castle(mem.get('chapter') or 1) in _owned(mem)):
                mem.setdefault('source_override', {})[order['step']] = chart.home_castle(
                    mem.get('chapter') or 1)
                mem.setdefault('general_override', {}).pop(order['step'], None)
                mem['orders'][order['step']] = 'pending'
                _record(mem, 'order_source_changed', strategy_variant='source_fallback',
                        deviation_reason=f"{order['source']}に出撃できる将軍がいない",
                        observed_metric=present[:8], reason='本城から出撃し直す')
                mem['active'] = None
                return [pad('b'), pad('b')]
        if move is None:
            _finish_order(mem, 'failed', deviation_reason=f"{order['general']}が出撃一覧に見えない",
                          observed_metric=[w for _, _, w in _options(screen)][:12],
                          reason='チャートの将軍が出撃元の城にいない')
            return [pad('b')]
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move], '出撃将軍を選択')
    if kind in {'card_select', 'sortie_confirm'}:
        if (_is_boss_order(order, mem) and _sortie_general(order, mem) == NAME
                and (mem.get('orders') or {}).get(order['step']) != 'launched_unconfirmed'):
            return _hold_guarded_sortie(mem, order, 'boss_companion_required',
                '主人公のボス出撃を取り消し、十分な戦力の代役を待つ',
                {'general': NAME, 'screen': kind, 'hero_forbidden': True})
        guard = _selected_actor_guard(screen, mem, order)
        if guard is not None:
            return guard
    if kind in {'card_select', 'sortie_confirm'} and _boss_egg_depleted(order, mem):
        # observe_events reads the egg row before this decision. Cancel a
        # sortie that was chosen while its quantity was still unknown.
        mem.setdefault('orders', {})[order['step']] = 'pending'
        mem['active'] = None
        mem['picked'] = []
        mem.setdefault('order_context', {}).pop(order['step'], None)
        _record(mem, 'boss_sortie_cancelled_for_egg', chart_step=order['step'],
                observed_metric={'general': NAME, 'egg_uses': mem['egg_uses'][NAME],
                                 'screen': kind}, expected_metric={'egg_uses': 4},
                reason='出撃画面で主人公の卵の消耗を確認したため、ボス出撃を取り消して回復を待つ')
        return [pad('b'), pad('b')]
    if kind in {'card_select', 'sortie_confirm'} and _is_boss_order(order, mem):
        mem.setdefault('order_context', {}).pop(order['step'], None)
        if not (mem.get('sortie_general') or {}).get(order['step']):
            return _hold_deploy(screen, mem, order, 'ボス出撃の選択本人を確認できないため保留')
    if kind == 'card_select':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        picked = mem.setdefault('picked', [])
        wanted = _deploy_cards(order, mem)
        for card in picked:
            if card in wanted:
                wanted.remove(card)
        card = wanted[0] if wanted else None
        inventory = _measured_card_select(screen)
        if inventory is None:
            if not _strict_boss_cards(order, mem):
                # An unreadable panel must not hold the sortie forever (g454
                # 10:02: an unknown card name stalled シェーブル's sortie). The
                # base boss kit keeps its strict hold.
                misses = mem.setdefault('card_unreadable', {})
                misses[order['step']] = misses.get(order['step'], 0) + 1
                if misses[order['step']] >= CARD_UNREADABLE_LIMIT:
                    misses.pop(order['step'], None)
                    _finish_order(mem, 'failed', deviation_reason='切り札一覧を実測構造として読めない',
                                  observed_metric={'screen': screen.kind,
                                                   'readings': CARD_UNREADABLE_LIMIT},
                                  reason='切り札一覧を読み取れないため出撃を取り消して次の指示へ進む')
                    return [pad('b'), pad('b')]
            return _hold_deploy(screen, mem, order,
                                '切り札一覧の名前・数量・配置またはカーソルが実測構造と一致しないため保留', card=card)
        mem.get('card_unreadable', {}).pop(order['step'], None)
        _observe_card_stock(mem, inventory)
        boss_actions = _chapter_two_balmung_inventory(mem, order, inventory)
        if boss_actions is not None:
            return boss_actions
        rare_actions = _rare_card_inventory(screen, mem, order, inventory)
        if rare_actions is not None:
            return rare_actions
        if _strong_card_inventory(mem, order, inventory, wanted):
            # 携行枠に1枚追加した: この観測の選択対象も並べ直す(予定札が先頭)。
            wanted = _deploy_cards(order, mem)
            for card in picked:
                if card in wanted:
                    wanted.remove(card)
        strong = (mem.get('strong_card_kit') or {}).get(order['step'])
        if not wanted:
            return _deploy_input(screen, mem, order, [pad('b')], '予定切り札の選択入力後に携行確認へ進む')
        # Pick what is on screen first: a full 4-row panel scrolls, and a card
        # below it (g421 13:27: クースカン, bought x4) is reached by scrolling.
        # 予定札(チャート/調整)が残っている間は、追加携行の強い切り札を先に選ばない:
        # 空き枠に足した札が予定札より先に消費されるのを避ける。
        planned = [c for c in wanted if c != strong] or wanted
        shown = {r['card'] for r in inventory['rows'] if r['stock'] > 0}
        card = next((c for c in planned if c in shown), planned[0])
        row = next((row for row in inventory['rows'] if row['card'] == card), None)
        if (row is None and len(inventory['rows']) == 4 and inventory['remaining'] > 0
                and not _card_proven_absent(card, inventory)
                and int((mem.get('card_scroll') or {}).get(order['step'], 0)) < (RARE_SCAN_LIMIT if (mem.get('rare_card_kit') or {}).get(order['step'])
                   else CARD_SCROLL_LIMIT)):
            scrolls = mem.setdefault('card_scroll', {})
            scrolls[order['step']] = scrolls.get(order['step'], 0) + 1
            _record(mem, 'card_scroll', **_deploy_context(order, mem), card=card,
                    observed_metric={'rows': [[r['card'], r['stock']] for r in inventory['rows']],
                                     'selected_y': inventory['selected_y'], 'scrolls': scrolls[order['step']]},
                    reason='予定切り札が一覧に見えないため下へ送って隠れた行を表示する')
            direction = ('up' if CARD_IDS.get(card, 99) <
                         min(CARD_IDS[r['card']] for r in inventory['rows']) else 'down')
            return [pad(direction)]
        if row is None or row['stock'] == 0 or inventory['remaining'] == 0:
            proven = _card_proven_absent(card, inventory)
            if proven:
                # Measured, even for a card never listed before: a month plan
                # buys a short boss card back (strategy.boss_cards).
                mem.setdefault('card_stock', {})[card] = 0
                (mem.get(UNSEEN) or {}).pop(card, None)
            # The base boss kit stays strict unless the panel proves the card
            # is gone: holding for a card that does not exist never ends.
            if not _strict_boss_cards(order, mem) or proven:
                dropped = _drop_card(screen, mem, order, card, inventory)
                if dropped is not None:
                    return dropped
            return _hold_deploy(screen, mem, order,
                                '予定切り札の正数在庫と携行余枠を確認できないため選択せず保留', card=card)
        if inventory['selected_y'] == row['y']:
            picked.append(card)
            _record(mem, 'card_pick', **_deploy_context(order, mem), card=card,
                    observed_metric={'stock': row['stock'], 'remaining': inventory['remaining'],
                                     'inventory_evidence': inventory['inventory_evidence'],
                                 'observed_rows': inventory['observed_rows']},
                    resulting_event='selection_planned_not_yet_confirmed', reason=order['note'])
            return [pad('a')]
        move = pad('down' if row['y'] > inventory['selected_y'] else 'up')
        selected = next(item for item in inventory['rows'] if item['y'] == inventory['selected_y'])
        _record(mem, 'sortie_input',
                **_deploy_context(order, mem, expected_metric={'card': card, 'goal': '予定切り札へカーソルを合わせる'}),
                screen=screen.kind, card=card,
                observed_metric={'desired_card': card, 'selected_y': selected['y'],
                                 'selected_card': selected['card'], 'target_y': row['y'],
                                 'target_stock': row['stock'], 'remaining': inventory['remaining'],
                                 'inventory_evidence': inventory['inventory_evidence'],
                                 'observed_rows': inventory['observed_rows']},
                reason='実測配置の正数在庫と携行余枠を確認し、予定切り札へカーソルを移動')
        return [move]
    if kind == 'sortie_confirm':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        empty_receipt = _empty_sortie_inventory(screen)
        slot_receipt = _sortie_inventory(screen)
        carried = [] if empty_receipt else slot_receipt
        inventory_evidence = ('measured_empty_sortie' if empty_receipt else
                              'measured_single_card_sortie' if slot_receipt and len(slot_receipt) == 1 else
                              'structured_card_slots')
        ambiguous = carried is None
        want = sorted(_deploy_cards(order, mem))
        got = sorted(carried) if carried is not None else None
        if ambiguous or got != want:
            misses = mem.setdefault('sortie_confirm_miss', {})
            misses[order['step']] = misses.get(order['step'], 0) + 1
            if (not _strict_boss_cards(order, mem)
                    and misses[order['step']] >= SORTIE_CONFIRM_LIMIT):
                if carried is None:
                    misses.pop(order['step'], None)
                    _finish_order(mem, 'failed', deviation_reason='出撃確認の携行札を読めない',
                                  observed_metric={'carried': None},
                                  reason='出撃確認を読み取れないため出撃を取り消して次の指示へ進む')
                    return [pad('b'), pad('b')]
                if misses[order['step']] == SORTIE_CONFIRM_LIMIT:
                    # A readable panel is the truth: approve the shown kit.
                    _record(mem, 'sortie_kit_mismatch',
                            **_deploy_context(order, mem, expected_metric=want),
                            carried=carried,
                            observed_metric={'carried': carried, 'planned': want},
                            reason='読み取れた携行札を実際の携行として出撃を承認（計画と不一致）')
            else:
                return _hold_deploy(screen, mem, order,
                                    '携行切り札の読取が不確実または計画と不一致のため出撃承認を保留', carried=carried)
        else:
            mem.get('sortie_confirm_miss', {}).pop(order['step'], None)
        move = menu_to(screen, 'うむッ!')
        if move is None:
            return _hold_deploy(screen, mem, order, '出撃確認カーソルまたは承認項目を読めないため保留', carried=carried)
        if move == 'here':
            context = _deploy_context(order, mem, expected_metric=want)
            from ..strategy.execution import confirm_departure_order
            from ..os_layer.roster import run_identity
            source = _source(order, mem)
            departure = confirm_departure_order(order, mem, context['general'], source, carried)
            if run_identity(mem.get('_run_identity')) is not None and departure is None:
                return _hold_guarded_sortie(mem, order, 'sortie_actor_unconfirmed',
                    '確認した将軍・出撃元・持ち札が一致しないため出撃を取り消す',
                    {'general': context['general'], 'source': source})
            mem['order_context'][order['step']] = {
                'strategy_variant': context['strategy_variant'],
                'deviation_reason': context['deviation_reason'],
                'actual_general': context['general'], 'planned_general': order['general'],
                'expected_metric': (context['expected_metric'] if (mem.get('retry_context') or {}).get(order['step'])
                                    else {'general': order['general'], 'cards': want}),
                'observed_metric': {'general': context['general'], 'cards': got},
                **({'departure_order': departure} if departure is not None else {})}
            _record(mem, 'sortie_confirm', **context, cards=carried,
                    inventory_evidence=inventory_evidence,
                    observed_metric=got,
                    reason='出撃確認で読み取った切り札が計画と一致したため承認を予定')
            mem['sortie_attempt'] = {'step': order['step'], 'target_seen': False,
                **({'confirmation_tick': departure['departure_binding']['confirm_tick']}
                   if departure is not None else {})}
            mem['cursor'] = list(chart.castles(mem['chapter'])[source])
            mem['uncertain'] = False      # the target marker starts on the source castle
            mem.pop('nav_search', None)
            mem.pop('nav_search_leg', None)
            return [pad('a')]
        return _deploy_input(screen, mem, order, [move], '携行品一致を確認したため出撃承認項目へ移動')
    return []
