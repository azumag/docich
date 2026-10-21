"""手順層: merchant lists, quantity input and soldier discharge."""
from __future__ import annotations

import re

from ..os_layer.screen import Screen
from ... import hanjuku_chart as chart
from ...hanjuku_reference import weakest_general
from ..decision_log import _record
from .egg_recovery import MONTH_SUB_EXIT_KINDS
from ..os_layer.commands import menu_to, pad
from ..os_layer.font import UNKNOWN
from ..os_layer.readers import month_menu_ready
from ..os_layer.roster import fixed_wage
from ..strategy.bargains import merchant_card_spendable, plan_bargains
from ..strategy.boss_cards import note_card_purchase
from ..strategy.economy import (month_chart_step, MONTH_SUB_MENU_WAIT, SOLDIER_ENOUGH, _army_count_increment, _egg_before_soldiers, _limit_soldier_refill,
                                _merchant_spendable, _month_key, _refresh_egg_priority, _reserve_general_repair,
                                _soldier_count_current, _soldier_count_scope, _soldiers_enough, refill_outcome)


def _merchant_quote_decline(screen, mem, shop, want, reason):
    # Close the rejected quote and its digit editor instead of reopening the
    # same cached target on the next frame. Other cards/owners/reserves stay.
    if shop.get('items') and shop['items'][0][0] == want[0]:
        shop['items'].pop(0)
    shop['want'] = None
    _record(mem, 'buy_skip', card=want[0], qty=want[1], price=want[2],
            gold=(screen.header or {}).get('gold'), deviation_reason=reason,
            reason='商人の実見積・同月所持金と、全賃金・未払回復等の予約を照合できないため購入を見送る')
    return [pad('b')]


