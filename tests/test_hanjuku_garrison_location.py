"""A lost castle's residents stay on the books as location-unknown (docich#3963).

g1163 2026-11-12 01:2x: the country held 32 generals while ``world.garrison``
listed 0 and ``general_location_unknown`` held 5. Two leaks fed that deficit:
every loss path popped ``garrison[castle]`` and discarded the members, and the
corridor plan never revisited a castle it had marked drained, so nobody was
left to re-read a list.
"""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.os_layer import world as os_world
from docich.hanjuku.strategy import corridor


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def test_world_flag_loss_marks_residents_unknown():
    mem = {'chapter': 9, 'tick': 100, '_records': [], 'captured': ['タオ'],
           'world': {'garrison': {'タオ': ['ラズベリー', 'セルリアク']}}}
    policy._apply_world_flags(mem, {'タオ': 'enemy'})
    world = os_world._world(mem)
    assert 'タオ' not in (world.get('garrison') or {})
    assert set(world['general_location_unknown']) == {'ラズベリー', 'セルリアク'}
    assert 'タオ' in mem['lost']


def test_world_flag_recapture_leaves_no_marks():
    mem = {'chapter': 9, 'tick': 100, '_records': [], 'captured': []}
    policy._apply_world_flags(mem, {'タオ': 'own'})
    assert 'タオ' in mem['captured']
    assert not os_world._world(mem).get('general_location_unknown')


def test_garrison_lost_returns_and_marks_members():
    mem = {'chapter': 9, 'tick': 7,
           'world': {'garrison': {'オシロガ': ['バトウラ']}}}
    assert os_world.garrison_lost(mem, 'オシロガ') == ['バトウラ']
    world = os_world._world(mem)
    assert world['general_location_unknown'] == ['バトウラ']
    assert world['general_location_unknown_at'] == {'バトウラ': 7}
    assert os_world.garrison_lost(mem, 'オシロガ') == []   # already dropped


ROAD = chart.corridor(9)
A, B, C = ROAD[0], ROAD[1], ROAD[2]


def memory(owned, drained, generals=32, garrison=None, unknown=None):
    garrison = garrison if garrison is not None else {}
    mem = {'chapter': 9, 'tick': 5000, 'month': '23-5',
           'captured': [c for c in owned if c != chart.home_castle(9)],
           'lost': [], 'orders': {},
           'country_general_count': {'scope': [9, '23-5'], 'count': generals},
           'world': {'garrison': garrison, 'sorties': {},
                     'general_location_unknown': list(unknown or ())},
           'corridor_drained': {castle: {'kind': 'empty', 'tick': tick, 'month': '23-5'}
                                for castle, tick in drained.items()},
           '_records': []}
    return mem


def orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def test_unaccounted_generals_probe_the_oldest_drained_castle():
    # ハヤク drained long ago, タオ drained just now: the tour starts at ハヤク.
    mem = memory([A, B, C], {A: 1000, B: 4000})
    out = orders(mem)
    assert out and out[0]['source'] == A


def test_the_tour_rotates_as_marks_are_rewritten():
    mem = memory([A, B, C], {A: 4000, B: 1000})
    out = orders(mem)
    assert out and out[0]['source'] == B
    # The visit re-marks B at the current tick; the next cycle moves on to A.
    corridor.mark_drained(mem, B, 'empty')
    out = orders(mem)
    assert out and out[0]['source'] == A


def test_no_probe_when_the_count_is_fully_known():
    garrison = {A: ['ラズベリー'], B: ['セルリアク']}
    mem = memory([A, B, C], {A: 1000}, generals=3,
                 garrison=garrison, unknown=['バトウラ'])
    assert all(order['source'] != A for order in orders(mem))


def test_no_probe_without_a_scoped_count():
    mem = memory([A, B, C], {A: 1000})
    mem['country_general_count'] = {'scope': [9, '20-5'], 'count': 32}
    assert all(order['source'] != A for order in orders(mem))
    mem.pop('country_general_count')
    assert all(order['source'] != A for order in orders(mem))


def test_a_live_garrison_keeps_the_probe_away():
    mem = memory([A, B, C], {B: 1000}, generals=1, garrison={A: ['ラズベリー']})
    assert all(order['source'] != B for order in orders(mem))


# --- the status panel itself is a readable castle list ---------------------

from pathlib import Path

from PIL import Image

from docich.hanjuku.flows import deploy
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).parent / 'fixtures' / 'hanjuku'


def _screen(name):
    im = Image.open(FIXTURES / name).convert('RGB')
    return parse(Frame(im.width, im.height, im.tobytes()))


def test_a_verified_empty_status_panel_records_the_empty_garrison():
    """オシロガ's verified panel reads 「しょうぐん0めい」: empty is a fact."""
    mem = {'chapter': 9, 'tick': 100, '_records': [], 'world': {}}
    deploy._observe_status_garrison(_screen('g1163-source-check-status-panel-oshiroga.png'),
                                    mem, name='オシロガ')
    assert os_world._world(mem)['garrison']['オシロガ'] == []


def test_a_partial_status_panel_writes_nothing():
    """ハヤク's panel says 18 generals but shows 4: no count, no write (v678)."""
    mem = {'chapter': 9, 'tick': 100, '_records': [], 'world': {}}
    deploy._observe_status_garrison(_screen('g1163-castle-status-panel-read-as-general-list.png'),
                                    mem, name='ハヤク')
    assert 'ハヤク' not in (os_world._world(mem).get('garrison') or {})
