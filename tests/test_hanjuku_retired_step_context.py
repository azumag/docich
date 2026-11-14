"""Settled steps lose their per-step plan context where they settle (#4054).

PR #4053 taught the state budget to trim these maps as a last resort, but
the writers kept adding context for orders that had already settled:
g1163 chapter 9 (2026-11-13 09:5x) still carried 179 settled entries in
``order_context`` alone, most of the record's 185 KB. The readers only ever
look a step up through a live order or march, so the context of a settled,
unreferenced step is dead weight that grows with every failure.
"""
from docich.hanjuku.strategy import orders as O, replan
from docich.hanjuku.state.regions import plan as _plan_region

MAPS = O.PLAN_STEP_CONTEXT_MAPS


def memory():
    return {'chapter': 9, 'tick': 100, '_records': [],
            'world': {'sorties': {}, 'launched_orders': {}}}


def seed(mem, step, status):
    plan = _plan_region(mem)
    plan.setdefault('orders', {})[step] = status
    for key in MAPS:
        plan.setdefault(key, {})[step] = {'seed': key}
    return plan


def context_steps(mem):
    plan = _plan_region(mem)
    return {key: set(plan.get(key) or {}) for key in MAPS}


def test_finish_order_drops_the_settled_steps_context():
    mem = memory()
    plan = seed(mem, 'A:r1:M1', 'pending')
    plan['active'] = 'A:r1:M1'
    O._finish_order(mem, 'failed', reason='test')
    assert plan['orders']['A:r1:M1'] == 'failed'
    assert context_steps(mem) == {key: set() for key in MAPS}
    # Emptied maps are removed, not left as husks (the budget trim's shape).
    assert all(key not in plan for key in MAPS)


def test_finish_order_done_drops_context_too():
    mem = memory()
    plan = seed(mem, 'A:r1:M2', 'pending')
    plan['active'] = 'A:r1:M2'
    O._finish_order(mem, 'done', reason='test')
    assert context_steps(mem) == {key: set() for key in MAPS}


def test_finish_order_launched_keeps_context():
    mem = memory()
    plan = seed(mem, 'A:r1:M3', 'pending')
    plan['active'] = 'A:r1:M3'
    O._finish_order(mem, 'launched', reason='test')
    assert context_steps(mem) == {key: {'A:r1:M3'} for key in MAPS}


def test_a_live_order_keeps_its_context():
    mem = memory()
    seed(mem, 'A:r1:M4', 'pending')
    seed(mem, 'A:r1:M5', 'failed')
    O.drop_retired_step_context(mem)
    assert context_steps(mem) == {key: {'A:r1:M4'} for key in MAPS}


def test_a_step_a_live_march_still_resolves_keeps_its_context():
    mem = memory()
    seed(mem, 'A:r1:M6', 'failed')
    mem['world']['sorties']['A:r1:M6'] = {'general': 'ジェラート'}
    O.drop_retired_step_context(mem)
    assert context_steps(mem) == {key: {'A:r1:M6'} for key in MAPS}


def test_a_step_with_retry_context_keeps_its_context():
    mem = memory()
    seed(mem, 'A:r1:M7', 'failed')
    mem['retry_context'] = {'A:r1:M7': {'deviation_reason': 'x'}}
    O.drop_retired_step_context(mem)
    assert context_steps(mem) == {key: {'A:r1:M7'} for key in MAPS}


def test_a_step_waiting_for_rearm_keeps_its_context():
    mem = memory()
    plan = seed(mem, 'A:r1:M8', 'failed')
    plan['plan_step_rearm'] = {'A:r1:M8': {'tick': 90}}
    O.drop_retired_step_context(mem)
    assert context_steps(mem) == {key: {'A:r1:M8'} for key in MAPS}


def test_suppressed_failure_drops_its_context():
    mem = memory()
    mem['failed_local_step'] = {'M9': 95}
    seed(mem, 'M9', 'pending')
    replan._suppress_recent_failures(mem, [{'step': 'M9'}])
    assert _plan_region(mem)['orders']['M9'] == 'failed'
    assert context_steps(mem) == {key: set() for key in MAPS}


def test_no_settled_step_keeps_context_after_a_sweep():
    """The headline invariant: settled + unreferenced means no context."""
    mem = memory()
    seed(mem, 'A:r1:S1', 'failed')
    seed(mem, 'A:r1:S2', 'done')
    seed(mem, 'A:r1:S3', 'pending')
    seed(mem, 'A:r1:S4', 'launched')
    O.drop_retired_step_context(mem)
    assert context_steps(mem) == {key: {'A:r1:S3', 'A:r1:S4'} for key in MAPS}
