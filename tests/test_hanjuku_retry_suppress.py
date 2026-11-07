"""A just-failed order is not re-issued with a new execution id (v415).

g1133 2026-11-05: the boss order failed 96 times in ten minutes because no
castle menu would open at either source. Every rebuild gives the same content
a new execution id (the situation digest changed), so the failure looked new
and the order was retried at once, each retry paying a full navigation.
"""
from docich.hanjuku.strategy import orders as O, replan


def memory(tick=1000):
    return {'chapter': 6, 'tick': tick, '_records': [], 'orders': {},
            'world': {'sorties': {}},
            'failed_local_step': {'M1': tick - 10}}


def test_a_recent_failure_suppresses_the_same_content():
    mem = memory()
    replan._suppress_recent_failures(mem, [{'step': 'M1'}])
    assert mem['orders']['M1'] == 'failed'
    assert (mem.get('tally') or {}).get('retry_suppressed') == 1


def test_an_old_failure_no_longer_suppresses():
    mem = memory(tick=1000)
    mem['failed_local_step'] = {'M1': 1000 - replan.PLAN_STALE_TICKS}
    replan._suppress_recent_failures(mem, [{'step': 'M1'}])
    assert 'M1' not in mem['orders']


def test_another_order_is_untouched():
    mem = memory()
    replan._suppress_recent_failures(mem, [{'step': 'M2'}])
    assert mem['orders'] == {}


def test_a_failure_records_the_content_name():
    mem = {'chapter': 6, 'tick': 500, '_records': [], 'orders': {}, 'active': 'A:abcd1234:M7'}
    mem['flow'] = {}
    O._finish_order(mem, 'failed', reason='test')
    assert mem['failed_local_step'] == {'M7': 500}
