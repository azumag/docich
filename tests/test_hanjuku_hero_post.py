"""The hero stands one road castle behind the front.

Owner 2026-11-12: 主人公は前線の自城一つ前の自城に常駐したほうが良い。
新しく将軍をやとったときに常に主人公のいる後方から送ることになる。
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C, D = ROAD[0], ROAD[1], ROAD[2], ROAD[3]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(hero_castle, owned, **extra):
    """`owned` castles are ours; the target is the first road castle not ours."""
    garrison = {castle: (['どうし'] if castle == hero_castle else ['リーキ'])
                for castle in owned}
    mem = {'chapter': 9, 'tick': 5000, 'month': '19-4',
           'captured': [c for c in owned if c != chart.home_castle(9)],
           'lost': [], 'orders': {}, 'garrison': garrison, 'sorties': {},
           'world': {'garrison': garrison, 'sorties': {}, 'launched_orders': {}},
           '_records': []}
    mem.update(extra)
    return mem


def hero_orders(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o.get('corridor_hero')]


def test_the_hero_steps_back_when_a_wave_left_him_in_the_front():
    # A, B, C ours; the target is D, so the front is C and the post is B.
    mem = memory(C, [A, B, C])
    orders = hero_orders(mem)
    assert len(orders) == 1
    assert (orders[0]['source'], orders[0]['target']) == (C, B)


def test_the_hero_walks_forward_while_he_is_behind_the_post():
    # A, B, C ours, hero still in A: he is two castles behind the front.
    mem = memory(A, [A, B, C])
    orders = hero_orders(mem)
    assert len(orders) == 1
    assert orders[0]['source'] == A
    assert orders[0]['target'] == B          # one hop forward, never straight to the front


def test_the_hero_on_his_post_gets_no_order():
    mem = memory(B, [A, B, C])
    assert hero_orders(mem) == []


def test_no_post_exists_when_the_home_castle_is_the_front():
    # Only the home castle is ours and the target is next to it: there is no
    # castle behind the front, so the hero simply stays.
    mem = memory(A, [A])
    assert hero_orders(mem) == []


def test_a_marching_hero_gets_no_second_order():
    mem = memory(C, [A, B, C],
                 sorties={'I:c9:%s>%s:h0' % (C, B): {
                     'general': 'どうし', 'source': C, 'target': B,
                     'purpose': 'move', 'status': 'en_route', 'tick': 4990}},
                 world={'garrison': {C: ['どうし']}, 'sorties': {
                     'I:c9:%s>%s:h0' % (C, B): {
                         'general': 'どうし', 'source': C, 'target': B,
                         'purpose': 'move', 'status': 'en_route', 'tick': 4990}},
                        'launched_orders': {}})
    assert hero_orders(mem) == []
