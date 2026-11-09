"""Chapter 9 is one winding road (owner 2026-11-09): the planner may only attack
the next castle on it, from an adjacent owned castle, and the hero guard is off.

Runs with the speedrun chart off, exactly as production does.
"""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.strategy import replan, routes
from docich.hanjuku.strategy.orders import _distance

ROAD = chart.corridor(9)
HOME = chart.home_castle(9)


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(owned_count, garrison=None, **extra):
    """Own the first ``owned_count`` castles of the road (home included)."""
    owned = ROAD[:owned_count]
    base = {'chapter': 9, 'tick': 1000, 'month': '1-7',
            'captured': [c for c in owned if c != HOME], 'orders': {},
            'garrison': {c: ['ココット', 'リーキ', 'ヘラ'] for c in owned},
            '_records': []}
    base['garrison'][HOME] = ['どうし', 'ゼウス', 'ヴィーナス']
    if garrison:
        base['garrison'] = garrison
    return {**base, **extra}


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in replan.interim_candidates(mem).values()
                if o['purpose'] in ('attack', 'retake')]


def test_the_road_is_the_castle_name_sentence_and_covers_every_castle():
    assert ''.join(c[0] for c in ROAD[1:]) == 'ハタサオドオナシカイイ'
    assert set(ROAD) == set(chart.CASTLE_NAMES[9]) - {'ボス'}
    assert ROAD[0] == HOME


def test_distance_on_the_road_counts_hops_not_straight_lines():
    # ナッテ is beside アルマムーン in a straight line yet seven hops away.
    cells = chart.castles(9)
    straight = abs(cells['ナッテ'][0] - cells[HOME][0]) + abs(cells['ナッテ'][1] - cells[HOME][1])
    assert _distance(9, HOME, 'ナッテ') == 7 * chart.CORRIDOR_STEP
    assert _distance(9, HOME, 'ハヤク') == chart.CORRIDOR_STEP
    assert straight < _distance(9, HOME, 'ナッテ')


@pytest.mark.parametrize('owned_count', range(1, len(ROAD)))
def test_only_the_next_castle_on_the_road_is_attacked_from_its_neighbour(owned_count):
    got = attacks(memory(owned_count))
    assert got, 'the front always has an attack to send'
    assert {o['target'] for o in got} == {ROAD[owned_count]}
    for order in got:
        assert order['source'] == ROAD[owned_count - 1], order


def test_a_lost_castle_in_the_middle_of_the_road_is_retaken_before_going_on():
    mem = memory(6, lost=['タオ'])
    mem['captured'] = [c for c in ROAD[1:6] if c != 'タオ']      # タオ fell, サヌト.. still ours
    got = attacks(mem)
    assert {o['target'] for o in got} == {'タオ'}
    assert all(o['source'] in ('ハヤク', 'サヌト') for o in got)


def test_the_road_only_allows_adjacent_marches():
    mem = {'chapter': 9}
    assert routes.allowed(mem, 'ハヤク', 'タオ') and routes.allowed(mem, 'タオ', 'ハヤク')
    assert not routes.allowed(mem, HOME, 'ナッテ')
    assert not routes.allowed(mem, 'サヌト', 'ドンドン')          # オシロガ lies between
    assert routes.allowed(mem, 'ハヤク', chart.boss_castle(9))    # the boss is not on the road


def test_march_blocking_by_straight_line_is_off_on_the_road():
    owned = set(ROAD[:5])
    assert replan._march_blocker(9, HOME, 'タオ', owned) is None


def test_route_to_a_distant_castle_walks_every_owned_castle_in_between():
    mem = {'chapter': 9}
    owned = set(ROAD[:4])
    assert routes.path(mem, HOME, 'オシロガ', owned) == (HOME, 'ハヤク', 'タオ', 'サヌト', 'オシロガ')


def test_spares_from_the_rear_are_staged_one_hop_toward_the_front():
    mem = memory(4, garrison={HOME: ['どうし', 'ゼウス', 'ヴィーナス'],
                              'ハヤク': ['ココット', 'リーキ', 'ヘラ'],
                              'タオ': ['A1', 'A2'], 'サヌト': ['B1', 'B2']})
    with chart.map_context(mem):
        moves = [o for o in replan.interim_candidates(mem).values() if o['purpose'] == 'move']
    assert moves, 'rear spares must start walking toward the front'
    for move in moves:
        assert routes.allowed(mem, move['source'], move['target']), move


def test_hero_guard_is_lifted_only_on_chapter_nine():
    names = ['どうし', 'ゼウス', 'ヴィーナス']
    with chart.map_context({'chapter': 9}):
        assert chart_adjust.hero_guard_short(names, 'ゼウス') is False
        assert chart_adjust.hero_guard_short(names, 'ヴィーナス') is False
    with chart.map_context({'chapter': 5}):
        assert chart_adjust.hero_guard_short(names, 'ゼウス') is True
    assert chart.guard_free(9) and not chart.guard_free(5)


def test_the_boss_is_not_attacked_until_a_skull_cell_is_measured():
    mem = memory(len(ROAD))                      # every road castle is ours
    chart.set_boss_cell(9, None)
    assert [o for o in attacks(mem) if chart.is_boss_castle(o['target'])] == []
    chart.set_boss_cell(9, (500, 300))
    try:
        got = attacks(mem)
        assert got and {o['target'] for o in got} == {chart.boss_castle(9)}
    finally:
        chart.set_boss_cell(9, None)


def test_the_encoded_chart_legs_follow_the_road():
    chain = [o for o in chart.all_orders(9) if o['step'].startswith('9-K')]
    for order in chain:
        if order['source'] == HOME:
            continue
        assert chart.corridor_gap(9, order['source'], order['target']) == 1, order['step']


def test_a_front_castle_may_send_its_last_general_on_chapter_nine_only():
    # Owner 2026-11-09: the "keep a defender" limit is not a game rule.
    assert chart.sender_min(9) == 1 and chart.sender_min(5) == 2
    mem = memory(3, garrison={HOME: ['どうし'], 'ハヤク': ['ココット', 'リーキ'], 'タオ': ['ヘラ']})
    got = attacks(mem)
    assert [(o['source'], o['target'], o['general']) for o in got] == [('タオ', 'サヌト', 'ヘラ')]
