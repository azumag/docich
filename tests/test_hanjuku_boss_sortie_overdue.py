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
from docich.hanjuku.state.regions import flow as _flow, journal as _journal
from docich.hanjuku.state.regions import world as _world

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
                     **({} if 'パタゴーヌ' in lost else {'パタゴーヌ': ['セルリアク', 'リースリング']})},
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
                                         'marching': 79599 - LAUNCH, 'relaunches': 0,
                                         'cell': list(chart.castles(CHAPTER)['ボス']),
                                         'source_still_has_general': False}
    assert '3-B1' not in mem['general_override']
    assert '3-B1' not in _world(mem)['launched_orders']


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


def test_a_march_that_was_cancelled_is_not_sent_again():
    mem = memory(79599, status='cancelled')
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert not decisions(mem, 'boss_sortie_overdue')


def test_an_entry_whose_fight_left_no_result_is_sent_again():
    """g1075 2026-10-30: 4-B1/4-B4 stayed 'launched' for hours after their site
    fights closed without a measured result; nothing else could reset them."""
    mem = memory(79599, status='arrived')
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'pending'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert mem['boss_relaunch'] == {'3-B1': 1}
    (record,) = decisions(mem, 'boss_sortie_overdue')
    assert record['resulting_event'] == 'relaunch:1'
    assert '3-B1' not in mem['general_override']
    assert '3-B1' not in _world(mem)['launched_orders']


def test_an_entry_with_a_live_fight_is_not_sent_again():
    mem = memory(79599, status='arrived')
    mem['battle'] = {'step': '3-B1', 'ally': 'アポロン', 'enemy': 'プリンス'}
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert mem['sorties']['3-B1']['status'] == 'arrived'
    mem['battle'] = {'step': 'other', 'ally': 'アポロン', 'enemy': 'プリンス'}
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'pending'           # another step's fight is not ours
    assert mem['sorties']['3-B1']['status'] == 'overdue'


def test_an_entry_to_a_site_already_won_is_not_sent_again():
    mem = memory(79599, status='arrived')
    mem['boss_victories'] = {'プリンス': {'site': 'ボス'}}
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert not decisions(mem, 'boss_sortie_overdue')


def test_an_entry_inside_the_limit_is_not_sent_again():
    mem = memory(LAUNCH + O.BOSS_ARRIVAL_LIMIT - 1, status='arrived')
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert not decisions(mem, 'boss_sortie_overdue')


def test_an_old_march_to_a_normal_castle_is_settled_and_relaunched():
    """Owner 2026-11-02 (g1097): a march to a normal castle that never
    arrived (26 stale sorties up to 40k ticks) starved a whole month —
    settle it overdue and hand the order back to pending."""
    mem = memory(79599, target='パタゴーヌ')
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'pending'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert mem['sortie_relaunch']['3-B1'] == 1


def test_a_march_relaunch_is_bounded():
    mem = memory(79599, target='パタゴーヌ')
    for n in range(1, O.MARCH_RELAUNCH_LIMIT + 1):
        O.next_order(mem)
        assert mem['sortie_relaunch']['3-B1'] == n
        mem['orders']['3-B1'] = 'launched'
        mem['sorties']['3-B1'] = {'general': 'セルリアク', 'source': 'アルマムーン',
                                  'target': 'パタゴーヌ', 'status': 'en_route',
                                  'tick': mem['tick']}
        mem['tick'] += O.BOSS_ARRIVAL_LIMIT
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'launched'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert len(decisions(mem, 'sortie_march_relaunch_exhausted')) == 1


def test_a_march_without_a_launched_order_is_just_closed():
    mem = memory(79599, target='パタゴーヌ')
    mem['orders']['3-B1'] = 'failed'
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'failed'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert not decisions(mem, 'sortie_march_overdue')
    assert decisions(mem, 'sortie_march_overdue_settled')


