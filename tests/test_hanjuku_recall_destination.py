"""The R picker starts at home, which is not the unit's origin."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen
from docich.hanjuku_pixels import Frame
from test_hanjuku_camp_receipts import picker
from docich.hanjuku.state.regions import world as _world, flow as _flow, journal as _journal

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
    assert _flow(mem)['recall']['goal'] == 'キカンドン'
    assert not any(r['decision'] == 'camp_recall_requested' for r in mem['_records'])
    at(monkeypatch, 'キカンドン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    assert _flow(mem)['recall']['goal'] == 'キカンドン'


def test_unknown_origin_can_join_observed_hero_castle(monkeypatch):
    mem = memory(); _world(mem)['garrison'] = {'ゴーメン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert _flow(mem)['recall']['goal'] == 'ゴーメン'


def test_unknown_origin_prefers_sortie_source_over_hero_castle():
    # issue #2907: origin が束縛できなくても、帰還する本人の出撃元を代理の
    # 現在地として距離判定へ使い、主人公の在城城へ一律固定しない。
    mem = memory()
    _world(mem)['garrison'] = {'アルマムーン': [p.NAME]}
    _flow(mem)['recall']['sorties'] = ['s1']
    mem['sorties'] = {'s1': {'source': 'ジョンリギ'}}
    assert p._recall_goal(mem) == 'ジョンリギ'


def test_captured_sortie_source_uses_nearest_owned_castle():
    # 出撃元が奪取されて自軍城でなくても、その座標を代理 origin として
    # 最も近い自軍城へ帰還する（けっかい(265,270) → ゴーメン(295,606)）。
    mem = memory()
    _world(mem)['garrison'] = {'アルマムーン': [p.NAME]}
    _flow(mem)['recall']['sorties'] = ['s1']
    mem['sorties'] = {'s1': {'source': 'けっかい'}}
    assert p._recall_goal(mem) == 'ゴーメン'


def test_distance_tie_prefers_hero_and_nearby_castle_beats_far_hero():
    mem = memory([649, 765]); _world(mem)['garrison'] = {'キカンドン': [p.NAME]}
    assert p._recall_goal(mem) == 'キカンドン'
    _flow(mem)['recall']['origin'] = list(p.chart.castles(1)['ジョンリギ'])
    assert p._recall_goal(mem) == 'ジョンリギ'


def test_diagonal_distance_selects_the_closest_castle_and_confirms_that_picker(monkeypatch):
    import json
    # SFC units move straight to their destination. Manhattan distance chose
    # ゴーメン (309) over キカンドン (312), although the direct squared
    # distances are respectively60381 and52034. This origin is synthetic.
    mem = memory([370, 840]); _world(mem)['garrison'] = {'ゴーメン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert _flow(mem)['recall']['goal'] == 'キカンドン'
    mem = json.loads(json.dumps(mem))
    at(monkeypatch, 'キカンドン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    assert _flow(mem)['recall']['goal'] == 'キカンドン'
    assert not _flow(mem)['recall'].get('input_sent')  # planned A is not a sent receipt


@pytest.mark.parametrize('change', ['marching', 'unknown', 'duplicate', 'enemy'])
def test_unconfirmed_hero_is_not_used_as_a_merge_target(change):
    mem = memory(); _world(mem)['garrison'] = {'ゴーメン': [p.NAME]}
    flags = {c: 'own' for c in p._owned(mem)}
    if change == 'marching':
        mem['sorties'] = {'x': {'general': p.NAME, 'status': 'en_route', 'tick': 99}}
    elif change == 'unknown': _world(mem)['general_location_unknown'] = [p.NAME]
    elif change == 'duplicate': _world(mem)['garrison']['キカンドン'] = [p.NAME]
    else: flags['ゴーメン'] = 'enemy'
    assert p._recall_goal(mem, flags) is None


def test_enemy_nearest_castle_is_excluded_and_selection_updates_after_loss(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    _flow(mem)['recall']['goal'] = 'キカンドン'
    at(monkeypatch, 'アルマムーン', {'アルマムーン': 'own', 'キカンドン': 'enemy', 'ジョンリギ': 'own'})
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('up')]
    assert _flow(mem)['recall']['goal'] == 'ジョンリギ'


def test_hotloaded_old_home_goal_is_replanned_before_a(monkeypatch):
    mem = memory(list(p.chart.castles(1)['ゴーメン']))
    _flow(mem)['recall'].update(goal='アルマムーン', destination_selected=True)
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert _flow(mem)['recall']['goal'] == 'ゴーメン'


def test_dispatch_receipt_cannot_be_retargeted(monkeypatch):
    mem = memory(list(p.chart.castles(1)['ゴーメン']))
    _flow(mem)['recall'].update(stage='await_dispatch', goal='アルマムーン', a_inputs=2)
    at(monkeypatch, 'アルマムーン')
    assert p.world_map_step(VIEW, mem, picker()) == []
    assert _flow(mem)['recall']['goal'] == 'アルマムーン'


def test_picker_stall_has_a_finite_budget(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(p.RECALL_LIMIT - 1):
        assert not any(a.get('buttons') == ['a'] for a in p.world_map_step(VIEW, mem, picker()))
    # Owner 2026-11-05: the budget no longer cancels, it confirms where the ring is.
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    assert _flow(mem)['recall']['picker_forced'] is True


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


def test_stationary_picker_cursor_tries_the_other_axis_then_confirms_where_it_stands(monkeypatch):
    # g1075 15:10:55-15:14:46: aiming at パタゴーヌ, the ring stayed on
    # アルマムーン through 54 left presses; one down press reached グリン.
    mem = chapter3_memory()
    chapter3_at(monkeypatch, 'アルマムーン')
    presses = [p.world_map_step(VIEW, mem, picker()) for _ in range(p.PICKER_STILL_LIMIT)]
    assert _flow(mem)['recall']['goal'] == 'パタゴーヌ'
    assert presses == ([[p.pad('left')]] * p.PICKER_AXIS_SWITCH
                       + [[p.pad('down')]] * (p.PICKER_STILL_LIMIT - p.PICKER_AXIS_SWITCH))
    # Owner 2026-11-05: a ring that will not move is confirmed on the castle it
    # stands on (アルマムーン, ours) instead of cancelling the recall.
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    recall = _flow(mem)['recall']
    assert recall['stage'] == 'await_dispatch' and recall['goal'] == 'アルマムーン'
    steps = [r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_step']
    assert [r['observed_metric']['still'] for r in steps] == list(range(p.PICKER_STILL_LIMIT + 1))
    assert mem['_records'][-1]['decision'] == 'camp_recall_requested'
    assert mem['_records'][-2]['decision'] == 'camp_recall_picker_fallback'
    # The destination is read after the recall's walking time (v774).
    assert mem['roster_read_due'] == {'アルマムーン': 100 + p.RECALL_READ_DELAY}


def test_moved_picker_cursor_returns_to_the_main_axis(monkeypatch):
    mem = chapter3_memory()
    chapter3_at(monkeypatch, 'アルマムーン')
    for _ in range(p.PICKER_AXIS_SWITCH):
        p.world_map_step(VIEW, mem, picker())
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('down')]
    chapter3_at(monkeypatch, 'グリン')
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert _flow(mem)['recall']['picker_still'] == 0


def test_moving_picker_cursor_keeps_pressing(monkeypatch):
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    x, y = p.chart.castles(1)['アルマムーン']; ox, oy = p.WORLD_MAP_OFFSET[1]
    at(monkeypatch, 'アルマムーン')
    for step in range(p.PICKER_STILL_LIMIT * 2):
        monkeypatch.setattr(p, 'world_cursor', lambda _, s=step: (x / 8 + ox - s % 2, y / 8 + oy))
        assert p.world_map_step(VIEW, mem, picker()) == [p.pad('left')]
    assert _flow(mem)['recall']['picker_still'] == 0


def test_unknown_origin_does_not_borrow_old_integrated_cursor_or_picker_without_marker(monkeypatch):
    mem = memory(); mem['cursor'] = list(p.chart.castles(1)['アルマムーン'])
    mem['uncertain'] = False
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_markers', lambda _: [])
    assert p.world_map_step(VIEW, mem, picker()) == [p.pad('right')]
    assert 'goal' not in _flow(mem)['recall']
    assert 'origin' not in _flow(mem)['recall']


def unit_box(castle):
    # A ▲ box centred on the castle's own picker cell (chart cell / 8 + offset).
    x, y = p.chart.castles(1)[castle]; ox, oy = p.WORLD_MAP_OFFSET[1]
    return (round(x / 8 + ox) - 3, round(y / 8 + oy) - 3, round(x / 8 + ox) + 3, round(y / 8 + oy) + 3)


def test_unique_picker_marker_binds_origin_and_selects_nearest_castle(monkeypatch):
    mem = memory(); _world(mem)['garrison'] = {'キカンドン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_markers', lambda _: [unit_box('ジョンリギ')])
    actions = p.world_map_step(VIEW, mem, picker())
    assert not any(a.get('buttons') == ['a'] for a in actions)
    assert _flow(mem)['recall']['origin_source'] == 'picker'
    assert _flow(mem)['recall']['goal'] == 'ジョンリギ'
    record = [r for r in mem['_records'] if r['decision'] == 'camp_recall_destination'][-1]
    assert record['observed_metric']['origin_source'] == 'picker'
    assert record['observed_metric']['castle'] == 'ジョンリギ'


def test_ambiguous_picker_markers_leave_origin_unknown(monkeypatch):
    mem = memory()
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_markers', lambda _: [unit_box('ジョンリギ'), unit_box('ゴーメン')])
    p.world_map_step(VIEW, mem, picker())
    assert 'origin' not in _flow(mem)['recall']
    assert 'origin_source' not in _flow(mem)['recall']


def test_origin_is_observed_from_current_roof_constellation(monkeypatch):
    mem = memory(); state = _flow(mem)['recall']; cells = p.chart.castles(1)
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
    assert p.world_map_step(VIEW, mem, frame) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert _flow(mem)['recall']['goal'] == 'アルマムーン'
    assert before['recall']['stage'] == 'dest'  # pre-migration snapshot stays flat


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
    assert _flow(mem)['recall']['goal'] == 'フーリック'


def test_a_blocked_axis_alternates_when_the_other_offset_is_small(monkeypatch):
    """g1133 01:06-01:33: バビラリー→ダーラント (dx 38, dy -1.5) pressed right
    8 times without moving; the old magnitude guard never tried up, so every
    recall fell back to バビラリー."""
    mem = {'chapter': 6, 'tick': 100, '_records': [],
           'captured': ['アルマムーン', 'バビラリー', 'ダーラント'],
           'recall': {'stage': 'dest', 'steps': 0}}
    x, y = p.chart.castles(6)['バビラリー']; ox, oy = p.WORLD_MAP_OFFSET[6]
    monkeypatch.setattr(p, 'world_cursor', lambda _: (x / 8 + ox, y / 8 + oy))
    monkeypatch.setattr(p, 'world_flags', lambda *_: {c: 'own' for c in p.chart.castles(6)})
    monkeypatch.setattr(p, 'world_markers', lambda _: [])
    monkeypatch.setattr(p, '_recall_goal', lambda *_: 'ダーラント')
    presses = [p.world_map_step(VIEW, mem, picker()) for _ in range(p.PICKER_STILL_LIMIT)]
    assert presses == ([[p.pad('right')]] * p.PICKER_AXIS_SWITCH
                       + [[p.pad('up')]] * p.PICKER_AXIS_SWITCH
                       + [[p.pad('right')]] * p.PICKER_AXIS_SWITCH
                       + [[p.pad('up')]] * p.PICKER_AXIS_SWITCH)


def test_a_picker_ring_cycling_between_two_castles_gives_up(monkeypatch):
    """g1133 02:13-02:22: right and left moved the ring between バビラリー
    (73, 120.5) and オケアナ (175, 90.5) forever, so the unchanged-cursor
    budget never fired and the recall never completed."""
    mem = {'chapter': 6, 'tick': 100, '_records': [],
           'captured': ['アルマムーン', 'バビラリー', 'ダーラント'],
           'recall': {'stage': 'dest', 'steps': 0}}
    positions = [(73.0, 120.5), (175.0, 90.5)]
    seen = {'i': 0}

    def cursor(_):
        spot = positions[seen['i'] % 2]
        seen['i'] += 1
        return spot

    monkeypatch.setattr(p, 'world_cursor', cursor)
    monkeypatch.setattr(p, 'world_flags', lambda *_: {c: 'own' for c in p.chart.castles(6)})
    monkeypatch.setattr(p, 'world_markers', lambda _: [])
    monkeypatch.setattr(p, '_recall_goal', lambda *_: 'ダーラント')
    actions = [p.world_map_step(VIEW, mem, picker())
               for _ in range(p.PICKER_STILL_LIMIT + 2)]
    assert any(a == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')] for a in actions)
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    steps = [r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_step']
    assert steps[-1]['observed_metric']['loops'] >= p.PICKER_LOOP_LIMIT


def test_a_picker_ring_that_stops_at_each_castle_still_gives_up(monkeypatch):
    """g1133 02:43: the ring alternated バビラリー and オケアナ with two
    unchanged readings at each stop, so a sliding window of changed positions
    never accumulated and the loop budget never fired."""
    mem = {'chapter': 6, 'tick': 100, '_records': [],
           'captured': ['アルマムーン', 'バビラリー', 'ダーラント'],
           'recall': {'stage': 'dest', 'steps': 0}}
    stops = [(73.0, 120.5)] * 3 + [(175.0, 90.5)] * 3
    seen = {'i': 0}

    def cursor(_):
        spot = stops[seen['i'] % len(stops)]
        seen['i'] += 1
        return spot

    monkeypatch.setattr(p, 'world_cursor', cursor)
    monkeypatch.setattr(p, 'world_flags', lambda *_: {c: 'own' for c in p.chart.castles(6)})
    monkeypatch.setattr(p, 'world_markers', lambda _: [])
    monkeypatch.setattr(p, '_recall_goal', lambda *_: 'ダーラント')
    actions = [p.world_map_step(VIEW, mem, picker())
               for _ in range(p.PICKER_STILL_LIMIT + 6)]
    assert any(a == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')] for a in actions)
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    steps = [r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_step']
    assert steps[-1]['observed_metric']['loops'] >= p.PICKER_LOOP_LIMIT


def test_the_picker_records_its_progress_every_ten_observations(monkeypatch):
    """g1163 2026-11-14 07:13: the dest picker ran 31 observations unseen."""
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(10):
        p.world_map_step(VIEW, mem, picker())
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_progress')
    assert record['observed_metric']['observations'] == 10
    assert record['observed_metric']['cursor'] is None


def test_the_picker_progress_records_the_own_castles(monkeypatch):
    """g1163 2026-11-14 08:05: goal null for 30+ observations; which own castles?"""
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(10):
        p.world_map_step(VIEW, mem, picker())
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_progress')
    assert 'アルマムーン' in record['observed_metric']['own']


def test_the_picker_fallback_records_every_nearby_castle(monkeypatch):
    """The fallback's destination choice needs the whole nearby list."""
    mem = memory(list(p.chart.castles(1)['キカンドン']))
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(p.RECALL_LIMIT - 1):
        p.world_map_step(VIEW, mem, picker())
    p.world_map_step(VIEW, mem, picker())
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_fallback')
    assert 'nearby' in record['observed_metric']


