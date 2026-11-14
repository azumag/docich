"""A castle that twice failed to receive a cliff-side camp ranks last.

Owner report 2026-11-15: the cliff-side camp's recall picked the nearest
castle, the path crossed a cliff and the general camped again, forever. The
goal now skips a castle with two unconfirmed recalls.
"""
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku.strategy.orders import _recall_goal


def memory(blocked=None):
    mem = {'chapter': 9, 'captured': ['ハヤク', 'タオ', 'サヌト', 'オシロガ']}
    state = _flow(mem).setdefault('recall', {})
    state['origin'] = [280, 176]          # the camp beside オシロガ
    if blocked is not None:
        _flow(mem)['camp_recall_blocked'] = blocked
    return mem


def test_the_nearest_owned_castle_is_the_goal_without_failures():
    assert _recall_goal(memory()) == 'オシロガ'


def test_a_castle_that_twice_failed_to_receive_the_camp_is_skipped():
    assert _recall_goal(memory({'オシロガ': 2})) == 'サヌト'


def test_a_single_unconfirmed_recall_still_keeps_the_nearest_castle():
    assert _recall_goal(memory({'オシロガ': 1})) == 'オシロガ'


def test_every_castle_blocked_falls_back_to_the_nearest():
    blocked = {c: 2 for c in ('オシロガ', 'サヌト', 'タオ', 'ハヤク', 'アルマムーン')}
    assert _recall_goal(memory(blocked)) == 'オシロガ'
