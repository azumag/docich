"""The same mechanical plan is not re-adopted on every call (v413).

g1133 2026-11-05: `chart_adjust_applied` appeared 487 times with the same
request_id and the same steps in about 40 minutes. The situation digest did not
change, so every planner call rebuilt the identical candidate list and adopted
it again - needless work and a log nobody could read.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import plan as _plan_region

CELLS = chart.castles(6)
NORMAL = [c for c in CELLS if c != 'ボス']


def memory():
    return {'chapter': 6, 'tick': 1000, 'month': '51-5', 'gold': 500, '_records': [],
            'orders': {}, 'sorties': {}, 'lost': [], 'chart_adjust': {},
            'captured': list(NORMAL),
            'garrison': {**{c: ['ゼウス'] for c in NORMAL},
                         'オケアナ': ['ゼウス', 'ポセイドン']}}


def adopt(mem, tick):
    mem['tick'] = tick
    replan._off_chart_mechanical(mem, mem['chart_adjust'], 'abcd1234abcd1234', 'normal')


def records(mem):
    return [r['decision'] for r in mem['_records']]


def test_the_plan_is_adopted_once_while_the_situation_is_unchanged():
    mem = memory()
    adopt(mem, 1000)
    assert records(mem) == ['chart_adjust_applied']
    adopt(mem, 1001)
    adopt(mem, 1002)
    assert records(mem) == ['chart_adjust_applied']


def test_the_kept_plan_is_the_same_object_and_keeps_its_tick():
    mem = memory()
    adopt(mem, 1000)
    plan = _plan_region(mem)['chart_plan']
    tick = mem['chart_plan_tick']
    adopt(mem, 1001)
    assert _plan_region(mem)['chart_plan'] is plan
    assert mem['chart_plan_tick'] == tick


def test_a_different_candidate_list_still_replaces_the_plan():
    mem = memory()
    adopt(mem, 1000)
    first = [o['step'] for o in _plan_region(mem)['chart_plan']['orders']]
    # The stored plan can no longer start (_plan_pending False) and the
    # situation changed: an owned castle is now empty.
    _plan_region(mem)['orders'] = {step: 'failed' for step in first}
    mem["captured"].remove("トウトウ")      # a normal castle becomes a target again
    adopt(mem, 1001)
    assert [o['step'] for o in _plan_region(mem)['chart_plan']['orders']] != first
    assert len(records(mem)) >= 2


# --- #3729: the identity comparison used the execution step -----------------

MOVED = {'general': 'ライム', 'source': 'グルーポフ', 'target': 'オケアナ',
         'cards': [], 'after': None, 'purpose': 'move', 'note': ''}


def candidates(target='オケアナ'):
    return {'move_1': {**MOVED, 'target': target}}


def stuck_memory(monkeypatch):
    """A plan whose only order cannot start (one general at its source).

    `_plan_pending` is False, which is the only state in which the planner
    rebuilds — and therefore the only state in which the identity comparison
    runs. The stored plan's steps are execution ids; the rebuild names local
    ids, so comparing the two never matched and the plan was re-adopted on
    every observation (refreshing ``chart_plan_tick``).
    """
    mem = memory()
    mem['garrison'] = {'グルーポフ': ['ライム'], 'オケアナ': ['ゼウス', 'ポセイドン']}
    monkeypatch.setattr(replan, 'interim_candidates', lambda *a, **k: candidates())
    return mem


def rebuild(mem, tick):
    mem['tick'] = tick
    replan._mechanical_chart(mem, mem['chart_adjust'], 'abcd1234abcd1234', 'normal')


def test_the_identical_rebuild_keeps_the_stored_plan(monkeypatch):
    mem = stuck_memory(monkeypatch)
    rebuild(mem, 1000)
    plan = _plan_region(mem)['chart_plan']
    tick = mem['chart_plan_tick']
    rebuild(mem, 1001)
    assert _plan_region(mem)['chart_plan'] is plan
    assert mem['chart_plan_tick'] == tick


def test_a_changed_candidate_list_replaces_the_kept_plan(monkeypatch):
    mem = stuck_memory(monkeypatch)
    rebuild(mem, 1000)
    first = _plan_region(mem)['chart_plan']
    monkeypatch.setattr(replan, 'interim_candidates',
                        lambda *a, **k: candidates('トウトウ'))
    rebuild(mem, 1001)
    assert _plan_region(mem)['chart_plan'] is not first
    assert mem['chart_plan_tick'] == 1001


def test_a_stale_launched_step_is_released_when_the_plan_is_kept(monkeypatch):
    """A kept plan must not make its 'launched' marker permanent."""
    mem = stuck_memory(monkeypatch)
    rebuild(mem, 1000)
    step = _plan_region(mem)['chart_plan']['orders'][0]['step']
    _plan_region(mem)['orders'][step] = 'launched'      # no sortie record: not marching
    rebuild(mem, 1001)
    assert _plan_region(mem)['orders'][step] == 'pending'


def test_a_stored_plan_without_local_step_is_rebuilt(monkeypatch):
    """A plan from before the field existed cannot prove identity: rebuild."""
    mem = stuck_memory(monkeypatch)
    rebuild(mem, 1000)
    first = _plan_region(mem)['chart_plan']
    for order in _plan_region(mem)['chart_plan']['orders']:
        order.pop('local_step', None)
    rebuild(mem, 1001)
    assert _plan_region(mem)['chart_plan'] is not first
    assert mem['chart_plan_tick'] == 1001

