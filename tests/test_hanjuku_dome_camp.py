"""v306: our camp drawn as a dome is a camp too.

g1075 2026-10-29: ゼウス and others camped inside the cliff ring beside the
chapter 3 cave and never came back (owner: キャンプグラフィックが２名以上だと
変わる). The tent there is a dome with a brown door under the red/orange flag,
not the small yellow/orange tent ``own_camps`` measured, so no frame of it
ever started a recall. Both live frames are winter (13:31 and 13:34), so the
dome may also be the winter tent; what A opens on it is not measured yet.
- g1075-boss-target-enclosure.png   13:31:26, the dome at the ring's top right
- g1075-ch3-dome-camp-cave-ring.png 13:34, the same dome 32 px left, 16 up
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows.recall import DOME_SCREEN_WAIT
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import own_camps
from docich.hanjuku_screen import Screen

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
DOMES = {'g1075-boss-target-enclosure.png': (173, 42),
         'g1075-ch3-dome-camp-cave-ring.png': (141, 26),
         'g1097-dome-beside-house.png': (142, 117),
         'g1097-probe-dome-under-cursor.png': (131, 110),
         'g1097-probe-fort-list.png': (132, 110)}


@pytest.mark.parametrize('name,target', sorted(DOMES.items()))
def test_the_live_dome_is_our_camp(name, target):
    assert own_camps(read_png(FIXTURES / name)) == [
        {'target': target, 'clipped': False, 'kind': 'dome'}]


def test_no_other_saved_frame_shows_a_dome():
    checked = 0
    for path in sorted(FIXTURES.glob('*.png')):
        if path.name in DOMES:
            continue
        frame = read_png(path)
        if (frame.width, frame.height) != (256, 224):
            continue
        checked += 1
        assert not [c for c in own_camps(frame) if c.get('kind') == 'dome'], path.name
    assert checked > 100


def test_the_small_tent_keeps_its_measured_cell():
    frame = read_png(FIXTURES / 'g1075-ch3-camp-beside-boss-building.png')
    assert own_camps(frame) == [{'target': (130, 115), 'clipped': False}]


MAP = Screen([], None, '', kind='map', cursor=(173, 42))


def test_a_dome_starts_a_recall_and_an_unmeasured_screen_is_closed(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (173, 42), 'clipped': False,
                                                         'kind': 'dome'}])
    mem = {'chapter': 3, 'tick': 100, '_records': []}
    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('a')]
    assert mem['recall']['stage'] == 'menu' and mem['recall']['camp_kind'] == 'dome'
    found = [r for r in mem['_records'] if r['decision'] == 'camp_found']
    assert found and found[-1]['observed_metric']['kind'] == 'dome'
    other = Screen([], None, 'ゼウス', kind='general_list')
    for _ in range(DOME_SCREEN_WAIT - 1):
        assert p.camp_recall_step(other, mem, object()) == []
    assert p.camp_recall_step(other, mem, object()) == [p.pad('b')]
    assert 'recall' not in mem and mem['recall_skip'] == {'tick': 100}
    closed = mem['_records'][-1]
    assert closed['decision'] == 'camp_group_screen'
    assert closed['observed_metric']['screen'] == 'general_list'


def test_a_small_tent_keeps_waiting_on_other_screens(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (173, 42), 'clipped': False}])
    mem = {'chapter': 3, 'tick': 100, '_records': []}
    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('a')]
    other = Screen([], None, '', kind='text')
    for _ in range(DOME_SCREEN_WAIT + 2):
        assert p.camp_recall_step(other, mem, object()) == []
    assert mem['recall']['stage'] == 'menu'
