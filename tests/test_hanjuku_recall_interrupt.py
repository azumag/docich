"""A battle/event between the recall plan and its A must not cost the long skip."""
from copy import deepcopy
from types import SimpleNamespace

from docich.hanjuku.flows import recall


def mem_with(state):
    return {'tick': 100, 'chapter': 5, 'flow': {'recall': state}}


def test_pending_recall_keeps_its_state_but_records_the_interruption():
    state = {'stage': 'await_dispatch', 'request_trace': {'x': 1}, 'a_inputs': 1}
    mem = mem_with(state)
    before = deepcopy(mem['flow']['recall'])
    recall._mark_recall_interrupted(mem)
    # The pending recall is not rewritten by a combat foreground.
    assert mem['flow']['recall'] == before
    assert mem['flow']['recall_interrupted'] == 100


def test_an_already_sent_recall_is_not_marked():
    for state in ({'stage': 'await_dispatch', 'input_sent': True, 'goal': 'カルーン'},
                  {'stage': 'dest', 'request_trace': {'x': 1}},
                  {'goal': 'カルーン'}):
        mem = mem_with(dict(state))
        recall._mark_recall_interrupted(mem)
        assert 'recall_interrupted' not in mem['flow']


def test_no_recall_state_is_a_noop():
    mem = {'tick': 100, 'chapter': 5, 'flow': {}}
    recall._mark_recall_interrupted(mem)
    assert 'recall' not in mem['flow'] and 'recall_interrupted' not in mem['flow']


def test_interrupted_unconfirmed_recall_retries_after_the_short_hold():
    # g1139 2026-11-06 15:18:18 JST: the カルーン defense message took the
    # confirm A. The next attempt must not wait the full 400-observation hold.
    state = {'stage': 'await_dispatch', 'steps': 6, 'goal': 'カルーン',
             'target': [139, 113]}
    mem = mem_with(state)
    mem['flow']['recall_interrupted'] = 100
    recall._recall_dispatch_step(SimpleNamespace(kind='map'), mem, state)
    assert mem['flow'].get('recall_skip_ticks') == recall.FORT_RETRY_TICKS
    assert 'recall' not in mem['flow']


def test_a_stale_interruption_marker_does_not_shorten_the_next_recalls_hold():
    # #3544: the marker could outlive its recall (e.g. the month-scene drop)
    # and then shorten the retry hold of an unrelated later recall.
    state = {'stage': 'menu', 'target': [139, 113]}
    mem = mem_with(state)
    mem['flow']['recall_interrupted'] = 100
    recall._request_recall(mem, state, 'カルーン', [recall.pad('a')])
    assert 'recall_interrupted' not in mem['flow']
    screen = SimpleNamespace(kind='map')
    for _ in range(6):
        recall._recall_dispatch_step(screen, mem, state)
    assert mem['flow'].get('recall_skip_ticks') != recall.FORT_RETRY_TICKS
    assert 'recall' not in mem['flow']
