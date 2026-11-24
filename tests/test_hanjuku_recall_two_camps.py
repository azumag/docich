"""Two stuck camps must not release each other's retry hold.

g1133 2026-11-05 14:16: the single ``recall_skip`` slot kept only the latest failure,
so camp A failed, camp B was chosen, B failed, A was chosen again (every few seconds),
and the short map windows the boss order needed were spent on failing recalls.
"""
from docich.hanjuku.flows import recall as R
from docich.hanjuku.state.regions import flow as _flow

A = {'target': (52, 148)}
B = {'target': (11, 46)}


def _fail(mem, camp, tick, base=None):
    mem['tick'] = tick
    R._arm_recall_skip(mem, {'target': list(camp['target'])}, base)


def test_a_second_failure_keeps_the_first_camp_held():
    mem = {'chapter': 6, 'tick': 100}
    _fail(mem, A, 100)
    assert R._filter_recall_skip(mem, [A, B]) == [B]
    _fail(mem, B, 110)
    assert R._filter_recall_skip(mem, [A, B]) == []


def test_each_hold_expires_on_its_own_window():
    mem = {'chapter': 6, 'tick': 100}
    _fail(mem, A, 100)
    _fail(mem, B, 110, base=60)          # B only got hidden: a short doubling wait
    mem['tick'] = 180
    assert R._filter_recall_skip(mem, [A, B]) == [B]
    mem['tick'] = 100 + R.RECALL_SKIP_TICKS + 1
    assert R._filter_recall_skip(mem, [A, B]) == [A, B]
    assert not _flow(mem).get('recall_skip_history')


def test_history_is_scoped_to_the_chapter_and_bounded():
    mem = {'chapter': 6, 'tick': 100}
    for i in range(R.RECALL_SKIP_HISTORY + 4):
        _fail(mem, {'target': (i * 100, 0)}, 100 + i)
    assert len(_flow(mem)['recall_skip_history']) <= R.RECALL_SKIP_HISTORY
    mem['chapter'] = 7
    assert R._filter_recall_skip(mem, [A, B]) == [A, B]


def test_a_changed_camp_kind_is_not_held():
    mem = {'chapter': 6, 'tick': 100}
    _fail(mem, A, 100)
    _fail(mem, B, 110)
    dome = {'target': A['target'], 'kind': 'dome'}
    assert R._filter_recall_skip(mem, [dome]) == [dome]
