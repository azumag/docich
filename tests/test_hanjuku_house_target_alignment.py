"""Y intent is not field-marker arrival. All geometry here is synthetic."""
import json

import pytest

from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import commands, world
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import Screen
from test_hanjuku_house_route_safety import route, make_crossing_friendly

FRAME = Frame(256, 224, bytes(256 * 224 * 3))


def setup(route, monkeypatch):
    mem, cells = route
    cells.update(source=(1000, 1000), crossing=(1160, 1000))
    make_crossing_friendly(mem)
    mem['house']['phase'] = 'house_confirm'
    roofs = [{'kind': 'own', 'target': (60, 60), 'clipped': False},
             {'kind': 'own', 'target': (220, 60), 'clipped': False}]
    monkeypatch.setattr(world, 'castle_roofs', lambda _: roofs)
    return mem, roofs


def marker(x=180, y=180):
    return Screen([], None, '', kind='map_target', marker=(x, y))


def test_y_return_offset_is_corrected_from_current_marker_before_dispatch(route, monkeypatch):
    mem, _ = setup(route, monkeypatch)
    mem.update(cursor=[1120, 1120], anchor='source', uncertain=False)
    assert house._repair_step(marker(164), mem, FRAME) == [commands.pad('right', 16)]
    assert mem['house']['phase'] == 'house_confirm'
    assert not mem['house'].get('departure_pending') and not mem.get('sorties')
    mem = json.loads(json.dumps(mem))
    assert house._repair_step(marker(), mem, FRAME) == [commands.pad('a')]
    assert mem['house']['phase'] == 'travel' and mem['house']['departure_pending']
    assert mem['house']['target_observation']['position'] == [1120, 1120]
    assert not mem['house'].get('purchased') and not mem.get('sorties')
    assert house._repair_step(Screen([], None, '', kind='map'), mem, FRAME) == []
    assert any(r['decision'] == 'house_departed' for r in mem['_records'])
    assert not any(r['decision'] in ('house_arrival_seen', 'house_repair_verified') for r in mem['_records'])


@pytest.mark.parametrize('case', ['no_frame', 'one_roof', 'no_marker', 'ambiguous'])
def test_unknown_landing_never_uses_cached_target_and_closes_finitely(route, monkeypatch, case):
    mem, roofs = setup(route, monkeypatch)
    mem.update(cursor=[1120, 1120], anchor='source', uncertain=False)
    mem['house']['target_observation'] = {'position': [1120, 1120], 'target': [1120, 1120]}
    frame = None if case == 'no_frame' else FRAME
    s = marker()
    if case == 'one_roof': roofs.pop()
    elif case == 'no_marker': s.marker = None
    elif case == 'ambiguous':
        # A second matching two-roof constellation at another camera offset.
        _, cells = route; cells['other1'] = (1400, 1400); cells['other2'] = (1560, 1400)
    for _ in range(3):
        actions = house._repair_step(s, mem, frame)
        assert commands.pad('a') not in actions
        mem = json.loads(json.dumps(mem))
    assert mem['house']['phase'] == 'close'
    assert mem['last_house_deferral']['code'] == 'position_unread'
    assert mem['last_house_deferral']['phase'] == 'house_confirm'
    assert not mem['house'].get('departure_pending') and not mem.get('sorties')


def test_ignored_field_alignment_is_bounded_across_json_resume(route, monkeypatch):
    mem, _ = setup(route, monkeypatch)
    for _ in range(8):
        assert house._repair_step(marker(164), mem, FRAME) == [commands.pad('right', 16)]
        mem = json.loads(json.dumps(mem))
    assert house._repair_step(marker(164), mem, FRAME) == []
    assert mem['house']['phase'] == 'close' and not mem['house'].get('departure_pending')
    assert mem['last_house_deferral']['code'] == 'alignment_limit'
    assert house.step(marker(164), mem, FRAME) == [commands.pad('b')]


@pytest.mark.parametrize('value', [None, True, -1, '0', 99])
def test_malformed_alignment_budget_never_restarts_permission(route, monkeypatch, value):
    mem, _ = setup(route, monkeypatch)
    mem['house']['alignment_moves'] = value
    assert house._repair_step(marker(164), mem, FRAME) == []
    assert mem['house']['phase'] == 'close'


