"""v353: a deferred repair waits for idle map time and can never hold sorties.

g1097 2026-11-02: no sortie left for tick 33014-47460 (about 4.6 hours) with
four lost castles and idle generals at home. The base and mechanical charts
were both fine; ``map_step`` was simply never reached.
"""
from pathlib import Path

import pytest

from docich.hanjuku.flows import house
from docich.hanjuku.flows import map as map_flow
from docich.hanjuku.os_layer import navigation, phase as phase_module
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.strategy import goals
from docich.hanjuku_screen import Screen

FIXTURES = Path(__file__).parent / 'fixtures' / 'hanjuku'
STARVED = FIXTURES / 'g1097-map-sortie-starved.png'


def map_screen():
    return Screen([], None, '', kind='map')


def idle_mem():
    mem = {'chapter': 1, 'tick': 100, 'month': '1-6', '_records': [],
           'recruit_field_scan_attempts': {'scope': [1, '1-6'], 'count': 2},
           'house_field_scan': {'scope': [1, '1-6'], 'tick': 95},
           'house_eggs': {'ゼウス': {'broken': True}},
           'captured': ['カストーラ'], 'garrison': {'カストーラ': ['ゼウス']}}
    goals.refresh(mem)
    return mem


def records(mem, decision):
    return [r for r in mem['_records'] if r['decision'] == decision]


def starved_frame():
    frame = read_png(STARVED).resized()
    return frame, parse(frame, phase=phase_module.classify(frame))


def test_lifetime_alignment_count_blocks_every_later_repair():
    """The measured frame: a 838-step history leaves the cursor unmoved."""
    frame, screen = starved_frame()
    assert screen.kind == 'map'
    stale = {'align_steps': {'HOUSE:source': 838}}
    assert navigation._align_on_roof(screen, stale, frame, {'own'}, 'HOUSE:source') is None
    fresh = {'align_steps': {}}
    assert navigation._align_on_roof(screen, fresh, frame, {'own'}, 'HOUSE:source')


@pytest.mark.parametrize('next_phase, key', [
    ('castle_open', 'HOUSE:source'),
    ('income_open', 'HOUSE:income'),
])
def test_each_castle_jump_gets_its_own_alignment_budget(monkeypatch, next_phase, key):
    from docich.hanjuku.os_layer import readers
    monkeypatch.setattr(readers, 'world_cursor', lambda frame: (10.0, 20.0))
    monkeypatch.setattr(readers, 'world_flags', lambda *a: {})
    mem = {'house': {'phase': 'castle_view', 'age': 0}, 'chapter': 5,
           'align_steps': {key: 838, 'other': 2}}
    actions = house._view_move(Screen([], None, '', kind='world_map'), mem, object(),
                               (10.0, 20.0), next_phase)
    assert actions and mem['house']['phase'] == next_phase
    assert mem['align_steps'] == {'other': 2}


def test_repair_goal_waits_until_map_step_found_no_order():
    """The ledger's bypass is for idle map time, not for any quiet frame."""
    mem = idle_mem()
    assert house.step(map_screen(), mem, None) is None
    assert 'house' not in mem
    assert not records(mem, 'house_goal_dispatch')

    goals.note_map_idle(mem)
    mem['tick'] += 1
    assert house.step(map_screen(), mem, None) is not None
    assert mem['house']['phase'] == 'open_roster'
    assert records(mem, 'house_goal_dispatch')


def test_stale_idle_mark_does_not_open_the_bypass():
    mem = idle_mem()
    goals.note_map_idle(mem)
    mem['tick'] += goals.MAP_IDLE_WINDOW + 1
    assert house.step(map_screen(), mem, None) is None
    assert not records(mem, 'house_goal_dispatch')


def test_map_step_with_no_order_marks_idle_and_a_ready_order_does_not(monkeypatch):
    monkeypatch.setattr(map_flow, '_off_chart', lambda mem: None)
    monkeypatch.setattr(map_flow, 'next_order', lambda mem: None)
    monkeypatch.setattr(map_flow, 'camp_recall_step', lambda *a: None)
    monkeypatch.setattr(map_flow, '_world_map_wanted', lambda mem: False)
    monkeypatch.setattr(map_flow, 'update_world', lambda *a: None)
    mem = {'chapter': 5, 'tick': 900, '_records': [], 'select_focus_tick': 900}
    assert map_flow.map_step(map_screen(), mem, None) == []
    assert goals.map_idle(mem)

    monkeypatch.setattr(map_flow, 'next_order', lambda m: (_ for _ in ()).throw(AssertionError('stop')))
    busy = {'chapter': 5, 'tick': 900, '_records': [], 'select_focus_tick': 900}
    with pytest.raises(AssertionError, match='stop'):
        map_flow.map_step(map_screen(), busy, None)
    assert not goals.map_idle(busy)
