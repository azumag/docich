"""A settled march is forgotten after its retention window.

g1163 2026-11-12 19:0x: eight ``オシロガ>ドンドン`` overdue records from a wave
decided long before, one with no target at all, stayed in ``world.sorties``
until the state compaction (SETTLED_MARCH_TICKS).
"""
from docich.hanjuku.state.regions import flow as _flow, sorties as _sorties, world as _world
from docich.hanjuku.strategy import orders

STEP = 'I:c9:A>B:1'


def memory(age, recheck=False):
    mem = {'chapter': 9, 'tick': 5000, '_records': [], 'orders': {}}
    _world(mem).setdefault('sorties', {})[STEP] = {
        'general': 'リーキ', 'source': 'A', 'target': 'B',
        'status': 'overdue', 'tick': 5000 - age}
    if recheck:
        _flow(mem)['location_recheck'] = {STEP: {'tick': 4900}}
    return mem


def retired(mem):
    return [r for r in mem['_records'] if r.get('decision') == 'sortie_overdue_retired']


def test_an_old_overdue_march_is_retired():
    mem = memory(orders.OVERDUE_RETENTION_TICKS + 10)
    orders._relaunch_overdue_march(mem)
    assert STEP not in _sorties(mem)
    assert retired(mem)


def test_a_fresh_overdue_march_is_kept():
    mem = memory(10)
    orders._relaunch_overdue_march(mem)
    assert STEP in _sorties(mem)


def test_a_march_a_recheck_points_at_is_kept():
    mem = memory(orders.OVERDUE_RETENTION_TICKS + 10, recheck=True)
    orders._relaunch_overdue_march(mem)
    assert STEP in _sorties(mem)