def test_a_new_confirmed_target_episode_has_its_own_alignment_budget(route, monkeypatch):
    mem, _ = setup(route, monkeypatch)
    mem['house'].update(phase='out_target', alignment_moves=8, alignment_unread=2)
    assert house._repair_step(marker(164), mem, FRAME) == [commands.pad('y')]
    assert mem['house']['phase'] == 'house_view'
    assert mem['house']['alignment_moves'] == mem['house']['alignment_unread'] == 0


def test_chapter5_own_cell_does_not_fall_back_to_known_wrong_charted_cell(route, monkeypatch):
    """A readable castle constellation is not proof of the house roof."""
    mem, _ = setup(route, monkeypatch)
    mem['chapter'] = 5
    # Chapter 5 has no HOUSE_VIEW coordinate in this test's chart data.
    # Supply synthetic geometry to exercise the fail-closed own-cell guard.
    monkeypatch.setitem(house.HOUSE_TARGET, 5, (140, 140))
    monkeypatch.setattr(house, '_defer_unsafe_house_route', lambda _: False)
    monkeypatch.setattr(house, '_house_roof_delta', lambda *args, **kwargs: None)
    from docich.hanjuku.os_layer import navigation
    monkeypatch.setattr(navigation, 'Y_JUMP_OFFSET', {5: (0, 0)})
    # Exactly on the old charted coordinate: this used to authorize A.
    target = [int(house.HOUSE_TARGET[5][a] * 8) for a in (0, 1)]
    monkeypatch.setattr(world, 'observed_cursor_cell', lambda *args, **kwargs: target)
    assert house._repair_step(marker(), mem, FRAME) == []
    assert mem['house']['phase'] == 'close'
    assert mem['last_house_deferral']['code'] == 'position_unread'
    assert not mem['house'].get('departure_pending')
    assert any(r['decision'] == 'house_entry_roof_unverified' for r in mem['_records'])


def test_failed_same_entry_twice_does_not_dispatch_third_time(route, monkeypatch):
    mem, _ = setup(route, monkeypatch)
    mem['house_entry_failures'] = {'1:12,-8': 2}
    assert house._repair_step(marker(), mem, FRAME) == []
    assert mem['house']['phase'] == 'close'
    assert not mem['house'].get('departure_pending')
    assert any(r['decision'] == 'house_entry_retry_blocked' for r in mem['_records'])


def test_failed_entry_count_survives_json_and_stops_same_candidate(route, monkeypatch):
    mem, _ = setup(route, monkeypatch)
    mem['house'].update(phase='close', target_observation={
        'entry': [12, -8], 'basis': 'house_roof_delta'})
    mem['sorties'] = {'HOUSE_OUT:1': {
        'general': 'ゼウス', 'purpose': 'house', 'status': 'en_route'}}
    house._finish(mem)
    assert mem['house_entry_failures'] == {'1:12,-8': 1}
    assert mem['sorties']['HOUSE_OUT:1']['status'] == 'closed'
    mem = json.loads(json.dumps(mem))
    assert house._entry_failures(mem, (12, -8)) == 1


def test_gift_list_marks_arrival_before_purchase_or_return(route, monkeypatch):
    from types import SimpleNamespace
    from docich.hanjuku.strategy import orders

    mem, _ = setup(route, monkeypatch)
    mem['house'].update(phase='travel', target_observation={
        'entry': [12, -8], 'basis': 'house_roof_delta'})
    mem['sorties'] = {'HOUSE_OUT:1': {
        'general': 'ゼウス', 'purpose': 'house', 'status': 'en_route'}}
    mem['house_entry_failures'] = {'1:12,-8': 1}
    monkeypatch.setattr(house, 'gift_prices', lambda _: dict(house.GIFTS))
    monkeypatch.setattr(orders, '_garrison_move', lambda *args, **kwargs: None)
    screen = SimpleNamespace(kind='text', text='', header={'gold': 250})
    assert house._repair_step(screen, mem, FRAME) == []
    assert mem['house']['phase'] == 'buy'
    assert mem['sorties']['HOUSE_OUT:1']['status'] == 'arrived_house'
    assert mem['house_entry_failures'] == {}
    mem['house']['phase'] = 'close'
    house._finish(mem)
    assert mem.get('house_entry_failures') == {}
    assert mem.get('house_entry_miss') is None
