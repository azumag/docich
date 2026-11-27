"""A merely evaluated order does not consume its restart backoff (#4622).

g1133 2026-11-05 showed why the backoff exists: a defence battle interrupted
the staging move and the bot restarted it at once. g1190 2026-11-19 run 5
showed the opposite failure: stamping the first evaluation meant an attempt
interrupted before the castle menu was ever reached held the real retry back.
"""
from docich.hanjuku.flows import map as M


def test_the_first_evaluation_is_allowed_without_consuming_the_backoff():
    mem = {'tick': 100}
    assert M._order_start_due(mem, 'A:x:M1') is True
    assert 'order_start_tick' not in mem


def test_a_proven_start_is_held_back_immediately():
    mem = {'tick': 100}
    M._mark_order_started(mem, 'A:x:M1')
    mem['tick'] = 100 + M.ORDER_START_BACKOFF - 1
    assert M._order_start_due(mem, 'A:x:M1') is False


def test_the_order_may_start_again_after_the_backoff():
    mem = {'tick': 100}
    M._mark_order_started(mem, 'A:x:M1')
    mem['tick'] = 100 + M.ORDER_START_BACKOFF
    assert M._order_start_due(mem, 'A:x:M1') is True


def test_another_order_is_not_held_by_the_first_one():
    mem = {'tick': 100}
    M._mark_order_started(mem, 'A:x:M1')
    assert M._order_start_due(mem, 'A:x:M2') is True


def test_the_start_memory_stays_bounded():
    mem = {'tick': 100}
    for i in range(60):
        mem['tick'] = 100 + i * M.ORDER_START_BACKOFF
        M._mark_order_started(mem, f'A:x:M{i}')
    assert len(mem['order_start_tick']) <= 40