def test_a_confirmed_move_to_an_own_castle_is_settled_not_relaunched():
    """g1097 2026-11-02: moves into our own アイアイエー (recorded empty)
    went overdue one after another — ジェラート, チコリ, ピオーネ,
    ピスタチオ — because a move's arrival is only observable in the
    destination's general list, which nothing reads. Each relaunch drained
    the donor while the earlier movers already sat there. A confirmed
    move past the limit closes the order; routes.unread_dispatches keeps
    the castle off the move list until its list is read."""
    mem = memory(79599, target='パタゴーヌ')
    mem['sorties']['3-B1']['purpose'] = 'move'
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'failed'
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert 'sortie_relaunch' not in mem or '3-B1' not in mem['sortie_relaunch']
    assert not decisions(mem, 'sortie_march_overdue')
    (record,) = decisions(mem, 'sortie_move_overdue_settled')
    assert record['chart_step'] == '3-B1' and record['general'] == 'アポロン'
    assert record['observed_metric']['target'] == 'パタゴーヌ'


def test_a_move_into_lost_or_enemy_ground_still_relaunches():
    mem = memory(79599, target='パタゴーヌ', lost=['パタゴーヌ'])
    mem['sorties']['3-B1']['purpose'] = 'move'
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'pending'
    assert mem['sortie_relaunch']['3-B1'] == 1


def test_an_unconfirmed_move_launch_still_relaunches():
    mem = memory(79599, target='パタゴーヌ')
    mem['sorties']['3-B1']['status'] = 'launched_unconfirmed'
    mem['sorties']['3-B1']['purpose'] = 'move'
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'pending'
    assert mem['sortie_relaunch']['3-B1'] == 1


def test_a_move_known_only_by_the_order_snapshot_is_settled_too():
    """Sorties written before the launch record carried ``purpose`` still
    resolve through launched_orders."""
    mem = memory(79599, target='パタゴーヌ')
    _world(mem)['launched_orders']['3-B1']['purpose'] = 'move'
    O.next_order(mem)
    assert mem['orders']['3-B1'] == 'failed'
    assert decisions(mem, 'sortie_move_overdue_settled')


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


def site_memory(planned_target):
    """g1075 2026-10-30 09:28: 4-B2 カペリーニ left ネクスドリア for 四季王城(南東)
    at tick 131676 unconfirmed, so the cell sat in planned_target only. His
    08:34:57 entry came before v330 bound such entries, and the sortie stayed
    en route for 3066 ticks while 4-B3, launched confirmed, went overdue."""
    return {'chapter': 4, 'tick': 134742,
            'orders': {o['step']: 'launched' for o in chart.orders(4)},
            'sorties': {'4-B2': {'general': 'カペリーニ', 'source': 'ネクスドリア', 'target': None,
                                 'planned_target': planned_target, 'status': 'en_route',
                                 'tick': 131676, 'confirmation_tick': 131418}},
            'lost': ['ミヒラギアン', 'アルカ']}


def test_an_unconfirmed_site_launch_goes_overdue_by_its_planned_target():
    mem = site_memory('四季王城(南東)')
    O._relaunch_overdue_boss(mem)
    assert mem['sorties']['4-B2']['status'] == 'overdue'
    assert mem['orders']['4-B2'] == 'pending'
    (record,) = decisions(mem, 'boss_sortie_overdue')
    assert record['chart_step'] == '4-B2' and record['general'] == 'カペリーニ'
    # An unconfirmed march to a normal castle is still left alone.
    mem = site_memory('マラコビア')
    O._relaunch_overdue_boss(mem)
    assert mem['sorties']['4-B2']['status'] == 'en_route'


def test_an_overdue_relaunch_drops_the_spent_sortie_attempt():
    """g1097 2026-11-02: an interrupted departure attempt with a confirmation
    tick survived the overdue relaunch, so ``execution_claimed`` resolved the
    re-picked pending order to None and every castle menu backed out."""
    mem = memory(79599, lost=['パタゴーヌ'])
    _flow(mem)['sortie_attempt'] = {'step': '3-B1', 'confirmation_tick': LAUNCH - 6,
                             'interrupted': True, 'target_seen': True}
    O.next_order(mem)
    assert mem['sorties']['3-B1']['status'] == 'overdue'
    assert mem['orders']['3-B1'] == 'pending'
    assert 'sortie_attempt' not in _flow(mem)
    assert O._order_for_step(mem, '3-B1') is not None


