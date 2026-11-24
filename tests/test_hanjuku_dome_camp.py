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
from docich.hanjuku.state.regions import flow as _flow, journal as _journal

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
DOMES = {'g1075-boss-target-enclosure.png': (173, 42),
         'g1075-ch3-dome-camp-cave-ring.png': (141, 26),
         'g1097-dome-beside-house.png': (142, 117),
         'g1097-probe-dome-under-cursor.png': (131, 110),
         'g1097-probe-fort-list.png': (132, 110)}
# Frames that show a 砦 next to an ordinary tent (checked in
# test_hanjuku_ch9_fort_and_cliff_tent.py): the 565 readback of a local core.
WITH_TENT = {'g1190-ch9-tao-fort-and-cliff-tent-565.png',
             'g1190-ch9-tao-cliff-tent-winter-565.png',
             # a real own camp (tent + red flag) beside the cliff at (176, 129)
             'g1190-ch9-target-marker-over-sea-565.png',
             # the 砦 menu/list/card/picker of ch9 2026-11-17 (v987 fixtures):
             # own tents stand beside the fort on the map behind them
             'g1190-ch9-riesling-fort-menu.png',
             'g1190-ch9-riesling-fort-list.png',
             'g1190-ch9-riesling-fort-card.png',
             'g1190-ch9-riesling-fort-picker.png'}


@pytest.mark.parametrize('name,target', sorted(DOMES.items()))
def test_the_live_dome_is_our_camp(name, target):
    assert own_camps(read_png(FIXTURES / name)) == [
        {'target': target, 'clipped': False, 'kind': 'dome'}]


def test_no_other_saved_frame_shows_a_dome():
    checked = 0
    for path in sorted(FIXTURES.glob('*.png')):
        if path.name in DOMES or path.name in WITH_TENT:
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
    assert _flow(mem)['recall']['stage'] == 'menu' and _flow(mem)['recall']['camp_kind'] == 'dome'
    found = [r for r in mem['_records'] if r['decision'] == 'camp_found']
    assert found and found[-1]['observed_metric']['kind'] == 'dome'
    other = Screen([], None, 'ゼウス', kind='general_list')
    for _ in range(DOME_SCREEN_WAIT - 1):
        assert p.camp_recall_step(other, mem, object()) == []
    assert p.camp_recall_step(other, mem, object()) == [p.pad('b')]
    assert 'recall' not in _flow(mem) and _flow(mem)['recall_skip'] == {'tick': 100}
    closed = mem['_records'][-1]
    assert closed['decision'] == 'camp_group_screen'
    assert closed['observed_metric']['screen'] == 'general_list'


def test_a_small_tent_holds_an_unrelated_text_screen_without_spending_steps(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (173, 42), 'clipped': False}])
    mem = {'chapter': 3, 'tick': 100, '_records': []}
    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('a')]
    state = _flow(mem)['recall']
    other = Screen([], None, '', kind='text')
    for tick in range(100, 106):
        mem['tick'] = tick
        assert p.camp_recall_step(other, mem, object()) is None
        assert state['stage'] == 'menu' and state['steps'] == 0
    assert state['hold_since'] == 100



def test_dome_unclassified_text_is_held_then_closed_after_bounded_wait():
    mem = {'chapter': 10, 'tick': 100, '_records': []}
    _flow(mem)['recall'] = {
        'stage': 'menu', 'steps': 0, 'camp_kind': 'dome',
        'target': [131, 113],
    }
    screen = Screen([], None, 'せめこまれました', kind='text')
    for _ in range(DOME_SCREEN_WAIT - 1):
        assert p.camp_recall_step(screen, mem, object()) == []
    assert p.camp_recall_step(screen, mem, object()) == [p.pad('b')]
    record = mem['_records'][-1]
    assert record['decision'] == 'camp_group_screen'
    assert record['resulting_event'] == 'unclassified_screen_closed'
    assert record['observed_metric']['text'] == 'せめこまれました'
