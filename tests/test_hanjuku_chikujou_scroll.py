"""Four-row windows must not limit monthly building to four holdings."""
from pathlib import Path
from dataclasses import replace

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_multi_chikujou import castles, memory, month, open_build, quote, result
from docich.hanjuku.state.regions import flow as _flow, world as _world

NAMES = ('アルマムーン', 'ナキューメラ', 'キカンドン', 'ジョンリギ', 'ゴーメン', 'スペンソニア')
FRAME = Path(__file__).parent / 'fixtures/hanjuku/g684-chikujou-four-rows.png'


def holdings(names=NAMES, **shop):
    mem = memory(**shop)
    mem['captured'] = list(names[1:])
    _world(mem)['garrison'] = {c: ['どうし' if i == 0 else 'ゼウス'] for i, c in enumerate(names)}
    return mem


def test_native_first_page_scrolls_when_its_four_castles_were_tried():
    frame = read_png(FRAME)
    assert frame.digest() == '3badd632b656aed835037aaa629c48200870f4853278f6300f90eed47a2c07d9'
    screen = parse(frame)
    mem = holdings(key='3-9')
    mem['month'] = '3-9'
    sub = mem['month_sub'] = {'kind': 'chikujou', 'key': '3-9', 'chikujou_version': 2,
                              'rows_tried': list(NAMES[:4]), 'presses': 0, 'left_menu': True}
    assert policy.month_sub_step(screen, mem) == [policy.pad('down')]
    assert not sub.get('chosen') and not sub.get('declined')
    assert mem['_records'][-1]['decision'] == 'chikujou_scroll'
    assert mem['_records'][-1]['observed_metric']['visible'] == list(NAMES[:4])


@pytest.mark.parametrize('chapter,names', [(1, NAMES[:5]), (1, NAMES),
                                          (5, policy.chart.CASTLE_NAMES[5][:-1])])
def test_all_holdings_receive_distinct_receipts_through_a_four_row_window(chapter, names):
    mem = holdings(names)
    mem['chapter'] = chapter
    gold = 200
    for _ in names:
        sub = open_build(mem, gold)
        index = offset = 0
        for _ in range(20):
            visible = names[offset:offset + 4]
            screen = castles(on=names[index], names=visible)
            action = policy.month_sub_step(screen, mem)
            if action == [policy.pad('a')]:
                break
            assert action in ([policy.pad('down')], [policy.pad('up')])
            index = max(0, min(len(names) - 1, index + (1 if action == [policy.pad('down')] else -1)))
            offset = max(min(offset, index), index - 3)
        else:
            pytest.fail('No observed castle selected within one traversal')
        chosen = sub['chosen']
        assert chosen == names[index]
        assert chosen not in mem['shop'].get('chikujou_tried', [])
        assert policy.month_sub_step(quote(chosen, gold, 5), mem) == [policy.pad('a')]
        assert policy.month_sub_step(result(chosen), mem) == [policy.pad('a')]
        assert policy.month_sub_step(screen, mem) == [policy.pad('b')]
        gold -= 5
        assert policy._finish_month_sub(month(gold), mem, mem['shop']) is True
    upgrades = mem['shop']['chikujou_upgrades']
    assert {u['castle'] for u in upgrades} == set(names)
    assert len(upgrades) == len(names) and mem['shop']['chikujou'] == 'done'
    assert all(u['gold_before'] - u['gold_after'] == u['cost'] == 5 for u in upgrades)
    assert any(r['decision'] == 'chikujou_scroll' for r in mem['_records'])


def test_a_catalog_end_requires_the_extreme_catalog_row_and_cursor():
    labels = policy._chikujou_name_labels(1)
    values = list(labels.values())
    rows = [(16, 39 + i * 16, values[i]) for i in range(4)]
    assert policy._chikujou_catalog_end(labels, rows, rows[0], 'up')
    assert not policy._chikujou_catalog_end(labels, rows, rows[0], 'down')
    interior = [(16, 39 + i * 16, values[i + 1]) for i in range(4)]
    assert not policy._chikujou_catalog_end(labels, interior, interior[-1], 'down')
    bottom = [(16, 39 + i * 16, values[-4 + i]) for i in range(4)]
    assert policy._chikujou_catalog_end(labels, bottom, bottom[-1], 'down')
    assert not policy._chikujou_catalog_end(labels, bottom, bottom[0], 'down')


