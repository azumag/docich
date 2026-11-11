"""Chapter 9 moves ride the owned road straight to the front castle (docich#3952).

Measured 2026-11-11 22:2x-23:0x: hop-by-hop relays fed the front at ~1 general
per 5 minutes, the front never held a squad and オオキク's defender recovered
between attacks (18 -> 34 -> 44). A rear castle's transfer now targets the
castle beside the next road castle in one march whenever every road castle in
between is ours; one enemy castle on the way falls back to the one-hop relay.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.os_layer import world as W
from docich.hanjuku.state.regions import plan as _plan_region, world as _world
from docich.hanjuku.strategy import corridor, routes
from docich.hanjuku.strategy import orders as O

from test_hanjuku_chapter9_mechanical_march import _mem, _orders

ROAD = chart.corridor(9)
HOME = ROAD[0]


def _road_mem(*, upto=6, **extra):
    """Every road castle through index ``upto - 1`` is ours, each with spares."""
    captured = list(ROAD[:upto])
    mem = {'chapter': 9, 'tick': 5000, 'month': '19-4', 'captured': captured,
           'lost': [], 'orders': {}, 'sorties': {}, '_records': [],
           'garrison': {c: [f'将軍{i}' for i in range(2)] for c in captured}}
    mem.update(extra)
    return mem


def test_a_rear_castle_moves_straight_to_the_front_castle():
    mem = _road_mem()                          # ドンドン is the front, オオキク next
    with chart.map_context(mem):
        orders = corridor.orders(mem)
    front = ROAD[5]                            # ドンドン
    moves = [o for o in orders if o['purpose'] == 'move' and not o.get('corridor_hero')]
    assert moves and all(o['target'] == front for o in moves)
    assert {o['source'] for o in moves} == set(ROAD[:5]) - {front}


def test_an_enemy_castle_on_the_way_retargets_the_last_owned_castle():
    """オシロガ lost: the front recomputes to サヌト and moves still go direct."""
    mem = _road_mem()
    mem['captured'] = [c for c in mem['captured'] if c != ROAD[4]]   # オシロガ lost
    mem['lost'] = [ROAD[4]]
    with chart.map_context(mem):
        orders = corridor.orders(mem)
    moves = {(o['source'], o['target']) for o in orders if o['purpose'] == 'move'}
    assert (ROAD[3], ROAD[4]) not in moves     # a move never lands on an enemy castle
    assert {o['target'] for o in orders if o['purpose'] == 'move'} == {ROAD[3]}
    for o in orders:
        assert o['target'] in mem['captured'] or o['purpose'] != 'move'


def test_a_broken_road_leg_falls_back_to_the_first_hop():
    """The one-hop relay remains when the direct leg cannot be proven owned."""
    mem = _road_mem()
    calls = iter([None, None])                 # move_leg cannot prove the leg
    real = routes.move_leg
    try:
        routes.move_leg = lambda *a, **k: False
        with chart.map_context(mem):
            orders = corridor.orders(mem)
    finally:
        routes.move_leg = real
    moves = {o['source']: o['target'] for o in orders if o['purpose'] == 'move'}
    for source, hop in moves.items():
        route = routes.path(mem, source, ROAD[6], set(mem['captured']))
        assert hop == route[1]                 # exactly the old relay hop


def test_the_move_leg_gate_accepts_owned_roads_and_rejects_gaps():
    mem = _road_mem()
    with chart.map_context(mem):
        assert routes.move_leg(mem, ROAD[0], ROAD[5])        # 5 hops, all owned
        order = {'purpose': 'move', 'target': ROAD[5]}
        assert routes.order_allowed(mem, order, ROAD[0])
        order = {'purpose': 'move', 'target': ROAD[6]}       # enemy target: no
        assert not routes.order_allowed(mem, order, ROAD[0])
        attack = {'purpose': 'retake', 'target': ROAD[6]}
        assert not routes.order_allowed(mem, attack, ROAD[0])    # attacks stay adjacent
        assert routes.order_allowed(mem, attack, ROAD[5])


def test_a_long_direct_march_gets_a_hop_bound_arrival_limit():
    mem = _road_mem()
    with chart.map_context(mem):
        far = {'source': ROAD[0], 'target': ROAD[5]}
        near = {'source': ROAD[4], 'target': ROAD[5]}
        assert W.march_arrival_limit(mem, far) > W.march_arrival_limit(mem, near)


def test_the_squad_cap_and_guard_free_still_hold():
    mem = _road_mem()
    with chart.map_context(mem):
        assert chart.guard_free(9)
        for i in range(corridor.SQUAD):
            step = f'{corridor.PREFIX}{ROAD[5]}>{ROAD[6]}:{i}'
            _world(mem).setdefault('sorties', {})[step] = {'general': f'将軍{i}', 'source': ROAD[5],
                                   'target': ROAD[6], 'purpose': 'attack',
                                   'status': 'en_route', 'tick': 5000}
            _plan_region(mem).setdefault('orders', {})[step] = 'launched'
        assert not [o for o in corridor.orders(mem) if o['purpose'] == 'attack']
        moves = [o for o in corridor.orders(mem) if o['purpose'] == 'move']
        assert moves                       # moves never wait for a full squad


def test_direct_moves_still_count_against_the_wave_budget():
    mem = _road_mem()
    with chart.map_context(mem):
        corridor.orders(mem)               # opens the wave
        for i in range(corridor.SQUAD):
            step = f'{corridor.PREFIX}{ROAD[2]}>{ROAD[5]}:{100 + i}'
            _world(mem).setdefault('sorties', {})[step] = {'general': f'将軍{i}', 'source': ROAD[2],
                                   'target': ROAD[5], 'purpose': 'move',
                                   'status': 'en_route', 'tick': 5000}
            _plan_region(mem).setdefault('orders', {})[step] = 'launched'
        assert not [o for o in corridor.orders(mem)
                    if o['purpose'] == 'move' and o['source'] == ROAD[2]]
