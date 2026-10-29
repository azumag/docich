"""#2973: a lost attack's retry must not run once its castle is ours.

g1075-1d5f65e9 2026-10-29 JST: ポセイドン lost at リグア 13:07 (I:b59ab6:3 back
to pending), レモン's I:f2f5ad:1 took リグア 13:22, yet next_order returned the
retry at 13:27 ahead of the boss sortie 3-B1, because with an adopted plan the
base chart's remaining orders are looked at after the retries.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy.orders import next_order

RETRY = 'I:b59ab6:3'


def state(retry_target='リグア', purpose='retake', captured=None):
    castles = [c for c in chart.castles(3) if c != 'ボス']
    mem = {'chapter': 3, 'tick': 71000,
           'captured': castles if captured is None else captured,
           'orders': {o['step']: 'launched' for o in chart.orders(3) if o['step'] != '3-B1'},
           'garrison': {'パタゴーヌ': ['どうし', 'アポロン'],
                        'グリン': ['ジェラート', 'リースリング', 'ポセイドン']},
           'chart_plan': {'orders': [{'step': 'A:b59ab675:K1', 'general': 'シャルドネ', 'source': 'グリン',
                                      'target': 'リグア', 'cards': [], 'after': None, 'note': 'plan'}]},
           'launched_orders': {RETRY: {'step': RETRY, 'general': 'ポセイドン', 'source': 'グリン',
                                       'target': retry_target, 'cards': ['イッテツーン', 'イッテツーン'],
                                       'after': None, 'purpose': purpose, 'note': 'retry'}}}
    mem['orders']['A:b59ab675:K1'] = 'launched'
    mem['orders'][RETRY] = 'pending'
    return mem


def decisions(mem):
    return [r.get('decision') for r in mem.get('_records', [])]


def test_a_retry_of_a_castle_we_hold_gives_way_to_the_boss_sortie():
    mem = state()
    order = next_order(mem)
    assert order['step'] == '3-B1'
    assert decisions(mem) == ['order_retry_retired']
    record = mem['_records'][0]
    assert (record['chart_step'], record['target']) == (RETRY, 'リグア')
    assert mem['orders'][RETRY] == 'pending'          # status untouched


def test_the_retirement_is_recorded_once():
    mem = state()
    next_order(mem)
    next_order(mem)
    assert decisions(mem) == ['order_retry_retired']


def test_a_retry_of_a_castle_still_held_by_the_enemy_still_runs():
    castles = [c for c in chart.castles(3) if c not in ('ボス', 'リグア')]
    mem = state(captured=castles)
    assert next_order(mem)['step'] == RETRY
    assert 'order_retry_retired' not in decisions(mem)


def test_a_retired_retry_comes_back_when_the_castle_is_lost_again():
    mem = state()
    next_order(mem)
    mem['captured'] = [c for c in mem['captured'] if c != 'リグア']
    assert next_order(mem)['step'] == RETRY


def test_a_move_into_our_own_castle_is_kept():
    mem = state(purpose='move')
    assert next_order(mem)['step'] == RETRY
    assert 'order_retry_retired' not in decisions(mem)
