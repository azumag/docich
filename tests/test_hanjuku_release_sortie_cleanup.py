"""A released plan step must not leave a marching ghost behind."""
from docich.hanjuku.strategy import replan
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.state.regions import world as _world, plan as _plan_region, sorties as _sorties


def memory(age=5000):
    return {'chapter': 6, 'tick': 10000, '_records': [],
            'orders': {'A:x:M1': 'launched'},
            'world': {'garrison': {'アルマムーン': []}},
            'sorties': {'A:x:M1': {'general': 'グレナデン', 'source': 'アルマムーン',
                                   'target': 'ボス', 'status': 'launched_unconfirmed',
                                   'tick': 10000 - age}},
            'launched_orders': {}}


def test_a_released_step_drops_its_marching_ghost():
    mem = memory(age=O.SITE_LATE_ENTRY_TICKS + 1)
    replan._release_stale_steps(mem, ['A:x:M1'])
    assert 'A:x:M1' not in _sorties(mem)
    assert _plan_region(mem)['orders']['A:x:M1'] == 'pending'
    assert _world(mem)['garrison']['アルマムーン'] == []
    assert _world(mem)['general_location_unknown'] == ['グレナデン']
    assert [r for r in mem['_records'] if r['decision'] == 'sortie_record_expired']


def test_a_running_march_is_not_released():
    mem = memory(age=100)
    replan._release_stale_steps(mem, ['A:x:M1'])
    assert 'A:x:M1' in _sorties(mem)
    assert _plan_region(mem)['orders']['A:x:M1'] == 'launched'