def test_the_scan_moves_up_toward_untried_rows_above_the_window():
    mem = holdings()
    sub = open_build(mem)
    visible = [NAMES[1], NAMES[3], NAMES[4], NAMES[5]]  # canonical ch1 rows 2-5
    sub['rows_tried'] = list(visible)                  # アルマムーン/キカンドン are still untried
    screen = castles(on=NAMES[5], names=visible)
    assert policy.month_sub_step(screen, mem) == [policy.pad('up')]
    record = mem['_records'][-1]
    assert record['decision'] == 'chikujou_scroll'
    assert record['month'] == '1-7'
    assert record['observed_metric']['action'] == 'up'
    assert not sub.get('aborted')


def test_ignored_scroll_inputs_reverse_and_wait_without_confirming_hidden_castles():
    mem = holdings()
    sub = open_build(mem)
    sub['rows_tried'] = list(NAMES[:4])
    screen = castles(on=NAMES[3], names=NAMES[:4])
    for _ in range(policy.MENU_NAV_PRESS_LIMIT):
        assert policy.month_sub_step(screen, mem) == [policy.pad('down')]
    for _ in range(policy.MENU_NAV_PRESS_LIMIT):
        assert policy.month_sub_step(screen, mem) == [policy.pad('up')]
    assert policy.month_sub_step(screen, mem) == []
    assert policy.month_sub_step(screen, mem) == []
    assert policy.month_sub_step(screen, mem) == [policy.pad('down')]
    assert not sub.get('aborted') and not sub.get('chosen') and not sub.get('confirm_sent')
    assert sub['rows_tried'] == list(NAMES[:4])
    assert any(r['decision'] == 'chikujou_scroll_stalled' for r in mem['_records'])
    assert not mem['shop'].get('chikujou_upgrades')


@pytest.mark.parametrize('bad', ['hand', 'row', 'layout', 'duplicate'])
def test_unread_or_ambiguous_list_never_scrolls_or_confirms(bad):
    mem = holdings()
    sub = open_build(mem)
    sub['rows_tried'] = list(NAMES[:4])
    screen = castles(names=NAMES[:4])
    if bad == 'hand':
        screen.hand = None
    elif bad == 'row':
        screen.lines.pop()
    elif bad == 'layout':
        screen.lines[-1] = replace(screen.lines[-1], y=screen.lines[-1].y + 8)
    else:
        screen.lines[-1] = screen.lines[0]
    for _ in range(policy.CHIKUJOU_WAIT_LIMIT - 1):
        assert policy.month_sub_step(screen, mem) == []
    assert policy.month_sub_step(screen, mem) == [policy.pad('b')]
    assert sub['aborted'] and not sub.get('chosen')


def test_a_wrapping_list_has_a_total_scroll_bound_even_when_the_hand_moves():
    mem = holdings()
    sub = open_build(mem)
    sub['rows_tried'] = list(NAMES[:4])
    limit = len(policy.chart.CASTLE_NAMES[1]) + 4
    for i in range(limit):
        assert policy.month_sub_step(castles(on=NAMES[i % 4], names=NAMES[:4]), mem) == [policy.pad('down')]
    assert policy.month_sub_step(castles(names=NAMES[:4]), mem) == [policy.pad('b')]
    assert sub['aborted'] and not sub.get('chosen')
    assert mem['_records'][-1]['decision'] == 'chikujou_held'
    assert mem['_records'][-1]['month'] == '1-7'
    assert mem['_records'][-1]['deviation_reason'] == 'castle_scan_exhausted'
    assert mem['shop']['chikujou_last_hold']['deviation_reason'] == 'castle_scan_exhausted'
    assert mem['shop']['chikujou_last_hold']['month'] == '1-7'


def test_scrolled_selection_still_declines_a_quote_that_spends_reserved_wages():
    mem = holdings()
    sub = open_build(mem, 45)
    sub['rows_tried'] = list(NAMES[:4])
    assert policy.month_sub_step(castles(names=NAMES[:4]), mem) == [policy.pad('down')]
    assert policy.month_sub_step(castles(on=NAMES[4], names=NAMES[1:5]), mem) == [policy.pad('a')]
    assert sub['chosen'] == NAMES[4]
    assert policy.month_sub_step(quote(NAMES[4], 45, 18, on='いかんッ!'), mem) == [policy.pad('a')]
    assert sub['declined'] and not sub.get('confirm_sent')