def test_an_already_overdue_sortie_drops_the_spent_attempt():
    """The live deadlock state: sortie already overdue, order pending, and a
    stale claimed attempt still blocking the menu flow."""
    mem = memory(79599)
    mem['sorties']['3-B1']['status'] = 'overdue'
    mem['orders']['3-B1'] = 'pending'
    _flow(mem)['sortie_attempt'] = {'step': '3-B1', 'confirmation_tick': LAUNCH - 6,
                             'interrupted': True, 'target_seen': True}
    O._relaunch_overdue_march(mem)
    assert 'sortie_attempt' not in _flow(mem)


def test_an_attempt_for_another_step_or_unconfirmed_is_kept():
    mem = memory(79599)
    mem['sorties']['3-B1']['status'] = 'overdue'
    _flow(mem)['sortie_attempt'] = {'step': '3-B2', 'confirmation_tick': 1}
    O._relaunch_overdue_boss(mem)
    assert _flow(mem)['sortie_attempt']['step'] == '3-B2'
    _flow(mem)['sortie_attempt'] = {'step': '3-B1', 'target_seen': True}  # no tick: no claim
    O._relaunch_overdue_march(mem)
    assert _flow(mem)['sortie_attempt']['step'] == '3-B1'


def chapter6_memory(tick, *, source='アルマムーン', target='セレーネ', status='en_route'):
    """A chapter 6 march; its chart has no measured orders, so the step is synthetic."""
    return {
        'chapter': 6, 'tick': tick,
        'orders': {'6-X1': 'launched'},
        'sorties': {'6-X1': {'general': 'チコリ', 'source': source, 'target': target,
                              'status': status, 'tick': LAUNCH}},
        'launched_orders': {'6-X1': {'step': '6-X1', 'general': 'チコリ',
                                         'source': source, 'target': target}},
        'captured': [c for c in chart.CASTLE_NAMES[6] if c != chart.boss_castle(6)],
        'lost': [],
    }


def test_a_slow_chapter_6_march_waits_past_the_fixed_limit():
    """g1133 2026-11-05: ナストリーニ took 23-32 min to セレーネ (603 cells)
    and ピスタチオ 34 min to ユージア (740), but the fixed 20 min reset both."""
    mem = chapter6_memory(LAUNCH + O.BOSS_ARRIVAL_LIMIT + 10)
    limit = O._arrival_limit(mem, mem['sorties']['6-X1'])
    assert limit > O.BOSS_ARRIVAL_LIMIT
    O.next_order(mem)
    assert mem['sorties']['6-X1']['status'] == 'en_route'
    assert not decisions(mem, 'sortie_march_overdue')
    mem['tick'] = LAUNCH + limit
    O.next_order(mem)
    assert mem['sorties']['6-X1']['status'] == 'overdue'
    assert decisions(mem, 'sortie_march_overdue')


def test_a_short_chapter_6_march_keeps_the_measured_limit():
    mem = chapter6_memory(LAUNCH + O.BOSS_ARRIVAL_LIMIT + 10, target='イカリア')
    assert O._arrival_limit(mem, mem['sorties']['6-X1']) == O.BOSS_ARRIVAL_LIMIT
    O.next_order(mem)
    assert mem['sorties']['6-X1']['status'] == 'overdue'


def test_an_unmeasured_chapter_keeps_the_fixed_limit():
    mem = chapter6_memory(LAUNCH + O.BOSS_ARRIVAL_LIMIT + 10)
    mem['chapter'] = 3
    assert O._arrival_limit(mem, mem['sorties']['6-X1']) == O.BOSS_ARRIVAL_LIMIT


def test_the_scaled_wait_never_outlives_the_long_march_window():
    assert O.MARCH_ARRIVAL_CAP == O.LONG_MARCH_TICKS
    # アルブラーカ→アブダレ is 1064 chart cells: 1064*2.4 = 2554 exceeds the
    # 2400-tick window, so the distance-scaled wait must clamp to the cap.
    mem = chapter6_memory(LAUNCH, source='アルブラーカ', target='アブダレ')
    assert O._arrival_limit(mem, mem['sorties']['6-X1']) == O.LONG_MARCH_TICKS
