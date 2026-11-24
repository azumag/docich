"""The same order is not restarted on every observation (v408).

g1133 2026-11-05: a defence battle interrupted the plan's staging move and the
bot restarted it at once - 10 starts of `A:bf8c3dee:Ma21426` in five minutes.
Each restart opens the world map, aborts on the stationary cursor and closes it
again, so the retry cost more observations than the sortie it was retrying.
"""
from docich.hanjuku.flows import map as M


def test_the_first_start_is_allowed_even_at_tick_zero():
    mem = {'tick': 0}
    assert M._order_start_due(mem, 'A:x:M1') is True


def test_the_first_start_is_allowed():
    mem = {'tick': 100}
    assert M._order_start_due(mem, 'A:x:M1') is True
    assert mem['order_start_tick'] == {'A:x:M1': 100}


def test_an_immediate_restart_is_held_back():
    mem = {'tick': 100}
    assert M._order_start_due(mem, 'A:x:M1') is True
    mem['tick'] = 100 + M.ORDER_START_BACKOFF - 1
    assert M._order_start_due(mem, 'A:x:M1') is False


def test_the_order_starts_again_after_the_backoff():
    mem = {'tick': 100}
    assert M._order_start_due(mem, 'A:x:M1') is True
    mem['tick'] = 100 + M.ORDER_START_BACKOFF
    assert M._order_start_due(mem, 'A:x:M1') is True
    assert mem['order_start_tick']['A:x:M1'] == mem['tick']


def test_another_order_is_not_held_by_the_first_one():
    mem = {'tick': 100}
    assert M._order_start_due(mem, 'A:x:M1') is True
    assert M._order_start_due(mem, 'A:x:M2') is True


def test_the_start_memory_stays_bounded():
    mem = {'tick': 100}
    for i in range(60):
        mem['tick'] = 100 + i * M.ORDER_START_BACKOFF
        assert M._order_start_due(mem, f'A:x:M{i}') is True
    assert len(mem['order_start_tick']) <= 40
