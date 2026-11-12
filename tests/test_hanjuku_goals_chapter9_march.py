"""Chapter 9's mechanical march owns every move; refilling is not a want.

Owner 2026-11-11: ９話は機械的に次の城にすべての将軍を送る、それ以外はdisable.
Empty road castles are the expected state there (``chart.guard_free``), so the
goal ledger must not queue a ``move`` for every one of them. g1163
2026-11-12 13:4x held six never-attempted move goals (queued 86867-92397)
while all four rear castles read ``[]``.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import goals

ROAD = chart.corridor(9)


def _memory(chapter, owned, garrison=None):
    garrison = garrison if garrison is not None else {castle: [] for castle in owned}
    mem = {'chapter': chapter, 'tick': 94100, 'month': '30-1',
           'captured': [c for c in owned if c != chart.home_castle(chapter)],
           'lost': [], 'world': {'garrison': garrison, 'sorties': {}},
           'garrison': garrison, '_records': [], 'goals': []}
    return mem


def wanted(mem):
    with chart.map_context(mem):
        return goals._wanted(mem)


def test_chapter_nine_queues_no_refill_move_for_empty_castles():
    mem = _memory(9, [ROAD[0], ROAD[1], ROAD[2]])
    got = wanted(mem)
    assert not [key for key in got if key[0] == 'move']
    with chart.map_context(mem):
        goals.refresh(mem)
    assert not [g for g in mem['goals'] if g.get('kind') == 'move']
    assert not [r for r in mem['_records'] if r.get('decision') == 'goal_queued']


def test_another_chapter_keeps_the_refill_want():
    home = chart.home_castle(8)
    other = [c for c in sorted(chart.castles(8)) if c != home][0]
    mem = _memory(8, [home, other])
    got = wanted(mem)
    assert ('move', None, other) in got, 'the refill intent still exists outside the march'


def test_the_boss_phase_stops_being_a_march():
    mem = _memory(9, list(ROAD))
    got = wanted(mem)
    assert ('move', None, ROAD[0]) in got, 'once the road is taken, refilling is a want again'


def test_existing_refill_goals_are_removed_during_march():
    """Previously queued no-op goals must not survive the next refresh."""
    mem = _memory(9, [ROAD[0], ROAD[1], ROAD[2]])
    mem['goals'] = [
        {'kind': 'move', 'actor': None, 'target': ROAD[0],
         'queued': 86867, 'attempts': 0},
        {'kind': 'move', 'actor': None, 'target': ROAD[1],
         'queued': 86868, 'attempts': 2, 'outcome': 'deferred'},
    ]
    with chart.map_context(mem):
        goals.refresh(mem)
    assert not [g for g in mem['goals'] if g.get('kind') == 'move']
    assert not [r for r in mem['_records'] if r.get('decision') == 'goal_queued']
    assert len([r for r in mem['_records'] if r.get('decision') == 'goal_resolved']) == 1
