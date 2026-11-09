"""An empty castle takes this cycle's lowest-priority attack (v593).

g1163 chapter 8 2026-11-09: しらかわ sat at zero recorded defenders while the
attack generator booked every spare, and ながはま fell with its only defender
earlier. Retakes keep their booked generals (owner 2026-11-05 #3266) and a
boss-only endgame never stages a move (owner 2026-11-05).
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan

NORMAL = [c for c in chart.castles(8) if c != 'ボス']


def memory(empty_castle, captured=None, donors=6):
    owned = NORMAL[:3]
    garrison = {c: [f'{c}{i}' for i in range(donors)] for c in owned}
    garrison[empty_castle] = []
    return {'chapter': 8, 'month': '14-9', 'tick': 5000, '_records': [],
            'captured': list(captured if captured is not None else owned),
            'lost': [], 'orders': {}, 'sorties': {}, 'gold': 1500,
            'house_eggs': {}, 'general_statuses': {'statuses': {}},
            'world': {'garrison': garrison, 'general_location_unknown': []}}


def _orders(mem):
    return list(replan.interim_candidates(mem).values())


def test_the_empty_castle_gets_this_cycle_slowest_attack():
    candidates = _orders(memory(NORMAL[1]))
    moves = [o for o in candidates if o.get('purpose') == 'move' and o.get('target') == NORMAL[1]]
    assert moves, [o.get('target') for o in candidates]
    # The staffing move rides ahead of the attacks it displaced.
    first_attack = next((i for i, o in enumerate(candidates) if o.get('purpose') == 'attack'), None)
    first_move = next(i for i, o in enumerate(candidates) if o in moves)
    assert first_attack is None or first_move < first_attack


def test_a_boss_only_endgame_never_stages_an_empty_castle():
    # Every normal castle captured: owner 2026-11-05 dropped staging moves here.
    mem = memory(NORMAL[1], captured=list(NORMAL))
    moves = [o for o in _orders(mem) if o.get('purpose') == 'move']
    assert not moves, [(o.get('target'), o.get('note')) for o in moves]
