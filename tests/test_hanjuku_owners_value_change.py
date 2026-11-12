"""A partial flag read is not an ownership change.

g1163 2026-11-12 08:5x: the same map arrived as 10, 11 or 12 readable flags and
every set counted as news — 56 `world_map_owners` records an hour while nothing
had changed hands.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer import world as W

ROAD = chart.corridor(9)
A, B, C = ROAD[0], ROAD[1], ROAD[2]


def memory(flags):
    return {'chapter': 9, 'tick': 1000, 'month': '26-12', 'captured': [B],
            'world_map_flags': dict(flags), '_records': []}


def records(mem):
    return [r for r in mem['_records'] if r.get('decision') == 'world_map_owners']


def test_the_same_values_are_not_recorded_twice():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'enemy'})
    assert records(mem) == []


def test_a_partial_read_of_unchanged_values_is_silent():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {C: 'enemy'})       # B's roof was hidden
    assert records(mem) == []
    # the known half is not forgotten
    assert mem['world_map_flags'][B] == 'own'


def test_a_real_change_is_recorded_and_remembered():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
    assert len(records(mem)) == 1
    assert mem['world_map_flags'][C] == 'own'
