"""A boss march that never enters the boss castle is sent again (g1075, 2026-10-29).

3-B1 launched アポロン from パタゴーヌ at tick 76407 (14:56, v293). The marker
landed 6 px outside the boss building; he camped beside it at 15:00. At tick
79599 (16:00) the order was still launched and the sortie en route: no boss
battle, no recall, nothing left to start it. パタゴーヌ itself was lost at
15:42, so the boss stayed locked meanwhile.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.strategy.campaign import _enter_chapter

CHAPTER = 3
LAUNCH = 76407
NORMAL = sorted(set(chart.CASTLE_NAMES[CHAPTER]) - {chart.home_castle(CHAPTER), chart.boss_castle(CHAPTER)})


def memory(tick, *, status='en_route', target='ボス', lost=()):
    steps = [o['step'] for o in chart.orders(CHAPTER)]
    return {
        'chapter': CHAPTER, 'tick': tick,
        'orders': {step: 'launched' for step in steps},
        'sorties': {'3-B1': {'general': 'アポロン', 'source': 'パタゴーヌ', 'target': target,
                             'status': status, 'tick': LAUNCH, 'confirmation_tick': LAUNCH - 6}},
        'launched_orders': {'3-B1': {'step': '3-B1', 'general': 'アポロン', 'source': 'パタゴーヌ',
                                     'target': 'ボス', 'cards': ['ゼンマイン', 'ミックミー']}},
        'general_override': {'3-B1': 'アポロン'},
        'captured': [c for c in NORMAL if c not in lost],
        'lost': list(lost),
        'garrison': {'アルマムーン': ['どうし', 'キャンディー', 'ラズベリー'],
                     **({} if 'パタゴーヌ' in lost else {'パタゴーヌ': ['セルリアク']})},
    }


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_the_live_march_is_overdue_and_waits_for_the_boss_unlock():
    mem = memory(79599, lost=['パタゴーヌ'])
    assert O.next_order(mem) is None                    # パタゴーヌ lost: boss still locked
    assert mem['orders']['3-B1'] == 'pending'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert mem['boss_relaunch'] == {'3-B1': 1}
    (record,) = decisions(mem, 'boss_sortie_overdue')
    assert record['chart_step'] == '3-B1' and record['general'] == 'アポロン'
    assert record['observed_metric'] == {'general': 'アポロン', 'source': 'パタゴーヌ',
                                         'marching': 79599 - LAUNCH, 'relaunches': 0}
    for key in ('general_override', 'launched_orders'):
        assert '3-B1' not in mem[key]


def test_once_every_castle_is_ours_the_chart_boss_order_runs_again():
    mem = memory(79599)
    order = O.next_order(mem)
    assert order is not None and order['step'] == '3-B1' and order['target'] == 'ボス'
    assert order['general'] == 'どうし'                  # the chart order, not the stale アポロン
    assert O._source(order, mem) == 'パタゴーヌ'


def test_the_old_march_no_longer_holds_its_general():
    mem = memory(79599)
    assert 'アポロン' in O._en_route({**mem, 'tick': LAUNCH + 10})[0]
    O.next_order(mem)
    assert 'アポロン' not in O._en_route({**mem, 'tick': LAUNCH + 10})[0]


def test_a_march_inside_the_limit_is_left_alone():
    mem = memory(LAUNCH + O.BOSS_ARRIVAL_LIMIT - 1)
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert mem['sorties']['3-B1']['status'] == 'en_route'
    assert not decisions(mem, 'boss_sortie_overdue')


def test_the_limit_is_above_the_longest_measured_march():
    assert O.BOSS_ARRIVAL_LIMIT > 677


@pytest.mark.parametrize('status', ['arrived', 'cancelled'])
def test_a_march_that_entered_or_was_cancelled_is_not_sent_again(status):
    mem = memory(79599, status=status)
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert not decisions(mem, 'boss_sortie_overdue')


def test_an_old_march_to_a_normal_castle_is_not_touched():
    mem = memory(79599, target='パタゴーヌ')
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert mem['sorties']['3-B1']['status'] == 'en_route'


def test_relaunches_are_bounded_per_step():
    mem = memory(79599, lost=['パタゴーヌ'])
    for n in range(1, O.BOSS_RELAUNCH_LIMIT + 1):
        O.next_order(mem)
        assert mem['boss_relaunch']['3-B1'] == n
        # the relaunched march is again launched and never enters
        mem['orders']['3-B1'] = 'launched'
        mem['sorties']['3-B1'] = {'general': 'セルリアク', 'source': 'パタゴーヌ', 'target': 'ボス',
                                  'status': 'en_route', 'tick': mem['tick']}
        mem['tick'] += O.BOSS_ARRIVAL_LIMIT
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert len(decisions(mem, 'boss_sortie_overdue')) == O.BOSS_RELAUNCH_LIMIT
    assert len(decisions(mem, 'boss_relaunch_exhausted')) == 1
    O.next_order(mem)                                   # recorded once, not every observation
    assert len(decisions(mem, 'boss_relaunch_exhausted')) == 1


def test_a_new_chapter_resets_the_budget():
    mem = memory(79599, lost=['パタゴーヌ'])
    O.next_order(mem)
    _enter_chapter(mem, 4, reason='test')
    assert 'boss_relaunch' not in mem
