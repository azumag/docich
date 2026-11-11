"""docich#3957 (owner 2026-11-12): the hero is sent to each fallen castle.

第9話の攻略手順: 敵城が取れるまで主人公以外を送り続け、城が取れたタイミングで
主人公をその城へ送り、その後に増援を送る — を繰り返す。Previously the hero
advanced a single hop per capture; now he keeps walking toward the front (the
castle that just fell) whenever no march carrying him is on the road, and his
order runs ahead of the reinforcement moves.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.state.regions import sorties as _sorties, world as _world
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
HOME = ROAD[0]
HERO = chart.HERO


def _road_mem(upto, hero_at=None, **extra):
    """Own the first ``upto`` road castles; ``hero_at`` is where the hero stands."""
    owned = list(ROAD[:upto])
    mem = {'chapter': 9, 'tick': 5000, 'month': '19-4',
           'captured': [c for c in owned if c != HOME], 'lost': [],
           'orders': {}, 'sorties': {}, '_records': [],
           'garrison': {c: ['将軍A', '将軍B'] for c in owned}}
    if hero_at is not None:
        mem['garrison'][hero_at] = [HERO, '将軍C']
    mem.update(extra)
    return mem


def _orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def _hero(mem, source, target, status, tick=4990):
    step = f'{corridor.PREFIX}{source}>{target}:h{len(_sorties(mem))}'
    _world(mem).setdefault('sorties', {})[step] = {
        'general': HERO, 'source': source, 'target': target,
        'purpose': 'move', 'status': status, 'tick': tick}
    return step


def test_the_hero_is_sent_to_the_castle_that_just_fell():
    """ROAD[3] fell this wave: the hero at ROAD[2] is sent straight into it."""
    mem = _road_mem(4, hero_at=ROAD[2])
    hero = [o for o in _orders(mem) if o.get('corridor_hero')]
    assert len(hero) == 1
    assert (hero[0]['source'], hero[0]['target']) == (ROAD[2], ROAD[3])


def test_the_hero_keeps_walking_to_the_front_after_arriving():
    """A arrived hero march does not end his advance (was: one hop per wave)."""
    mem = _road_mem(4, hero_at=ROAD[1])
    _orders(mem)                                    # opens the wave
    _hero(mem, HOME, ROAD[1], 'arrived')
    hero = [o for o in _orders(mem) if o.get('corridor_hero')]
    assert len(hero) == 1
    assert (hero[0]['source'], hero[0]['target']) == (ROAD[1], ROAD[2])


def test_the_hero_stays_put_once_he_reaches_the_front():
    mem = _road_mem(4, hero_at=ROAD[3])             # ROAD[3] is the front
    assert not [o for o in _orders(mem) if o.get('corridor_hero')]


def test_a_hero_march_on_the_road_holds_the_next_leg():
    mem = _road_mem(4, hero_at=ROAD[1])
    _orders(mem)
    _hero(mem, HOME, ROAD[1], 'en_route')
    assert not [o for o in _orders(mem) if o.get('corridor_hero')]


def test_hero_marching_status_and_age():
    mem = _road_mem(3)
    assert not corridor._hero_marching(mem)
    step = _hero(mem, HOME, ROAD[1], 'en_route')
    assert corridor._hero_marching(mem)
    sortie = _world(mem).setdefault('sorties', {})[step]
    sortie['status'] = 'arrived'
    assert not corridor._hero_marching(mem)
    sortie['status'] = 'en_route'
    sortie['tick'] = 0                              # past the arrival window
    assert not corridor._hero_marching(mem)


def test_an_absent_hero_drops_his_order_for_the_wave():
    mem = _road_mem(3, hero_at=HOME)
    _orders(mem)
    corridor.hero_absent(mem)
    assert not [o for o in _orders(mem) if o.get('corridor_hero')]


def test_the_hero_goes_before_the_reinforcements():
    """Order of the owner steps: attack, then the hero, then the extra movers."""
    mem = _road_mem(4, hero_at=HOME)
    orders = _orders(mem)
    attack_i = next(i for i, o in enumerate(orders) if o['purpose'] in ('attack', 'retake'))
    hero_i = next(i for i, o in enumerate(orders) if o.get('corridor_hero'))
    move_i = next(i for i, o in enumerate(orders)
                  if o['purpose'] == 'move' and not o.get('corridor_hero'))
    assert attack_i < hero_i < move_i


def test_the_vanguard_still_attacks_without_the_hero():
    """Step (1) of the cycle: non-hero generals keep attacking the next castle."""
    mem = _road_mem(3, hero_at=HOME)
    attacks = [o for o in _orders(mem) if o['purpose'] in ('attack', 'retake')]
    assert attacks and all(o['target'] == ROAD[3] and o['source'] == ROAD[2]
                           for o in attacks)
    assert all(not o.get('corridor_hero') for o in attacks)
