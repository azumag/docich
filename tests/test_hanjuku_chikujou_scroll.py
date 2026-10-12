"""Four-row windows must not limit monthly building to four holdings."""
from pathlib import Path
from dataclasses import replace

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_multi_chikujou import castles, memory, month, open_build, quote, result

NAMES = ('アルマムーン', 'ナキューメラ', 'キカンドン', 'ジョンリギ', 'ゴーメン', 'スペンソニア')
FRAME = Path(__file__).parent / 'fixtures/hanjuku/g684-chikujou-four-rows.png'


def holdings(names=NAMES, **shop):
    mem = memory(**shop)
    mem['captured'] = list(names[1:])
    mem['garrison'] = {c: ['どうし' if i == 0 else 'ゼウス'] for i, c in enumerate(names)}
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


def test_ignored_scroll_inputs_exit_without_confirming_or_marking_hidden_castles():
    mem = holdings()
    sub = open_build(mem)
    sub['rows_tried'] = list(NAMES[:4])
    screen = castles(on=NAMES[3], names=NAMES[:4])
    for _ in range(policy.MENU_NAV_PRESS_LIMIT):
        assert policy.month_sub_step(screen, mem) == [policy.pad('down')]
    assert policy.month_sub_step(screen, mem) == [policy.pad('b')]
    assert sub['aborted'] and not sub.get('chosen') and not sub.get('confirm_sent')
    assert sub['rows_tried'] == list(NAMES[:4])
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


def test_scrolled_selection_still_declines_a_quote_that_spends_reserved_wages():
    mem = holdings()
    sub = open_build(mem, 45)
    sub['rows_tried'] = list(NAMES[:4])
    assert policy.month_sub_step(castles(names=NAMES[:4]), mem) == [policy.pad('down')]
    assert policy.month_sub_step(castles(on=NAMES[4], names=NAMES[1:5]), mem) == [policy.pad('a')]
    assert sub['chosen'] == NAMES[4]
    assert policy.month_sub_step(quote(NAMES[4], 45, 18, on='いかんッ!'), mem) == [policy.pad('a')]
    assert sub['declined'] and not sub.get('confirm_sent')
