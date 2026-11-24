"""Live 2026-11-05: the house flow spun without any input for 46 minutes.

The decision log held 192 house_input_free_hop records over 46 minutes, one
per 1.5 s observation, with planned_actions: []: the flow kept hopping
roster_advance -> roster and back, so the month's roster scan never finished.
A run of observations that issues no pad is not progress, so the flow now
leaves the menu (house_idle_escape) after a bounded run.
"""
from docich import hanjuku_house as house
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen


def memory(phase, **extra):
    return {'chapter': 6, 'month': '50-5', 'gold': 250, 'tick': 500,
            'house': {'phase': phase, 'chapter': 6, 'age': 3, 'total': 0,
                      'general': 'ゼウス', 'pending': [], 'seen': [], 'inspected': [],
                      **extra}}


def test_a_menu_that_never_accepts_input_is_left_after_a_bounded_run():
    mem = memory('status', selected='ゼウス')
    screen = Screen([], None, '', kind='text')   # no status page to read
    for _ in range(house.HOUSE_IDLE_LIMIT - 1):
        assert house.step(screen, mem, None) == []
    assert house.step(screen, mem, None) == [pad('b')]
    assert mem['house']['phase'] == 'leave_roster'
    record = mem['_records'][-1]
    assert record['decision'] == 'house_idle_escape'
    assert record['observed_metric']['observations'] == house.HOUSE_IDLE_LIMIT
    assert record['observed_metric']['phases'] == ['status']
    assert 'idle_observations' not in mem['house']


def test_an_issued_pad_resets_the_idle_run():
    mem = memory('status', selected='ゼウス')
    screen = Screen([], None, '', kind='text')
    for _ in range(3):
        assert house.step(screen, mem, None) == []
    assert mem['house']['idle_observations'] == 3
    mem['house']['phase'] = 'close'
    assert house.step(Screen([], None, '', kind='world_map'), mem, None) == [pad('y')]
    assert 'idle_observations' not in mem['house']
    assert 'idle_phases' not in mem['house']


def test_the_escape_keeps_the_generals_read_so_far():
    mem = memory('status', selected='ゼウス', seen=['どうし', 'ゼウス'], total=2)
    screen = Screen([], None, '', kind='text')
    actions = None
    for _ in range(house.HOUSE_IDLE_LIMIT):
        actions = house.step(screen, mem, None)
    assert actions == [pad('b')]
    assert mem['house']['seen'] == ['どうし', 'ゼウス']
    assert mem['house']['phase'] == 'leave_roster'