def test_a_pending_launch_defers_the_castle_scroll():
    # owner 2026-11-27 (#5696/#5702): 出撃の開始から発射確認までの間は、
    # 築城一覧の探索を1回で打ち切って発射確認へ時間を回す。
    from docich.hanjuku.flows import month as month_flow
    frame = read_png(FRAME)
    screen = parse(frame)
    mem = holdings(key='3-9')
    mem['month'] = '3-9'
    step = 'A:3:M1'
    order = {'step': step, 'purpose': 'retake', 'source': 'ヘリオポリス',
             'target': 'ラグナグ', 'general': 'デュオニソス', 'cards': [], 'note': '奪還'}
    mem['sortie_launch_pending'] = step
    from docich.hanjuku.state.regions import plan as _plan_region
    _plan_region(mem)['chart_plan'] = {'orders': [order]}
    _plan_region(mem)['orders'] = {step: 'pending'}
    _plan_region(mem)['active'] = step
    sub = mem['month_sub'] = {'kind': 'chikujou', 'key': '3-9', 'chikujou_version': 2,
                              'rows_tried': list(NAMES[:4]), 'presses': 1,
                              'build_scroll': {'presses': 1}, 'left_menu': True}
    assert month_flow._chikujou_scroll(screen, mem, sub) == [policy.pad('b')]
    assert sub.get('declined') and sub.get('aborted')
    record = mem['_records'][-1]
    assert record['decision'] == 'chikujou_deferred_sortie'
    assert record['observed_metric']['step'] == 'A:3:M1'


def test_a_settled_sortie_retry_does_not_defer_the_castle_scroll():
    from docich.hanjuku.flows import month as month_flow
    frame = read_png(FRAME)
    screen = parse(frame)
    mem = holdings(key='3-9')
    mem['month'] = '3-9'
    mem['tick'] = 1000
    step = 'A:3:M1'
    order = {'step': step, 'purpose': 'retake', 'source': 'ヘリオポリス',
             'target': 'ラグナグ', 'general': 'デュオニソス', 'cards': [], 'note': '奪還'}
    mem['sortie_launch_pending'] = step
    from docich.hanjuku.state.regions import plan as _plan_region
    _plan_region(mem)['chart_plan'] = {'orders': [order]}
    _plan_region(mem)['orders'] = {step: 'pending'}
    _world(mem)['launched_orders'] = {step: dict(order)}
    _flow(mem)['sortie_attempt'] = {'step': step, 'target_seen': True,
                                    'confirmation_tick': 800, 'interrupted': True}
    _flow(mem)['launch_probe'] = {step: {'general': order['general'], 'source': order['source'],
                                         'target': order['target'], 'reads': 1,
                                         'confirmation_tick': 800}}
    sub = mem['month_sub'] = {'kind': 'chikujou', 'key': '3-9', 'chikujou_version': 2,
                              'rows_tried': list(NAMES[:4]), 'presses': 1,
                              'build_scroll': {'presses': 1}, 'left_menu': True}
    assert month_flow._chikujou_scroll(screen, mem, sub) == [policy.pad('down')]
    assert not sub.get('declined') and not sub.get('aborted')
    assert not any(r['decision'] == 'chikujou_deferred_sortie' for r in mem.get('_records') or [])
    assert 'sortie_launch_pending' not in mem
    assert step not in (_flow(mem).get('launch_probe') or {})


def test_without_a_pending_launch_the_scroll_continues():
    from docich.hanjuku.flows import month as month_flow
    frame = read_png(FRAME)
    screen = parse(frame)
    mem = holdings(key='3-9')
    mem['month'] = '3-9'
    sub = mem['month_sub'] = {'kind': 'chikujou', 'key': '3-9', 'chikujou_version': 2,
                              'rows_tried': list(NAMES[:4]), 'presses': 1,
                              'left_menu': True}
    assert month_flow._chikujou_scroll(screen, mem, sub) == [policy.pad('down')]
    assert not sub.get('declined')


def test_the_deferral_count_is_bounded_per_month(monkeypatch):
    # owner 2026-11-27 (#5733): 出撃の多い月に毎回築城を譲ると「築城可能なまま月一終了」
    # になるため、月内の打ち切り回数に上限を置く。
    from docich.hanjuku.flows import month as month_flow
    monkeypatch.setattr(month_flow, 'active_sortie_launch_priority', lambda mem: 'A:3:M1')
    frame = read_png(FRAME)
    screen = parse(frame)
    mem = holdings(key='3-9')
    mem['month'] = '3-9'
    sub = mem['month_sub'] = {'kind': 'chikujou', 'key': '3-9', 'chikujou_version': 2,
                              'rows_tried': list(NAMES[:4]), 'presses': 1,
                              'build_scroll': {'presses': 1}, 'left_menu': True}
    for _ in range(month_flow.CHIKUJOU_DEFER_LIMIT):
        assert month_flow._chikujou_scroll(screen, mem, sub) == [policy.pad('b')]
        sub['declined'] = False
        sub['aborted'] = False
        sub['build_scroll'] = {'presses': 1}
    # 上限に達したら、発射待ちでも通常の探索へ戻る
    assert month_flow._chikujou_scroll(screen, mem, sub) == [policy.pad('down')]
    assert mem['chikujou_defer']['count'] == month_flow.CHIKUJOU_DEFER_LIMIT