def shop_step(screen: Screen, mem):
    shop = mem.get('shop')
    kind = screen.kind
    if kind == 'shop_exit_confirm':
        move = menu_to(screen, 'うむッ!')
        if shop:
            shop['merchant_done'] = True
        # Without a readable hand, the measured default cursor is うむッ (#960).
        return [move] if move and move != 'here' else [pad('a')]
    if kind == 'shop_quantity_prompt':
        return [pad('a')]
    if kind == 'shop_quantity':
        return quantity_step(screen, mem)
    # shop_list
    gold = (screen.header or {}).get('gold')
    if not shop or shop.get('key') != _month_key(screen.header):
        return [pad('b')]
    spendable = _merchant_spendable(mem, shop, screen.header)
    if spendable is None:
        return [pad('b')]  # a faded header is not permission or evidence of missing stock
    listed = {}
    for line in screen.lines:
        joined = ''.join(line.words(120, 256))
        # Numeric cash headers (for example 300G) are not product names.
        m = re.match(r'^([^\d\s]+)(\d+)G$', joined)
        if m:
            listed[m.group(1)] = int(m.group(2))
    plan_bargains(mem, shop, screen.header, listed=listed)
    while shop['items']:
        name, qty = shop['items'][0]
        price = listed.get(name)
        available = merchant_card_spendable(mem, shop, screen.header, name, price)
        if (type(qty) is not int or qty <= 0 or price is None or price <= 0
                or available is None or price > available):
            _record(mem, 'buy_skip', card=name, qty=qty, price=price, gold=gold,
                    deviation_reason='invalid_quantity' if type(qty) is not int or qty <= 0
                                     else 'not_listed' if price is None else 'reserved_funds',
                    reason='商人の品揃えまたは全賃金・未払回復等を保持した購入予算の制約')
            shop['items'].pop(0)
            continue
        n = min(qty, available // price)
        shop['items'][0] = [name, n]
        shop['want'] = [name, n, price]
        move = menu_to(screen, name)
        return [pad('a')] if move == 'here' else [move] if move else []
    return [pad('b')]


def _observe_soldier_receipt(screen, mem, directly_seen=None):
    """Add a refill once, after returning with its exact 1G/person payment."""
    shop = mem.get('shop') or {}
    receipt = shop.get('soldier_receipt') or {}
    if receipt.get('status') != 'pending':
        return
    header = screen.header
    month = _month_key(header) if header else None
    gold = (header or {}).get('gold')
    before, qty = receipt.get('gold_before'), receipt.get('qty')
    same_month = month == receipt.get('key') and month is not None
    left = (screen.kind in MONTH_SUB_EXIT_KINDS
            or (month is not None and not same_month))
    paid = (not left and same_month and type(before) is int and type(gold) is int
            and type(qty) is int and qty > 0 and before - gold == qty)
    if paid:
        receipt['payment_seen'] = True
        if directly_seen is not None:
            receipt['direct_total_after_payment'] = directly_seen
    if not left and not month_menu_ready(screen):
        return
    if not left and not paid and (gold is None or gold == before):
        receipt['menu_wait'] = int(receipt.get('menu_wait') or 0) + 1
        if receipt['menu_wait'] < MONTH_SUB_MENU_WAIT:
            return
    receipt['status'] = 'paid' if paid else 'unverified'
    if paid and directly_seen is None and type(receipt.get('direct_total_after_payment')) is int:
        directly_seen = receipt['direct_total_after_payment']
        mem['soldiers_seen'] = directly_seen
        mem['soldiers_seen_key'] = month
        mem['soldiers_seen_basis'] = 'screen_total'
    count_before = receipt.get('soldiers_before')
    added = paid and directly_seen is None and type(count_before) is int
    if added:
        mem['soldiers_seen'] = count_before + qty
        mem['soldiers_seen_key'] = month
        mem['soldiers_seen_basis'] = 'observed_plus_paid_refill'
    elif directly_seen is None:
        # Missing payment or a stale/missing baseline cannot establish the
        # army total. Retain the old value for history, but not its freshness.
        mem.pop('soldiers_seen_key', None)
        mem['soldiers_seen_basis'] = 'unverified_refill'
    _record(mem, 'soldier_refill_receipt', month=receipt.get('key'),
            observed_metric={'qty': qty, 'gold_before': before, 'gold_after': gold,
                             'soldiers_before': count_before,
                             'soldiers_after': mem.get('soldiers_seen')
                             if mem.get('soldiers_seen_key') == month and same_month else None,
                             'basis': mem.get('soldiers_seen_basis')},
            resulting_event='paid' if paid else 'unverified',
            reason=('兵士補充の支払いを確認し、当月に読んだ兵士数へ購入人数を一度だけ加算'
                    if added else '兵士補充後の実人数表示を優先して記録'
                    if paid and directly_seen is not None else
                    '購入または購入前の兵士数を照合できないため人数を推測で加算しない'))


def quantity_step(screen: Screen, mem, soldiers=False):
    shop = mem.get('shop') or {}
    if soldiers:
        # Keep full wage/egg/repair clamps even while the free inspection is
        # in charge: its early return must not retain an unsafe saved budget.
        _reserve_general_repair(mem, shop, (screen.header or {}).get('gold'))
        _refresh_egg_priority(mem, shop, screen.header)
        _limit_soldier_refill(mem, shop, screen.header, payment=True)
        state = mem.get('army_count_check')
        scope = _soldier_count_scope(mem, screen.header)
        if mem.get('_run_identity') is not None and scope is None:
            return []  # a faded month/header cannot authorize a runtime purchase
        if (scope is not None and isinstance(state, dict) and state.get('scope') == scope
                and state.get('stage') == 'measured'
                and not _soldier_count_current(mem, screen.header)
                and not shop.get('soldier_receipt')):
            # A prior inspection is not authority to pay after its actual
            # total becomes unreadable. Preserve the exhausted read budget.
            state.update(stage='closing', result='unverified')
            refill_outcome(mem, shop, 'unverified', reason='確認後の実兵数が未読になったため補充を確定せず中止')
            shop['soldiers_done'] = True
            _record(mem, 'army_count_unverified', month=scope[-1],
                    reason='確認後の実兵数が未読になったため補充を確定せず戻る。確認予算を増やさない')
        if (scope is not None and not _soldier_count_current(mem, screen.header)
                and not shop.get('soldier_receipt')
                and (not isinstance(state, dict) or state.get('scope') != scope)):
            state = mem['army_count_check'] = {'scope': scope, 'stage': 'requested', 'observations': 0}
            mem.pop('soldiers_seen_key', None)
        if (scope is not None and isinstance(state, dict) and state.get('scope') == scope
                and state.get('stage') == 'unverified'
                and not _soldier_count_current(mem, screen.header)):
            refill_outcome(mem, shop, 'unverified', reason='実兵数を確認できない補充画面では数量を確定せず中止')
            shop['soldiers_done'] = True
            state.update(stage='closing', result='unverified')
            return [pad('b')]  # a reopened unread editor still cannot authorize payment
        if (scope is not None and isinstance(state, dict) and state.get('scope') == scope
                and state.get('stage') != 'measured'):
            if state.get('stage') not in ('opening', 'requested', 'quantity_requested', 'closing'):
                state.update(stage='closing', result='unverified')
            if state.get('stage') == 'closing':
                return [pad('b')] if _army_count_increment(state, 'exit_presses', 4) <= 3 else []
            if _soldier_count_current(mem, screen.header):
                state.update(stage='closing', result='measured')
                return [pad('b')]
            if _army_count_increment(state, 'quantity_reads', 4) >= 4:
                state.update(stage='closing', result='unverified')
                refill_outcome(mem, shop, 'unverified', reads=state['quantity_reads'],
                               reason='補充画面で実人数を有限回読めないため数量を確定せず中止')
                _record(mem, 'army_count_unverified', month=scope[-1],
                        reason='実人数を読み取れないため補充の確定をせず戻る。兵数は未確認のまま保持')
                return [pad('b')]
            return []
        if _egg_before_soldiers(shop) and not shop.get('soldier_receipt'):
            # This is where the army total first becomes visible. Leave the
            # old quantity plan before sending a purchase confirmation.
            return [pad('b')]
        if _soldiers_enough(mem, shop, screen.header) and not shop.get('soldier_receipt'):
            # Owner (2026-10-11): そもそも兵士も十分いるから兵士も買わなくていい
            # (g684 2-12: 79 read, 75 more bought, 105G -> 30G).
            if not shop.get('soldiers_done'):
                _record(mem, 'soldier_refill_skip', month=shop.get('key'),
                        observed_metric={'soldiers': mem.get('soldiers_seen'),
                                         'planned': shop.get('soldiers'), 'enough': SOLDIER_ENOUGH},
                        reason='当月の兵士数が十分なため兵士補充を行わず資金を残す')
            shop['soldiers'] = 0
            shop['soldiers_done'] = True
            # g684 3-7: 133 soldiers meant no refill, but the saved 56G
            # pre-recruit budget still blocked recruitment at 86G. The
            # measured sufficient army fulfils that necessity without a
            # payment receipt; recheck the real spare cash on menu return.
            shop['soldiers_before_recruit_done'] = True
            if shop.get('recruit') == 'deferred_cash':
                shop['recruit'] = 'pending'
            return [pad('b')]
        receipt = shop.get('soldier_receipt') or {}
        if scope is not None and receipt:
            if (receipt.get('key') != scope[-1] or type(receipt.get('qty')) is not int
                    or not 0 < receipt['qty'] <= 99
                    or type(receipt.get('gold_before')) is not int
                    or receipt['gold_before'] < 0):
                return [pad('b')]
            if receipt.get('status') == 'pending':
                # Wait for the actual debit once; an unchanged editor must
                # neither repeat A nor strand this month indefinitely.
                waits = receipt.get('quantity_waits', 0)
                if type(waits) is not int or not 0 <= waits < 3:
                    receipt['status'] = 'unverified'
                    refill_outcome(mem, shop, 'unverified', qty=receipt.get('qty'),
                                   reason='確定入力後も数量画面が残り支払いを照合できない。補充できたとは扱わない')
                    shop['soldiers_done'] = True
                    return [pad('b')]
                receipt['quantity_waits'] = waits + 1
                return []
        if scope is not None and shop.get('soldiers_done') and not receipt:
            return [pad('b')]  # a saved done flag is not a new payment request
        if receipt.get('status') in ('paid', 'unverified') or receipt.get('payment_seen'):
            return [pad('b')]
        # Digits sit at x=216 (tens) and x=224 (ones). The hand on the ones
        # digit hides the tens digit, so keep the last tens digit seen.
        line = next((l for l in screen.lines if 'かわりますぞ' in l.known.replace(' ', '')), None)
        cells = dict(line.cells) if line else {}
        ones_ch, tens_ch = cells.get(224), cells.get(216)
        if tens_ch and tens_ch.isdigit() and screen.hand and screen.hand[0] < 200:
            shop['soldier_tens'] = int(tens_ch)
        tens_seen = shop.get('soldier_tens')
        if ones_ch and ones_ch.isdigit() and tens_seen is not None:
            value = tens_seen * 10 + int(ones_ch)
        else:
            value = None
            if screen.hand and screen.hand[0] >= 200:
                return [pad('left')]     # reveal the tens digit first
        target = shop.get('soldiers', 0)
    else:
        m = re.search(r'(\d+)こで', screen.text)
        value = int(m.group(1)) if m else None
        want = shop.get('want') or [None, 0, 0]
        target = want[1]
        if screen.hand and 'うむッ' in screen.text and screen.selected in ('うむッ!', 'いかんッ!'):
            if value == target and target:
                quotes = re.findall(r'(\d+)こで(\d+)Gになりまんな', ''.join(screen.text.split()))
                spendable = merchant_card_spendable(mem, shop, screen.header, want[0], want[2])
                if (type(target) is not int or target <= 0
                        or type(want[2]) is not int or want[2] <= 0
                        or len(quotes) != 1 or int(quotes[0][0]) != value
                        or int(quotes[0][1]) != value * want[2]
                        or spendable is None or int(quotes[0][1]) > spendable):
                    return _merchant_quote_decline(screen, mem, shop, want, 'quote_or_reserved_funds')
                move = menu_to(screen, 'うむッ!')
                if move == 'here':
                    bought = shop.setdefault('bought', [])
                    bought.append([want[0], value, want[2]])
                    extra = want[0] in (shop.get('bargain_cards') or [])
                    _record(mem, 'buy', card=want[0], qty=value, price=want[2],
                            chart_step=month_chart_step(mem),
                            strategy_variant='spare_cash_strong_cards' if extra else shop.get('variant', 'chart'),
                            reason=('必要資金・予備費・買い足し上限を再確認し、安い強力札の購入を確定する'
                                    if extra else 'チャート/予算計画どおりの個数を確認して購入'))
                    note_card_purchase(mem, want[0], value)  # a boss card returns to its kit
                    if shop.get('items') and shop['items'][0][0] == want[0]:
                        shop['items'].pop(0)
                    shop['want'] = None
                    return [pad('a')]
                return [move] if move else []
            return [pad('b')]
    if value is None or not screen.hand:
        return []
    if not target:
        return [pad('b')]
    # Digit editor: hand x>=200 is the ones digit, a smaller x the tens digit.
    ones_selected = screen.hand[0] >= 200
    tens, ones = divmod(value % 100, 10)
    want_tens, want_ones = divmod(min(target, 99), 10)
    if tens != want_tens:
        if ones_selected:
            return [pad('left')]
        if soldiers:
            shop['soldier_tens'] = None      # re-read after the change
        return [pad('up' if (want_tens - tens) % 10 <= 5 else 'down')]
    if ones != want_ones:
        if not ones_selected:
            return [pad('right')]
        return [pad('up' if (want_ones - ones) % 10 <= 5 else 'down')]
    if soldiers:
        shop['soldiers_done'] = True
        if not shop.get('soldier_receipt'):
            header = screen.header
            key = _month_key(header) if header else None
            count = mem.get('soldiers_seen')
            fresh = (key is not None and key == shop.get('key')
                     and mem.get('soldiers_seen_key') == key
                     and type(count) is int and count >= 0)
            shop['soldier_receipt'] = {
                'status': 'pending', 'key': shop.get('key'), 'qty': value,
                'gold_before': (header or {}).get('gold'),
                'soldiers_before': count if fresh else None}
            year, month = (int(p) for p in shop.get('key', '0-0').split('-'))
            expected = (chart.purchase_for(mem.get('chapter') or 0, year, month) or {}).get('soldiers')
            _record(mem, 'soldier_refill', qty=value, chart_step=month_chart_step(mem),
                    strategy_variant=shop.get('variant', 'chart'),
                    expected_metric=expected,
                    observed_metric=value, reason='兵士補充数を確認して確定を要求。支払いは次の画面で照合')
    return [pad('a')]


# ---------------------------------------------------------------- prompts
DISCHARGE_LIMIT = 32             # at most the full roster, one confirmed removal at a time
DISCHARGE_SCAN_LIMIT = 20        # preserve the existing bounded cursor walk
DISCHARGE_EXIT_LIMIT = 4         # B presses per month once the balance is paid up


def discharge_candidates(screen):
    """Read the forced-discharge list, or None when any row is not proven.

    The measured list is one name per 16px row at x=24 from y=47. A partial or
    unknown name invalidates the whole list: the weakest candidate cannot be
    confirmed from an incomplete set. The list may scroll; callers accumulate
    rows across fresh frames rather than assuming one page is the whole army.
    """
    if screen.kind != 'discharge_menu':
        return None
    names = []
    for y in range(47, 160, 16):
        row = next((line for line in screen.lines if line.y == y), None)
        if row is None:
            continue
        name = row.span(24, 96).strip()
        if not name:
            continue
        if UNKNOWN in name or re.search(r'[\d\s]', name) or fixed_wage(name) is None:
            return None
        names.append(name)
    if not names or len(set(names)) != len(names):
        return None
    return names


def _discharge_scroll_end(screen, frame, names):
    """Native last-page proof for the discharge list (same green list panel).

    The discharge list shows one name per 16px row from y=47. A full eight-row
    page whose cursor sits on the last row still needs the native up-arrow and
    empty down-arrow slot; a stalled cursor alone is not an end proof.
    """
    from ..os_layer.phase import green
    if (frame is None or frame.width != 256 or frame.height != 224
            or len(names) != 8 or screen.selected != names[-1]
            or not screen.hand or not 137 <= screen.hand[1] <= 160):
        return False
    return (all(min(frame.pixel(x, y)) >= 240
                for y in range(30, 34) for x in range(195 - (y - 30), 196 + (y - 30)))
            and all(green(*frame.pixel(x, y))
                    for y in range(159, 162) for x in range(192, 199))
            and all(max(frame.pixel(x, 162)) <= 8 for x in range(192, 199))
            and all(170 <= frame.pixel(x, 163)[0] <= 220
                    and 110 <= frame.pixel(x, 163)[1] <= 170
                    and 50 <= frame.pixel(x, 163)[2] <= 100
                    for x in range(192, 199)))


def discharge_step(screen: Screen, mem, frame=None):
    """Forced discharge list: dismiss the weakest general only while in debt.

    Wages come out at the month boundary; a negative balance forces
    dismissing generals (their 賃金 becomes cash) until it is back to at
    least zero (gcgx 収入). While the balance is negative the header gold is
    unreadable (the game prints ー1G and the header only matches digits) and
    B is refused. Once the balance is non-negative the header gold parses:
    leave with B instead of dismissing more generals (owner 2026-09-28:
    そもそも将軍解雇はしないで欲しい).

    Issue #2367 (owner 2026-10-20): if a dismissal is forced, choose the
    weakest general instead of the default cursor. The list is scanned from
    the same fresh frames, the cursor is moved to the candidate and the
    selection is re-read before A. A name or cursor that cannot be read never
    confirms a candidate. One general at a time is dismissed, then the list
    and the balance are re-observed; a paid-up balance leaves with B.
    """
    month = re.search(r'(\d+)ねん(\d+)のつき', screen.text)
    key = f'{month[1]}-{month[2]}' if month else None
    gold = (screen.header or {}).get('gold')
    paid_up = type(gold) is int
    state = mem.get('discharge') or {}
    if state.get('key') != key or type(state.get('presses')) is not int:
        state = {'key': key, 'presses': 0, 'exits': 0}
    mem['discharge'] = state
    if paid_up:
        pending = state.pop('pending_target', None)
        if pending is not None:
            _record(mem, 'discharge_verified', general=pending,
                    observed_metric={'month': key, 'gold': gold, 'basis': 'balance_nonnegative'},
                    reason='解雇要求後に残高が非負になり強制解雇が解消したことを確認（候補消失の確認とは区別）')
        exits = int(state.get('exits') or 0)
        if exits >= DISCHARGE_EXIT_LIMIT:
            if not state.get('held'):
                state['held'] = True
                _record(mem, 'situation_held', screen=screen.kind, strategy_variant='discharge_exit_failed',
                        observed_metric={'month': key, 'gold': gold, 'exits': exits},
                        reason='所持金が0以上でも解雇画面がBで抜けないため入力を保留')
            return []
        state['exits'] = exits + 1
        _record(mem, 'discharge_exit', strategy_variant='paid_up_leave_with_b',
                observed_metric={'month': key, 'gold': gold, 'exits': state['exits'],
                                 'selected': screen.selected},
                reason='所持金が0以上になったため解雇を続けずBで画面を出る')
        return [pad('b')]

    names = discharge_candidates(screen)
    if names is None or screen.selected not in names:
        # An unread name/cursor is not proof that this is the weakest entry.
        if not state.get('unverified'):
            state['unverified'] = True
            _record(mem, 'situation_held', screen=screen.kind,
                    strategy_variant='discharge_unverified',
                    observed_metric={'month': key, 'selected': screen.selected,
                                     'names': names},
                    reason='解雇候補の名前またはカーソルを読めないため最弱将軍を確定せず入力を保留')
        return []
    selected = screen.selected
    scan = [n for n in (state.get('scan') or []) if isinstance(n, str)]
    scan = list(dict.fromkeys([*scan, *names]))
    state['scan'] = scan
    phase = state.get('phase') or 'scan'
    if phase == 'select_move':
        phase = state['phase'] = 'select'
    if phase == 'after':
        phase = state['phase'] = 'scan'
    if phase in ('scan', 'scan_move'):
        state['moves'] = int(state.get('moves') or 0) + 1
        wrapped = (selected == scan[0] and state.get('moved') is True
                   and len(scan) > len(names))
        proven = (len(names) < 8 or wrapped
                  or _discharge_scroll_end(screen, frame, names))
        if not proven:
            if state['moves'] > DISCHARGE_SCAN_LIMIT:
                if not state.get('scan_held'):
                    state['scan_held'] = True
                    _record(mem, 'situation_held', screen=screen.kind,
                            strategy_variant='discharge_scan_unproven',
                            observed_metric={'month': key, 'candidates': scan,
                                             'moves': state['moves'],
                                             'selected': selected},
                            reason='解雇候補の一覧端を証明できないため、未走査ページを残したまま最弱を確定しない')
                return []
            state['moved'] = True
            state['phase'] = 'scan_move'
            return [pad('down')]
        pending = state.get('pending_target')
        if pending is not None:
            if pending in scan:
                # A is a request, not a receipt. Never repeat it while the
                # previous target is still in the complete candidate list.
                if not state.get('removal_held'):
                    state['removal_held'] = True
                    _record(mem, 'situation_held', screen=screen.kind,
                            strategy_variant='discharge_removal_unverified',
                            observed_metric={'month': key, 'target': pending},
                            reason='前の解雇対象が候補一覧に残るため、成立未確認として決定入力を重ねない')
                for field in ('scan', 'moved', 'moves'):
                    state.pop(field, None)
                state['phase'] = 'scan'
                return []
            _record(mem, 'discharge_verified', general=pending,
                    observed_metric={'month': key, 'basis': 'complete_list_removal'},
                    reason='完全な解雇候補一覧から前の対象が消えたことを確認')
            state.pop('pending_target', None)
            state.pop('removal_held', None)
        if state['presses'] >= DISCHARGE_LIMIT:
            if not state.get('held'):
                state['held'] = True
                _record(mem, 'situation_held', screen=screen.kind, strategy_variant='discharge_limit',
                        observed_metric={'month': key, 'presses': state['presses']},
                        reason='解雇画面で上限回数まで決定しても抜けないため入力を保留')
            return []
        target, evidence = weakest_general(scan)
        if target is None:
            if not state.get('unresolved'):
                state['unresolved'] = True
                _record(mem, 'situation_held', screen=screen.kind,
                        strategy_variant='discharge_unresolved',
                        observed_metric={'month': key, 'unresolved': evidence['unresolved']},
                        reason='候補に静的データの無い将軍がいるため最弱を確定せず入力を保留')
            return []
        state['target'] = target
        state['ranking'] = evidence['candidates']
        state['phase'] = 'select'
        phase = 'select'
    if phase == 'select':
        target = state.get('target')
        if not isinstance(target, str) or target not in scan:
            state['phase'] = 'scan'
            return []
        moves = int(state.get('cursor_moves') or 0)
        if moves > DISCHARGE_SCAN_LIMIT:
            if not state.get('cursor_held'):
                state['cursor_held'] = True
                _record(mem, 'situation_held', screen=screen.kind,
                        strategy_variant='discharge_cursor_unreached',
                        observed_metric={'month': key, 'target': target,
                                         'selected': selected, 'moves': moves},
                        reason='最弱候補へカーソルを移動できないため解雇を確定せず入力を保留')
            return []
        if selected != target:
            state['cursor'] = selected
            state['cursor_moves'] = moves + 1
            if target in names:
                return [pad('down' if names.index(target) > names.index(selected) else 'up')]
            # The candidate is on an earlier/later page. Walk the real list
            # back to it instead of re-reading this page forever.
            target_index = scan.index(target)
            if target_index < scan.index(names[0]):
                return [pad('up')]
            if target_index > scan.index(names[-1]):
                return [pad('down')]
            state['phase'] = 'scan'
            return []
        state['presses'] = int(state['presses']) + 1
        _record(mem, 'discharge_general', general=target,
                strategy_variant='weakest_general',
                observed_metric={'month': key, 'presses': state['presses'],
                                 'selected': selected, 'target': target,
                                 'candidates': scan, 'ranking': state.get('ranking'),
                                 'hand': list(screen.hand) if screen.hand else None},
                reason='所持金不足で強制解雇のため、最弱将軍をカーソルで確認して解雇を要求。成立は未確認')
        state['pending_target'] = target
        state.pop('scan', None)
        state.pop('ranking', None)
        state.pop('target', None)
        state.pop('still', None)
        state.pop('moved', None)
        state.pop('last', None)
        state.pop('moves', None)
        state.pop('cursor_moves', None)
        state.pop('cursor', None)
        state['phase'] = 'after'
        return [pad('a')]
    return []
