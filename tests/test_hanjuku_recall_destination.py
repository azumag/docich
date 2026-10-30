"""The R picker starts at home, which is not the unit's origin."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen
from docich.hanjuku_pixels import Frame
from test_hanjuku_camp_receipts import picker

VIEW = Screen([], None, '', kind='world_map')


def memory(origin=None):
    return {'chapter': 1, 'tick': 100, '_records': [],
            'captured': ['キカンドン', 'ジョンリギ', 'ゴーメン'],
            'recall': {'stage': 'dest', 'steps': 0, **({'origin': origin} if origin else {})}}


def at(monkeypatch, castle, flags=None):
    x, y = p.chart.castles(1)[castle]; ox, oy = p.WORLD_MAP_OFFSET[1]
    monkeypatch.setattr(p, 'world_cursor', lambda _: (x / 8 + ox, y / 8 + oy))
    monkeypatch.setattr(p, 'world_flags', lambda *_: flags or {
        'アルマムーン': 'own', 'キカンドン': 'own', 'ジョンリギ': 'own', 'ゴーメン': 'own'})
    # Only the picker-marker tests bind an origin from ▲; the fixture's own
    # marker would otherwise rewrite these legacy home-fallback cases.
    monkeypatch.setattr(p, 'world_markers', lambda _: [])


def test_home_cursor_does_not_confirm_when_unit_is_near_kikandon(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert mem['recall']['goal'] == 'キカンドン'
    assert not any(r['decision'] == 'camp_recall_requested' for r in mem['_records'])
    at(monkeypatch, 'キカンドン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('a')]
    assert mem['recall']['stage'] == 'await_dispatch'
    assert mem['recall']['goal'] == 'キカンドン'
    assert mem['recall']['confirm_after_zoom']  # confirming A is deferred to dispatch


def test_unknown_origin_can_join_observed_hero_castle(monkeypatch):
    mem = memory(); mem['garrison'] = {'ゴーメン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert mem['recall']['goal'] == 'ゴーメン'


def test_unknown_origin_prefers_sortie_source_over_hero_castle():
    # issue #2907: origin が束縛できなくても、帰還する本人の出撃元を代理の
    # 現在地として距離判定へ使い、主人公の在城城へ一律固定しない。
    mem = memory()
    mem['garrison'] = {'アルマムーン': [p.NAME]}
    mem['recall']['sorties'] = ['s1']
    mem['sorties'] = {'s1': {'source': 'ジョンリギ'}}
    assert p._recall_goal(mem) == 'ジョンリギ'


def test_captured_sortie_source_uses_nearest_owned_castle():
    # 出撃元が奪取されて自軍城でなくても、その座標を代理 origin として
    # 最も近い自軍城へ帰還する（けっかい(265,270) → ゴーメン(295,606)）。
    mem = memory()
    mem['garrison'] = {'アルマムーン': [p.NAME]}
    mem['recall']['sorties'] = ['s1']
    mem['sorties'] = {'s1': {'source': 'けっかい'}}
    assert p._recall_goal(mem) == 'ゴーメン'


def test_distance_tie_prefers_hero_and_nearby_castle_beats_far_hero():
    mem = memory([649, 765]); mem['garrison'] = {'キカンドン': [p.NAME]}
    assert p._recall_goal(mem) == 'キカンドン'
    mem['recall']['origin'] = list(p.chart.castles(1)['ジョンリギ'])
    assert p._recall_goal(mem) == 'ジョンリギ'


def test_diagonal_distance_selects_the_closest_castle_and_confirms_that_picker(monkeypatch):
    import json
    # SFC units move straight to their destination. Manhattan distance chose
    # ゴーメン (309) over キカンドン (312), although the direct squared
    # distances are respectively60381 and52034. This origin is synthetic.
    mem = memory([370, 840]); mem['garrison'] = {'ゴーメン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert mem['recall']['goal'] == 'キカンドン'
    mem = json.loads(json.dumps(mem))
    at(monkeypatch, 'キカンドン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('a')]
    assert mem['recall']['stage'] == 'await_dispatch'
    assert mem['recall']['goal'] == 'キカンドン'
    assert mem['recall']['confirm_after_zoom']  # confirming A is deferred to dispatch
    assert not mem['recall'].get('input_sent')  # planned A is not a sent receipt


@pytest.mark.parametrize('change', ['marching', 'unknown', 'duplicate', 'enemy'])
def test_unconfirmed_hero_is_not_used_as_a_merge_target(change):
    mem = memory(); mem['garrison'] = {'ゴーメン': [p.NAME]}
    flags = {c: 'own' for c in p._owned(mem)}
    if change == 'marching':
        mem['sorties'] = {'x': {'general': p.NAME, 'status': 'en_route', 'tick': 99}}
    elif change == 'unknown': mem['general_location_unknown'] = [p.NAME]
    elif change == 'duplicate': mem['garrison']['キカンドン'] = [p.NAME]
    else: flags['ゴーメン'] = 'enemy'
    assert p._recall_goal(mem, flags) is None


def test_enemy_nearest_castle_is_excluded_and_selection_updates_after_loss(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    mem['recall']['goal'] = 'キカンドン'
    at(monkeypatch, 'アルマムーン', {'アルマムーン': 'own', 'キカンドン': 'enemy', 'ジョンリギ': 'own'})
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('up')]
    assert mem['recall']['goal'] == 'ジョンリギ'


def test_hotloaded_old_home_goal_is_replanned_before_a(monkeypatch):
    mem = memory(list(p.chart.castles(1)['ゴーメン']))
    mem['recall'].update(goal='アルマムーン', destination_selected=True)
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert mem['recall']['goal'] == 'ゴーメン'


def test_dispatch_receipt_cannot_be_retargeted(monkeypatch):
    mem = memory(list(p.chart.castles(1)['ゴーメン']))
    mem['recall'].update(stage='await_dispatch', goal='アルマムーン', a_inputs=2)
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == []
    assert mem['recall']['goal'] == 'アルマムーン'


def test_picker_stall_has_a_finite_budget(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(p.RECALL_LIMIT - 1):
        assert not any(a.get('buttons') == ['a'] for a in p.world_map_step(VIEW, mem, picker()))
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('b')]
    assert not mem.get('recall')


def chapter3_at(monkeypatch, castle):
    x, y = p.chart.castles(3)[castle]; ox, oy = p.WORLD_MAP_OFFSET[3]
    monkeypatch.setattr(p, 'world_cursor', lambda _: (x / 8 + ox, y / 8 + oy))
    monkeypatch.setattr(p, 'world_flags', lambda *_: {
        c: 'enemy' if c == 'ボス' else 'own' for c in p.chart.castles(3)})
    monkeypatch.setattr(p, 'world_markers', lambda _: [])


def chapter3_memory():
    return {'chapter': 3, 'tick': 100, '_records': [],
            'captured': [c for c in p.chart.castles(3) if c not in ('ボス', 'アルマムーン')],
            'recall': {'stage': 'dest', 'steps': 0,
                       'origin': list(p.chart.castles(3)['パタゴーヌ'])}}


def test_stationary_picker_cursor_tries_the_other_axis_then_gives_up(monkeypatch):
    # g1075 15:10:55-15:14:46: aiming at パタゴーヌ, the ring stayed on
    # アルマムーン through 54 left presses; one down press reached グリン.
    mem = chapter3_memory()
    chapter3_at(monkeypatch, 'アルマムーン')
    presses = [p.world_map_step(VIEW, mem, picker()) for _ in range(p.PICKER_STILL_LIMIT)]
    assert mem['recall']['goal'] == 'パタゴーヌ'
    assert presses == ([[p.pad('left')]] * p.PICKER_AXIS_SWITCH
                       + [[p.pad('down')]] * (p.PICKER_STILL_LIMIT - p.PICKER_AXIS_SWITCH))
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('b')]
    assert not mem.get('recall') and mem['uncertain'] and mem['recall_skip'] == {'tick': 100}
    steps = [r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_step']
    assert [r['observed_metric']['still'] for r in steps] == list(range(p.PICKER_STILL_LIMIT + 1))
    assert mem['_records'][-1]['decision'] == 'camp_recall_aborted'
    assert mem['_records'][-1]['resulting_event'] == 'picker_cursor_stationary'


def test_moved_picker_cursor_returns_to_the_main_axis(monkeypatch):
    mem = chapter3_memory()
    chapter3_at(monkeypatch, 'アルマムーン')
    for _ in range(p.PICKER_AXIS_SWITCH):
        p.world_map_step(VIEW, mem, picker())
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('down')]
    chapter3_at(monkeypatch, 'グリン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert mem['recall']['picker_still'] == 0


def test_moving_picker_cursor_keeps_pressing(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    x, y = p.chart.castles(1)['アルマムーン']; ox, oy = p.WORLD_MAP_OFFSET[1]
    at(monkeypatch, 'アルマムーン')
    for step in range(p.PICKER_STILL_LIMIT * 2):
        monkeypatch.setattr(p, 'world_cursor', lambda _, s=step: (x / 8 + ox - s % 2, y / 8 + oy))
        assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert mem['recall']['picker_still'] == 0


def test_unknown_origin_does_not_borrow_old_integrated_cursor_or_picker_without_marker(monkeypatch):
    mem = memory(); mem['cursor'] = list(p.chart.castles(1)['アルマムーン'])
    mem['uncertain'] = False
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_markers', lambda _: [])
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('right')]
    assert 'goal' not in mem['recall']
    assert 'origin' not in mem['recall']


def unit_box(castle):
    # A ▲ box centred on the castle's own picker cell (chart cell / 8 + offset).
    x, y = p.chart.castles(1)[castle]; ox, oy = p.WORLD_MAP_OFFSET[1]
    return (round(x / 8 + ox) - 3, round(y / 8 + oy) - 3, round(x / 8 + ox) + 3, round(y / 8 + oy) + 3)


def test_unique_picker_marker_binds_origin_and_selects_nearest_castle(monkeypatch):
    mem = memory(); mem['garrison'] = {'キカンドン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_markers', lambda _: [unit_box('ジョンリギ')])
    actions = p.world_map_step(VIEW, mem, picker())
    assert not any(a.get('buttons') == ['a'] for a in actions)
    assert mem['recall']['origin_source'] == 'picker'
    assert mem['recall']['goal'] == 'ジョンリギ'
    record = [r for r in mem['_records'] if r['decision'] == 'camp_recall_destination'][-1]
    assert record['observed_metric']['origin_source'] == 'picker'
    assert record['observed_metric']['castle'] == 'ジョンリギ'


def test_ambiguous_picker_markers_leave_origin_unknown(monkeypatch):
    mem = memory()
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_markers', lambda _: [unit_box('ジョンリギ'), unit_box('ゴーメン')])
    p.world_map_step(VIEW, mem, picker())
    assert 'origin' not in mem['recall']
    assert 'origin_source' not in mem['recall']


def test_origin_is_observed_from_current_roof_constellation(monkeypatch):
    mem = memory(); state = mem['recall']; cells = p.chart.castles(1)
    cam = (450, 500)
    roofs = [{'target': (cells[c][0] - cam[0], cells[c][1] - cam[1]),
              'kind': 'own', 'clipped': False} for c in ('キカンドン', 'ジョンリギ', 'ナキューメラ')]
    monkeypatch.setattr(p, 'castle_roofs', lambda _: roofs)
    p._observe_recall_origin(Screen([], None, '', kind='map', cursor=(117, 225)), mem, picker(), state)
    assert state['origin'] == [567, 725]
    assert p._recall_goal(mem) == 'キカンドン'
    monkeypatch.setattr(p, 'castle_roofs', lambda _: roofs[:1])
    p._observe_recall_origin(Screen([], None, '', kind='map', cursor=(26, 66)), mem, picker(), state)
    assert 'origin' not in state


def test_native_r_home_fixture_only_owned_castle_preserves_dispatch():
    frame = picker(); mem = {'chapter': 2, 'tick': 100, '_records': [],
                              'recall': {'stage': 'dest', 'steps': 0}}
    before = deepcopy(mem)
    assert p.world_map_step(VIEW, mem, frame) == [p.pad('a')]
    assert mem['recall']['confirm_after_zoom']
    assert mem['recall']['goal'] == 'アルマムーン'
    assert before['recall']['stage'] == 'dest'


def test_native_home_ring_with_a_second_own_flag_moves_towards_nearby_castle():
    # Controlled ownership variant of the isolated R-picker fixture; not
    # reported as a natural-run frame. Keep the real ring detector active.
    frame = picker(); rgb = bytearray(frame.rgb)
    cell = p.chart.castles(2)['フーリック']; ox, oy = p.WORLD_MAP_OFFSET[2]
    mx, my = round(cell[0] / 8 + ox), round(cell[1] / 8 + oy)
    for y in range(my - 2, my + 5):
        for x in range(mx - 2, mx + 6):
            pos = (y * frame.width + x) * 3
            rgb[pos:pos + 3] = bytes(p.WORLD_FLAG_OWN)
    frame = Frame(frame.width, frame.height, bytes(rgb))
    mem = {'chapter': 2, 'tick': 100, '_records': [],
           'recall': {'stage': 'dest', 'steps': 0, 'origin': list(cell)}}
    assert p.world_flags(frame, 2)['フーリック'] == 'own'
    assert p.world_map_step(VIEW, mem, frame) == [p.pad('left')]
    assert mem['recall']['goal'] == 'フーリック'