def test_the_picker_progress_records_the_hero_castle(monkeypatch):
    """g1163 2026-11-14 08:59: goal null; was the hero's location known?"""
    mem = memory(); _world(mem)['garrison'] = {'アルマムーン': [p.NAME]}
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(10):
        p.world_map_step(VIEW, mem, picker())
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_progress')
    assert record['observed_metric']['hero'] == ['アルマムーン']


def test_a_status_location_locates_the_hero_for_the_goal():
    """g1163 2026-11-14 08:59: the hero stood in an own castle but the goal was null."""
    mem = memory()
    _world(mem)['garrison'] = {}
    mem['general_statuses'] = {'statuses': {p.NAME: {'location': 'キカンドン',
                                                     'location_observed': True}}}
    flags = {c: 'own' for c in p._owned(mem)}
    assert p._recall_goal(mem, flags) == 'キカンドン'


def test_the_picker_progress_records_the_hero_status_location(monkeypatch):
    """g1163 2026-11-14 08:59: goal null; the status location was the other source."""
    mem = memory()
    mem['general_statuses'] = {'statuses': {p.NAME: {'location': 'キカンドン',
                                                     'location_observed': True}}}
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(10):
        p.world_map_step(VIEW, mem, picker())
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_picker_progress')
    assert record['observed_metric']['hero_status'] == {'location': 'キカンドン', 'observed': True}


def test_a_goal_less_picker_schedules_the_capital_roster_read(monkeypatch):
    """g1163 2026-11-14 08:59: statuses null in ch.9; read the capital's list."""
    mem = memory()
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    p.world_map_step(VIEW, mem, picker())
    home = p.chart.home_castle(1)
    assert mem['roster_read_due'][home] == 100 + 180


def test_the_picker_fallback_names_the_capital_when_no_goal(monkeypatch):
    """g1163 2026-11-14 10:12: goal null and nearby [] left the recall unnamed."""
    mem = memory()
    at(monkeypatch, 'アルマムーン')
    monkeypatch.setattr(p, 'world_cursor', lambda _: None)
    for _ in range(p.RECALL_LIMIT - 1):
        p.world_map_step(VIEW, mem, picker())
    p.world_map_step(VIEW, mem, picker())
    assert _flow(mem)['recall']['goal'] == p.chart.home_castle(1)
