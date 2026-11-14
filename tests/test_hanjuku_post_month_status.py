"""Post-month roster-first policy: sequence ordering and conservative evidence."""
import json
from unittest.mock import patch

from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.os_layer import roster as receipts
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen, parse

from test_hanjuku_house import Canvas, roster, status
from docich.hanjuku.state.regions import world as _world, plan as _plan_region


def memory(**fields):
    return {'chapter': 1, 'month': '2-6', 'tick': 100, 'gold': 250,
            '_records': [], **fields}


def test_month_exit_arms_one_scoped_roster_first_owner():
    mem = memory()
    post.arm(mem)
    assert mem['post_month_status']['stage'] == 'await_map'
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert mem['post_month_status']['stage'] == 'open_menu'
    previous = mem['post_month_status']
    post.arm(mem)
    assert mem['post_month_status'] is previous


def test_individual_statuses_are_read_before_any_repair_or_recall():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    second = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(first, mem, None) == []  # settle highlighted sprite
    assert post.step(first, mem, None) == [pad('a')]
    assert post.step(parse(status('どうし', egg='エラベルエッグ4')), mem, None) == [pad('b')]
    assert post.step(first, mem, None) == [pad('down')]
    assert post.step(second, mem, None) == []
    assert post.step(second, mem, None) == [pad('a')]
    assert post.step(parse(status('ゼウス', egg='こわれている')), mem, None) == [pad('b')]
    assert post.step(second, mem, None) == [pad('down')]
    # Short final roster page is a visible last-page proof.
    assert post.step(second, mem, None) == [pad('b')]
    assert mem['general_statuses']['complete'] is True
    assert list(mem['general_statuses']['statuses']) == ['どうし', 'ゼウス']
    assert state['tasks'] == [{'kind': 'locate', 'name': 'どうし'},
                              {'kind': 'locate', 'name': 'ゼウス'},
                              {'kind': 'repair', 'name': 'ゼウス'}]
    assert state['stage'] == 'process'


def _status_with_location(text):
    c = Canvas()
    c.text(24, 151, text)
    return parse(c.frame())


def test_exact_native_location_lines_remain_distinct():
    for text, expected in [
        ('キャンプしています', 'camp'),
        ('しろのなかにいます', 'castle'),
        ('いまこちらにむかっています', 'march'),
        ('しんぐんちゅうです', 'march'),
    ]:
        assert post._location(_status_with_location(text)) == expected
    assert post._location(_status_with_location('????')) == 'unknown'


def test_roster_triangle_needs_a_unique_fresh_castle_position():
    from docich import hanjuku_chart as chart
    castle, cell = next(iter(chart.castles(1).items()))
    mem = memory(cursor=list(cell), uncertain=False,
                 nav_last={'kind': 'map', 'screen': [63, 53], 'tick': 99})
    with patch.object(post, 'world_markers', return_value=[(60, 50, 66, 56)]):
        result = post._marker(mem, object())
        assert result['castle_candidate'] == castle
        mem['uncertain'] = True
        assert 'castle_candidate' not in post._marker(mem, object())


def test_known_garrison_does_not_need_redundant_select():
    mem = memory(garrison={'アルマムーン': ['ゼウス']})
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='status', selected='ゼウス')
    assert post.step(parse(status('ゼウス', egg='こわれている')), mem, None) == [pad('b')]
    assert state['statuses']['ゼウス']['castle'] == 'アルマムーン'
    assert state['statuses']['ゼウス']['castle_evidence'] == 'previous_named_roster'


def test_castle_location_is_confirmed_by_native_status_name():
    mem = memory(garrison={'グリン': ['ゼウス']})
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    with patch.object(post.map_registration, '_label', return_value='アルマムーン'):
        assert post.step(Screen([], None, '', kind='castle_info'), mem, None) == [pad('b')]
    assert _world(mem)['garrison']['アルマムーン'] == ['ゼウス']
    assert 'ゼウス' not in _world(mem)['garrison']['グリン']


def test_repair_opens_castle_status_even_when_cursor_hides_roof():
    # g1153 2026-11-07 00:29 and g1163 2026-11-09 13:58: after the roster
    # SELECT centred the cursor on the broken general, _roof_under_cursor was
    # False (the cursor brackets covered the roof) and the task finished with
    # repair_source_unconfirmed without ever opening the castle status.
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'repair', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle', 'broken': True,
                                     'castle': 'アルマムーン'}})
    # No roof gate: the cursor can hide the roof, and the house FSM verifies
    # the castle by opening ステータス itself.
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('a')]
    assert state['stage'] == 'wait_repair'
    assert mem['house']['phase'] == 'castle_verify'
    assert mem['house']['general'] == 'ゼウス'
    assert mem['house']['source'] == 'アルマムーン'


def test_repair_with_unknown_source_does_not_open_a_castle():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'repair', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle', 'broken': True}})
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert state['stage'] == 'process'
    assert 'house' not in mem


def test_repair_select_lost_in_roster_resends_select_before_opening():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'repair', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle', 'broken': True,
                                     'castle': 'アルマムーン'}},
                 phase_reads=3)
    still_roster = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(still_roster, mem, None) == [pad('select')]
    assert state['stage'] == 'task_jump'
    assert 'house' not in mem


def test_unknown_status_never_allows_planning_and_releases_the_field():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='status', selected='ゼウス', phase_reads=post.STATUS_RETRIES)
    assert post.step(Screen([], None, '', kind='unknown'), mem, None) == [pad('b')]
    assert state['stage'] == 'blocked'
    # The failed read must not hold the map: record it and hand control back.
    assert post.step(Screen([], None, '', kind='map'), mem, None) is None
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['failed'] == 'status_unreadable'
    assert mem['post_month_status_last']['complete'] is False
    assert 'general_statuses' not in mem


def test_scripted_dialogue_releases_the_monthly_owner():
    # g1133 2026-11-06 02:56: the new-partner ceremony arrived while a locate
    # task was navigating. Holding [] at task_menu starved the ordinary A
    # handler and every later observation still read 'task_menu'.
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_menu', job={'kind': 'locate', 'name': 'ゼウス'}, tasks=[])
    dialogue = Screen([], None, 'それでは あたらしい あいぼう を よんでみましょう。',
                      kind='text')
    assert post.step(dialogue, mem, None) is None
    # A menu stage still owns its own map/menu frames.
    assert post.step(Screen([], None, '', kind='main_menu'), mem, None) == []


def test_select_locate_opens_castle_even_when_roof_is_hidden_by_cursor():
    """SELECT's named position is evidence; roof recognition can be occluded."""
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('a')]
    assert state['stage'] == 'locate_open'
    assert not _world(mem).get('general_location_unknown')
    assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == []
    assert state['stage'] == 'locate_info'
    menu = Screen([], (40, 184), 'ステータス', kind='castle_menu')
    with patch.object(post, 'menu_to', return_value='here'):
        assert post.step(menu, mem, None) == [pad('a')]
    with patch.object(post.map_registration, '_label', return_value='アルマムーン'):
        assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == [pad('b')]
    assert state['stage'] == 'process'
    assert _world(mem)['garrison']['アルマムーン'] == ['ゼウス']
    assert state['statuses']['ゼウス']['castle_evidence'] == 'native_castle_name'


def native_castle_panel(name='アルマムーン'):
    """The native 城ステータス panel keeps the しゅつげき/ステータス menu drawn."""
    c = Canvas()
    c.text(24, 31, 'しゅつげき')
    c.text(24, 47, 'ステータス')
    c.text(120, 31, name)
    c.text(120 + 8 * len(name), 31, 'じょう')
    c.text(120, 47, 'しゅうにゅう')
    c.text(176, 47, '22')
    c.text(200, 47, 'G')
    c.text(120, 63, 'レベル')
    c.text(176, 63, '1')
    c.text(120, 79, 'しょうぐん')
    c.text(176, 79, '2')
    c.text(200, 79, 'めい')
    c.text(120, 95, 'ゼウス')
    return parse(c.frame())


def test_native_status_panel_classified_as_menu_still_confirms_location():
    # g1133 2026-11-06: the panel is classified castle_menu because the menu
    # stays behind it, so a castle_info-only gate never read the castle name
    # and every locate task failed until the retry budget ran out.
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    panel = native_castle_panel()
    assert panel.kind == 'castle_menu'
    assert post.step(panel, mem, None) == [pad('b')]
    assert state['stage'] == 'process'
    assert _world(mem)['garrison']['アルマムーン'] == ['ゼウス']
    assert state['statuses']['ゼウス']['castle_evidence'] == 'native_castle_name'
    assert not _world(mem).get('general_location_unknown')


def test_named_castle_whose_ownership_is_stale_is_remembered_once():
    # g1139 2026-11-07 00:03-00:08: ポワソン took ジュメルズ, the monthly roster
    # read him as しろのなかにいます with the triangle on that castle, but the
    # Y map still listed ジュメルズ as enemy. _assign rejected the measured
    # name, the locate task read the same name from the castle status panel,
    # and still waited out STATUS_RETRIES three times, logging
    # castle_status_not_read (the name had in fact been read each time).
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    panel = native_castle_panel('スペンソニア')
    assert panel.kind == 'castle_menu'
    assert post.step(panel, mem, None) == [pad('b')]
    assert state['stage'] == 'process'
    assert _world(mem)['castle_location_pending']['ゼウス']['castle'] == 'スペンソニア'
    assert state['statuses']['ゼウス']['castle'] == 'スペンソニア'
    assert state['statuses']['ゼウス']['castle_pending'] is True
    # The planner still must not use a castle we do not own yet.
    assert 'スペンソニア' not in _world(mem).get('garrison', {})
    assert _world(mem)['general_location_unknown'] == ['ゼウス']
    assert any(r['decision'] == 'post_month_castle_pending_ownership'
               for r in mem['_records'])
    assert any(r.get('resulting_event') == 'castle_named_unowned'
               for r in mem['_records'])


def test_pending_castle_does_not_queue_another_select_locate():
    pending = {'ゼウス': {'location': 'castle', 'castle': 'スペンソニア',
                          'castle_pending': True}}
    assert post._tasks(pending, ['ゼウス']) == []
    assert post._tasks({'ゼウス': {'location': 'castle'}}, ['ゼウス']) == [
        {'kind': 'locate', 'name': 'ゼウス'}]


def test_pending_castle_is_promoted_once_the_world_map_shows_it_ours():
    mem = memory(captured=['スペンソニア'])
    _world(mem)['castle_location_pending'] = {
        'ゼウス': {'castle': 'スペンソニア', 'chapter': 1, 'tick': 90,
                   'evidence': 'native_castle_name'}}
    _world(mem)['general_location_unknown'] = ['ゼウス']
    post.arm(mem)
    assert _world(mem)['garrison']['スペンソニア'] == ['ゼウス']
    assert _world(mem)['general_location_unknown'] == []
    assert 'ゼウス' not in _world(mem).get('castle_location_pending', {})


def test_roster_triangle_for_a_just_captured_castle_waits_for_ownership():
    mem = memory()
    post.arm(mem)
    _world(mem)['garrison'] = {'アルマムーン': ['どうし']}
    state = mem['post_month_status']
    state.update(stage='roster', top_confirmed=True)
    first = parse(roster(names=('どうし',), selected=0))
    mapped = {'source': 'selection_marker', 'castle_candidate': 'スペンソニア',
              'box': [98, 110, 104, 116]}
    with patch.object(post, '_marker', return_value=mapped):
        assert post.step(first, mem, first) == []      # settle the highlight
        assert post.step(first, mem, first) == [pad('a')]
    assert post.step(parse(status('どうし')), mem, None) == [pad('b')]
    row = state['statuses']['どうし']
    assert row['castle'] == 'スペンソニア' and row['castle_pending'] is True
    assert 'スペンソニア' not in _world(mem).get('garrison', {})
    # An older garrison entry is not his observed station any more.
    assert 'どうし' not in _world(mem)['garrison']['アルマムーン']
    assert any(r['decision'] == 'post_month_castle_pending_ownership'
               for r in mem['_records'])


def test_unread_castle_panel_records_the_screen_for_the_next_diagnosis():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}},
                 phase_reads=post.STATUS_RETRIES - 1)
    assert post.step(Screen([], None, 'よめないがめん', kind='castle_menu'),
                     mem, None) == [pad('b')]
    record = next(r for r in mem['_records']
                  if r['decision'] == 'post_month_castle_location_unverified')
    assert record['observed_metric']['text'] == 'よめないがめん'
    assert record['observed_metric']['screen'] == 'castle_menu'


def test_castle_menu_header_cannot_be_mistaken_for_verified_status():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    header = Screen([], None, 'しゅつげきステータス', kind='castle_menu')
    assert post.step(header, mem, None) == []
    assert state['stage'] == 'locate_info'
    assert 'アルマムーン' not in _world(mem).get('garrison', {})


def test_missing_castle_status_retries_same_general_before_advancing():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}},
                 phase_reads=post.STATUS_RETRIES - 1, locate_retries=0)
    assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == [pad('b')]
    assert state['stage'] == 'locate_reset'
    assert state['job']['name'] == 'ゼウス'
    assert state['locate_retries'] == 1
    assert not _world(mem).get('general_location_unknown')
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert state['stage'] == 'task_menu'
    assert state['job']['name'] == 'ゼウス'
    # Only after the bounded retry budget is exhausted can an unknown
    # general be excluded from planning; it is never attributed to a castle.
    state.update(stage='locate_info', phase_reads=post.STATUS_RETRIES - 1,
                 locate_retries=post.LOCATE_RETRIES)
    assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == [pad('b')]
    assert state['stage'] == 'process'
    assert _world(mem)['general_location_unknown'] == ['ゼウス']
    assert 'castle' not in state['statuses']['ゼウス']


def test_select_lost_in_roster_resends_select_before_location_failure():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}}, phase_reads=3)
    still_roster = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(still_roster, mem, None) == [pad('select')]
    assert state['stage'] == 'task_jump'


def _triangle(c, x, y):
    white, gray, black = (255, 254, 255), (90, 88, 90), (32, 32, 32)
    rows = ((0, (white,) * 7),
            (0, (gray,) + (white,) * 5 + (gray,)),
            (0, (gray,) * 2 + (white,) * 3 + (gray,) * 2),
            (0, (black,) + (gray,) * 2 + (white,) + (gray,) * 2 + (black,)),
            (1, (black,) + (gray,) * 3 + (black,)),
            (2, (black,) + (gray,) + (black,)),
            (3, (black,)))
    for row, (offset, colours) in enumerate(rows):
        for col, colour in enumerate(colours):
            c.put(x + offset + col, y + row, colour)


def _roster_map(target="キカンドン", *, flags=True):
    from docich import hanjuku_chart as chart
    c = Canvas()
    if flags:
        for x, y in chart.castles(1).values():
            fx, fy = round(x / 8 + 17), round(y / 8 + 30)
            for dy in range(4):
                for dx in range(6):
                    c.put(fx + dx, fy + dy, (230, 56, 90))
    x, y = chart.castles(1)[target]
    cx, cy = round(x / 8 + 17) + 2, round(y / 8 + 30) + 1
    box = (cx - 3, cy - 6, cx + 3, cy)
    _triangle(c, box[0], box[1])
    # A red Y-view army marker elsewhere must never become the selection.
    for dy in range(6):
        for dx in range(7):
            c.put(30 + dx, 30 + dy, (255, 24, 0))
    return c.frame(), box



def test_roster_waits_for_transient_marker_then_records_castle_without_select():
    mem = memory(captured=['キカンドン'])
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし',), selected=0))
    frame, _ = _roster_map()
    mapped = {'source': 'selection_marker', 'castle_candidate': 'キカンドン',
              'box': [98, 110, 104, 116]}
    with patch.object(post, '_marker', side_effect=[None, None, mapped]):
        assert post.step(first, mem, frame) == []  # settle highlight
        assert post.step(first, mem, frame) == []  # triangle temporarily missing
        assert post.step(first, mem, frame) == []  # still missing
        assert post.step(first, mem, frame) == [pad('a')]  # candidate recovered
    assert state['stage'] == 'status'
    assert post.step(parse(status('どうし')), mem, None) == [pad('b')]
    assert state['statuses']['どうし']['castle'] == 'キカンドン'
    assert state['statuses']['どうし']['castle_evidence'] == 'roster_triangle'
    assert _world(mem)['garrison']['キカンドン'] == ['どうし']
    assert state['tasks'] == []


def test_roster_marker_missing_is_bounded_and_never_guesses_a_castle():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし',), selected=0))
    frame, _ = _roster_map(flags=False)
    with patch.object(post, '_marker', return_value=None) as marker:
        assert post.step(first, mem, frame) == []  # highlight
        for _ in range(post.MARKER_RETRIES - 1):
            assert post.step(first, mem, frame) == []
        assert post.step(first, mem, frame) == [pad('a')]
        assert marker.call_count == post.MARKER_RETRIES
    assert state['markers']['どうし'] is None
    assert post.step(parse(status('どうし')), mem, None) == [pad('b')]
    assert 'castle' not in state['statuses']['どうし']


def test_roster_marker_without_flag_gets_another_observation():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし',), selected=0))
    frame, _ = _roster_map(flags=False)
    with patch.object(post, '_marker', return_value={'source': 'selection_marker',
                                                     'box': [20, 20, 26, 26]}):
        assert post.step(first, mem, frame) == []
        assert post.step(first, mem, frame) == []  # marker seen, castle not mapped
    assert state['stage'] == 'roster'


def test_measured_selection_triangle_overrides_the_red_army_marker():
    frame, box = _roster_map()
    result = post._marker(memory(chapter=1), frame)
    assert result["source"] == "selection_marker"
    assert tuple(result["box"]) == box
    assert result["castle_candidate"] == "キカンドン"



def test_selection_triangle_accepts_neutral_palette_brightness_drift_only():
    from docich.hanjuku.os_layer import readers
    palette = {
        readers.GENERAL_MARKER_WHITE: (236, 235, 236),
        readers.GENERAL_MARKER_GRAY: (106, 105, 107),
        readers.GENERAL_MARKER_BLACK: (48, 47, 49),
    }
    c = Canvas()
    for dx, dy, colour in readers._GENERAL_MARKER_PATTERN:
        c.put(55 + dx, 62 + dy, palette[colour])
    assert readers.selection_marker(c.frame())['box'] == (55, 62, 61, 68)

    # A tinted/coloured shape is not the white-grey selected-general marker.
    coloured = Canvas()
    for dx, dy, colour in readers._GENERAL_MARKER_PATTERN:
        r, g, b = palette[colour]
        coloured.put(55 + dx, 62 + dy, (min(r + 50, 255), g, b))
    assert readers.selection_marker(coloured.frame()) is None


def test_selection_triangle_without_castle_flags_has_no_candidate():
    frame, box = _roster_map(flags=False)
    result = post._marker(memory(chapter=1), frame)
    assert result["source"] == "selection_marker"
    assert tuple(result["box"]) == box
    assert "castle_candidate" not in result
    assert result["icons_seen"] == 0



def _known_status(name):
    return {'general': name, 'location': 'castle', 'castle': 'アルマムーン',
            'broken': False, 'hp': 50, 'max_hp': 50, 'egg': 'エラベルエッグ',
            'uses': 4}


def _persistent_memory(*names, month='1-7', **changes):
    cache = {'scope': [1, '1-6'], 'identity': None, 'complete': True,
             'statuses': {name: _known_status(name) for name in names},
             'dirty': {}, 'pending_joins': []}
    return memory(month=month, general_statuses=cache,
                  garrison={'アルマムーン': list(names)}, **changes)


def test_unchanged_general_status_survives_many_months_without_opening_menu():
    mem = _persistent_memory('どうし', 'ゼウス', month='7-9')
    mem['general_statuses']['tick'] = 1
    mem['country_general_count'] = {'scope': [1, '7-9'], 'identity': None,
                                    'count': 2}
    post.arm(mem)
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['result'] == 'unchanged_cached'
    assert mem['post_month_status_last']['scope'] == [1, '7-9']
    assert mem['general_statuses']['statuses']['ゼウス']['castle'] == 'アルマムーン'


def test_only_event_dirty_general_opens_status_on_roster():
    mem = _persistent_memory('どうし', 'ゼウス')
    receipts.dirty_status(mem, ['ゼウス'], fields=('location',))
    assert mem['general_statuses']['dirty'] == {'ゼウス': ['location']}
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    second = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(first, mem, None) == [pad('down')]
    assert state['names'] == ['どうし']
    assert post.step(second, mem, None) == []
    assert post.step(second, mem, None) == [pad('a')]
    assert post.step(parse(status('ゼウス')), mem, None) == [pad('b')]
    assert state['dirty'] == []
    assert post.step(second, mem, None) == [pad('down')]
    assert post.step(second, mem, None) == [pad('b')]
    assert list(mem['general_statuses']['statuses']) == ['どうし', 'ゼウス']
    assert mem['general_statuses']['dirty'] == {}
    assert mem['general_statuses']['statuses']['どうし']['hp'] == 50


def test_joined_general_gets_only_new_individual_status_read():
    mem = _persistent_memory('どうし')
    assert receipts.record_join(mem, 'ゼウス') is False  # no historical roster receipt
    assert mem['general_statuses']['pending_joins'] == ['ゼウス']
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    second = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(first, mem, None) == [pad('down')]
    assert post.step(second, mem, None) == []
    assert post.step(second, mem, None) == [pad('a')]


def event_dialogue_frame():
    """The measured 59年1月 あけまつ greeting: a green bottom panel, no cursor.

    g1133 2026-11-06 02:57: 「おめでとうございます! あたらしい ぼん くれ
    しょうがつ」. It classifies as phase 'dialogue' and parses as kind 'text',
    exactly like the live frame the monthly state check held for 21 minutes.
    """
    from test_hanjuku_chart_bot import Canvas, GREEN
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    canvas.text(48, 15, '59ねん 1のつき 99936')
    canvas.text(24, 151, 'おめでとうございます!')
    canvas.text(24, 167, 'あたらしい ぼん くれ しょうがつ')
    return canvas.frame()


def test_task_menu_releases_month_event_dialogue_instead_of_holding_input():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_menu', job={'kind': 'locate', 'name': 'ゼウス'},
                 steps=0)
    screen = parse(event_dialogue_frame())
    assert screen.kind == 'text'
    assert post.step(screen, mem, None) is None
    # A scene this owner never claimed must not consume its observation budget.
    assert state['steps'] == 0
    assert mem['post_month_status'] is state
    # The same flow resumes the task menu on the map after the scene ends.
    field = Screen([], None, '', kind='map')
    assert post.step(field, mem, None) == []
    assert post.step(field, mem, None) == []
    assert post.step(field, mem, None) == [pad('x')]


def test_task_menu_still_owns_the_roster_text_panel():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_menu', job={'kind': 'locate', 'name': 'ゼウス'})
    roster_screen = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    assert roster_screen.kind == 'text'
    assert post.step(roster_screen, mem, None) == [pad('down')]
    assert state['stage'] == 'task_select'


def test_blocked_status_releases_the_field_instead_of_holding_it():
    scene = parse(event_dialogue_frame())
    field = Screen([], None, '', kind='map')
    for frame in (scene, field):
        mem = memory()
        post.arm(mem)
        mem['post_month_status'].update(stage='blocked', failure='observation_budget')
        assert post.step(frame, mem, None) is None
        assert 'post_month_status' not in mem
        last = mem['post_month_status_last']
        assert last['failed'] == 'observation_budget'
        assert last['complete'] is False


def test_observation_budget_failure_releases_the_map_on_the_next_observation():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='process', steps=post.MAX_STEPS + 1, tasks=[])
    field = Screen([], None, '', kind='map')
    assert post.step(field, mem, None) == []
    assert state['stage'] == 'blocked'
    assert state['failure'] == 'observation_budget'
    assert post.step(field, mem, None) is None
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['failed'] == 'observation_budget'


def test_month_event_dialogue_is_advanced_while_post_month_tasks_wait():
    from docich.hanjuku_bot import classify, decide
    frame = event_dialogue_frame()
    assert classify(frame) == 'dialogue'
    mem = memory()
    post.arm(mem)
    mem['post_month_status'].update(stage='task_menu',
                                    job={'kind': 'locate', 'name': 'ゼウス'},
                                    steps=0)
    actions, updated = decide(frame, {'policy': mem})
    assert actions == [pad('a')]
    assert updated['policy']['post_month_status']['stage'] == 'task_menu'


def test_defeated_missing_general_pruned_only_after_full_roster_walk():
    mem = _persistent_memory('どうし', 'ゼウス')
    receipts.defeated(mem, 'ゼウス')
    assert 'ゼウス' in mem['general_statuses']['statuses']
    assert mem['general_statuses']['suspected_departures'] == ['ゼウス']
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    only = parse(roster(names=('どうし',), selected=0))
    assert post.step(only, mem, None) == [pad('down')]
    assert 'ゼウス' in _world(mem)['garrison']['アルマムーン']
    assert post.step(only, mem, None) == [pad('b')]
    assert 'ゼウス' not in mem['general_statuses']['statuses']
    assert 'ゼウス' not in _world(mem)['garrison']['アルマムーン']


def test_old_run_identity_never_reuses_cached_status():
    mem = _persistent_memory('どうし')
    mem['_run_identity'] = {'game': 'hanjuku-hero', 'runtime_id': 'new-run',
                            'generation': 2, 'lease_id': 'new-lease'}
    post.arm(mem)
    assert mem['post_month_status']['statuses'] == {}
    assert mem['post_month_status']['stage'] == 'await_map'


def test_scrolled_roster_cannot_discard_unseen_cached_general():
    mem = _persistent_memory('どうし', 'ゼウス')
    receipts.defeated(mem, 'どうし')
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    # The selector starts on the list's tail, so failure to see どうし
    # has not established departure. Rewind before comparing membership.
    tail = parse(roster(names=('ゼウス',), selected=0))
    assert post.step(tail, mem, None) == [pad('up')]
    assert 'どうし' in mem['general_statuses']['statuses']
    assert 'どうし' in _world(mem)['garrison']['アルマムーン']
    assert not state.get('top_confirmed')


def test_existing_defeat_verification_also_purges_persistent_status_and_garrison():
    from test_hanjuku_survey_cache import completed, IDENTITY
    mem = completed()
    mem['general_statuses'] = {
        'scope': [1, '1-6'], 'identity': dict(IDENTITY), 'complete': True,
        'statuses': {name: _known_status(name) for name in ('どうし', 'ゼウス')},
        'dirty': {}}
    receipts.defeated(mem, 'ゼウス')
    assert 'ゼウス' in mem['general_statuses']['statuses']
    assert receipts.forget_defeated(mem, 'ゼウス')
    assert 'ゼウス' not in mem['general_statuses']['statuses']
    assert 'ゼウス' not in _world(mem)['garrison']['アルマムーン']
    assert 'ゼウス' not in mem['house_eggs']


def test_independent_repair_status_updates_only_persistent_egg_and_hp_fields():
    mem = _persistent_memory('ゼウス')
    mem['general_statuses']['statuses']['ゼウス']['broken'] = True
    receipts.dirty_status(mem, ['ゼウス'], fields=('egg', 'hp', 'location'))
    info = {'general': 'ゼウス', 'broken': False, 'egg': 'エラベルエッグ',
            'uses': 4, 'hp': 38, 'max_hp': 50, 'location': 'field',
            'location_observed': True}
    receipts.status_observed(mem, info)
    cache = mem['general_statuses']
    assert cache['statuses']['ゼウス']['broken'] is False
    assert cache['statuses']['ゼウス']['hp'] == 38
    assert cache['dirty'] == {'ゼウス': ['location']}
    assert cache['statuses']['ゼウス']['location'] == 'unknown'
    assert 'castle' not in cache['statuses']['ゼウス']
    assert 'ゼウス' not in _world(mem)['garrison']['アルマムーン']


def test_boss_adjacent_camp_is_not_a_monthly_recall_task():
    # #3505 / g1133: ボス城直前で野営した将軍が月次帰還で戻され、ボス戦が
    # 成立しなかった。marker_castle が MAP_CASTLE_TOLERANCE 内でボス城に
    # 対応づけた castle_candidate を持つ野営は帰還タスクから除外する。
    statuses = {
        'バーミセリ': {'location': 'camp',
                     'marker': {'castle_candidate': 'ボス'}},
        'ヘーゼル': {'location': 'camp',
                   'marker': {'castle_candidate': 'けっかい'}},   # 1面ボス城名
        'ナストリーニ': {'location': 'camp',
                      'marker': {'castle_candidate': 'ダーラント'}},
        'デーメーテール': {'location': 'camp'},                  # marker 無し
        'カバルッサ': {'location': 'camp', 'marker': {}},         # candidate 無し
    }
    assert post._tasks(statuses, list(statuses)) == [
        {'kind': 'recall', 'name': 'ナストリーニ'},
        {'kind': 'recall', 'name': 'デーメーテール'},
        {'kind': 'recall', 'name': 'カバルッサ'}]


def test_broken_boss_adjacent_camp_is_recalled_for_repair():
    # 実測 g1153 2026-11-07: ヴィーナス HP3/82・卵こわれ・けっかい野営。
    # 卵こわれは修理タスクの合図だが、修理は在城確認後のタスクでしか始まらない。
    # ボス至近というだけで月次帰還から除外すると野営に固定される。
    statuses = {
        'ヴィーナス': {
            'location': 'camp', 'broken': True, 'hp': 3, 'max_hp': 82,
            'marker': {'castle_candidate': 'けっかい'}},
        'デーメーテール': {
            'location': 'camp', 'broken': False, 'hp': 49, 'max_hp': 49,
            'marker': {'castle_candidate': 'ボス'}},
    }
    # 無傷のデーメーテールは除外で座り続けるのではなく、いどうでボスへ
    # 再移動するタスクになる（docich#3505 の boss_move）。
    assert post._tasks(statuses, list(statuses)) == [
        {'kind': 'recall', 'name': 'ヴィーナス'},
        {'kind': 'boss_move', 'name': 'デーメーテール'}]


def test_arm_recalls_a_broken_boss_camp_without_exempting_it():
    # 月次 arm はキャッシュ済みでも、こわれたボス至近野営を帰還タスクへ戻し、
    # その将軍を除外月数へ数えない（翌月以降も帰還対象のまま）。
    mem = memory(month='2-12')
    mem['general_statuses'] = {
        'scope': [1, '2-12'], 'complete': True, 'dirty': {},
        'identity': receipts.run_identity(mem.get('_run_identity')),
        'statuses': {'ヴィーナス': {
            'location': 'camp', 'broken': True, 'hp': 3, 'max_hp': 82,
            'marker': {'castle_candidate': 'けっかい'}}}}
    post.arm(mem)
    assert mem['post_month_status']['tasks'] == [
        {'kind': 'recall', 'name': 'ヴィーナス'}]
    assert 'boss_camp_exempt' not in mem


def test_recall_task_starts_the_named_return_on_the_selected_camp(monkeypatch):
    # 月次の queue から実際の帰還シーケンス（recall state と wait_recall）へ入る。
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory(month='2-12')
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump',
                 job={'kind': 'recall', 'name': 'ヴィーナス'},
                 statuses={'ヴィーナス': {'location': 'camp', 'broken': True}})
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(150, 100))
    monkeypatch.setattr(post, 'own_camps',
                        lambda _frame: [{'target': [150, 100], 'kind': 'tent'}])
    monkeypatch.setattr(post.recall_flow, 'camp_recall_step',
                        lambda _screen, _mem, _frame: [pad('a')])
    assert post.step(screen, mem, object()) == [pad('a')]
    assert _flow(mem)['recall']['general'] == 'ヴィーナス'
    assert _flow(mem)['recall']['stage'] == 'to_camp'
    assert state['stage'] == 'wait_recall'


def test_boss_adjacent_camp_exemption_expires_into_a_recall():
    # docich#3659: ボス城至近の野営除外に期限を設ける。除外が
    # BOSS_CAMP_EXEMPT_MONTHS か月連続した野営は、翌月の月次処理で
    # 帰還タスクへ戻され、将軍が野営に固定されないことを保証する。
    statuses = {'ゼウス': {'location': 'camp',
                        'marker': {'castle_candidate': 'けっかい'}}}
    mem = memory()
    mem['general_statuses'] = {
        'scope': [1, '1-6'], 'complete': True, 'dirty': {},
        'identity': receipts.run_identity(mem.get('_run_identity')),
        'statuses': {name: dict(row) for name, row in statuses.items()}}
    # 除外上限までの各月では帰還タスクを積まず、除外月数だけを進める。
    for month in range(2, post.BOSS_CAMP_EXEMPT_MONTHS + 2):
        mem['month'] = f'{month}-1'
        mem.pop('post_month_status', None)
        post.arm(mem)
        # タスク無しの月は cached の早期復帰で owner が残らない。
        assert 'post_month_status' not in mem, mem['post_month_status']
        assert mem['boss_camp_exempt'] == {'ゼウス': month - 1}
    # 上限を超えた月では帰還タスクへ戻る（複数月を跨ぐ到達性）。
    mem['month'] = f'{post.BOSS_CAMP_EXEMPT_MONTHS + 2}-1'
    mem.pop('post_month_status', None)
    post.arm(mem)
    assert mem['post_month_status']['tasks'] == [
        {'kind': 'recall', 'name': 'ゼウス'}]
    assert mem['boss_camp_exempt'] == {
        'ゼウス': post.BOSS_CAMP_EXEMPT_MONTHS + 1}


def test_boss_adjacent_camp_exemption_resets_when_the_camp_resolves():
    # 野営が解消（在城・行軍・帰還済み）した将軍の除外月数は次の月替わりで
    # 破棄され、再びボス城至近で野営しても月数は 1 から数え直す。
    mem = memory()
    mem['general_statuses'] = {
        'scope': [1, '1-6'], 'complete': True, 'dirty': {},
        'identity': receipts.run_identity(mem.get('_run_identity')),
        'statuses': {'ゼウス': {'location': 'camp',
                             'marker': {'castle_candidate': 'けっかい'}}}}
    mem['month'] = '2-1'
    post.arm(mem)
    assert mem['boss_camp_exempt'] == {'ゼウス': 1}
    mem.pop('post_month_status', None)
    mem['general_statuses']['statuses'] = {
        'ゼウス': {'location': 'castle', 'castle': 'アルマムーン'}}
    mem['month'] = '3-1'
    post.arm(mem)
    assert 'boss_camp_exempt' not in mem
    mem.pop('post_month_status', None)
    mem['general_statuses']['statuses'] = {
        'ゼウス': {'location': 'camp',
                  'marker': {'castle_candidate': 'けっかい'}}}
    mem['month'] = '4-1'
    post.arm(mem)
    assert mem['boss_camp_exempt'] == {'ゼウス': 1}


def _castle_menu_with_hand():
    # Live 2026-11-06 g1139 48-10: the しゅつげき/ステータス box stayed open on
    # the map with the hand on しゅつげき while the overlay read
    # 「月越し後の確認: task_menu」 and no input was sent.
    return Screen([], (120, 112), 'しゅつげき ステータス', kind='castle_menu')


def test_task_menu_backs_out_of_a_stray_castle_menu():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_menu', job={'kind': 'locate', 'name': 'ゼウス'}, steps=0)
    assert post.step(_castle_menu_with_hand(), mem, None) == [pad('b')]
    # The owner keeps the stage: the map after the B resumes the X menu.
    assert state['stage'] == 'task_menu'


def test_open_menu_backs_out_of_a_stray_castle_menu():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='open_menu', steps=0)
    assert post.step(_castle_menu_with_hand(), mem, None) == [pad('b')]


def test_blocked_status_closes_a_stray_castle_menu_before_releasing():
    mem = memory()
    post.arm(mem)
    mem['post_month_status'].update(stage='blocked', failure='roster_end_unconfirmed')
    assert post.step(_castle_menu_with_hand(), mem, None) == [pad('b')]
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['failed'] == 'roster_end_unconfirmed'
    # The next field observation belongs to the ordinary planner.
    assert post.step(Screen([], None, '', kind='map'), mem, None) is None


def test_stray_menu_back_out_is_bounded():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_menu', job={'kind': 'locate', 'name': 'ゼウス'}, steps=0)
    outs = [post.step(_castle_menu_with_hand(), mem, None) for _ in range(post.STRAY_MENU_BACKOUTS + 4)]
    assert outs[0] == [pad('b')]
    assert outs.count([pad('b')]) == post.STRAY_MENU_BACKOUTS
    assert outs[-1] == []    # past the bound the generic no-input fallback owns it


def _battle_result_interrupt(mem, ally='ゼウス', *, away=1):
    from docich.hanjuku.flows import battle as battle_flow
    mem['battle'] = {
        'ally': ally, 'enemy': 'ミント', 'ally_hp': 40, 'enemy_hp': 0,
        'away': away, 'cards_used': [], 'card_consumption_complete': True,
        'step': None, 'castle': None, 'side': None,
    }
    battle_flow.battle_end(mem, 'map')


def test_battle_result_reopens_interrupted_monthly_roster_instead_of_planning():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='status', selected='ゼウス', names=['どうし'],
                 top_confirmed=True, list_moves=2, phase_reads=5,
                 statuses={'ゼウス': _known_status('ゼウス')},
                 markers={'ゼウス': {'castle_candidate': 'アルマムーン'}})
    _battle_result_interrupt(mem)
    assert 'battle' not in mem
    assert state['stage'] == 'await_map'
    assert state['names'] == [] and not state.get('top_confirmed')
    assert 'ゼウス' in state['dirty']
    assert state['statuses']['ゼウス']['castle'] == 'アルマムーン'
    assert not state.get('markers')
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert state['stage'] == 'open_menu'
    assert 'post_month_status' in mem
    assert not _plan_region(mem).get('chart_plan')
    assert any(r['decision'] == 'post_month_battle_resume'
               and r['observed_metric']['previous_stage'] == 'status'
               for r in mem['_records'])


def test_battle_result_reopens_same_monthly_named_task_without_losing_queue():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    job = {'kind': 'locate', 'name': 'ゼウス'}
    remaining = [{'kind': 'repair', 'name': 'どうし'}]
    state.update(stage='task_jump', job=job, tasks=remaining, steps=19,
                 phase_reads=8)
    _battle_result_interrupt(mem, ally='アテナ')
    assert state['stage'] == 'task_menu'
    assert state['job'] == job
    assert state['tasks'] == remaining
    assert state['steps'] == 0
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert 'post_month_status' in mem


def test_battle_result_retries_a_blocked_monthly_status_read_instead_of_idle_hold():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='blocked', failure='status_unreadable', steps=post.MAX_STEPS + 1,
                 names=['どうし'], top_confirmed=True)
    _battle_result_interrupt(mem)
    assert state['stage'] == 'await_map'
    assert state['steps'] == 0
    assert 'failure' not in state
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]


def test_unfinished_battle_does_not_rewind_monthly_status():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='roster', names=['どうし'], top_confirmed=True)
    _battle_result_interrupt(mem, away=0)
    assert 'battle' in mem
    assert state['stage'] == 'roster'
    assert state['names'] == ['どうし']
    assert not any(r['decision'] == 'post_month_battle_resume'
                   for r in mem['_records'])


def test_battle_result_preserves_live_monthly_repair_owner():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='wait_repair', job={'kind': 'repair', 'name': 'ゼウス'},
                 tasks=[{'kind': 'locate', 'name': 'どうし'}])
    _battle_result_interrupt(mem, ally='アテナ')
    assert state['stage'] == 'wait_repair'
    assert state['job']['name'] == 'ゼウス'
    assert len(state['tasks']) == 1


def test_battle_resume_is_scoped_to_active_month_and_chapter():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'status'
    mem['month'] = '2-7'
    post.resume_after_battle(mem, {'ally': 'ゼウス'})
    assert state['stage'] == 'status'
    assert not any(r['decision'] == 'post_month_battle_resume'
                   for r in mem['_records'])


def test_battle_result_closes_old_roster_before_reopening_monthly_scan():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='roster', names=['どうし'], top_confirmed=True)
    _battle_result_interrupt(mem, ally='ゼウス')
    assert state['stage'] == 'await_map'
    old_roster = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    assert post.step(old_roster, mem, None) == [pad('b')]
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert state['stage'] == 'open_menu'


EIGHT_GENERALS = ('どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'アマンディーヌ',
                  'リースリング', 'ビシソワーズ', 'マカデミア')


def _full_walk_state(mem, visited):
    state = mem['post_month_status']
    state.update(stage='roster', names=list(visited),
                 statuses={name: _known_status(name) for name in EIGHT_GENERALS},
                 dirty=[], markers={}, top_confirmed=True,
                 list_moves=len(visited))
    return state


def test_full_page_roster_without_wrap_completes_via_end_stall():
    # g1147 2026-11-07 1-8: an 8-name roster fills the visible page, so the
    # short-page end proof never applies. Live 299x224 frames never satisfy
    # the 256x224 scroll-end pixels, and down on the last row does not wrap.
    # The walk had read all 8 yet failed roster_end_unconfirmed after 13
    # identical downs. A short stall on the visible last row of a full walk
    # is now an end proof when the membership proof passes.
    mem = memory()
    post.arm(mem)
    state = _full_walk_state(mem, EIGHT_GENERALS[:7])
    last = parse(roster(names=EIGHT_GENERALS, selected=7))
    assert last.kind == 'text'
    assert post.step(last, mem, None) == [pad('down')]  # cached 8th joins
    assert state['names'] == list(EIGHT_GENERALS)
    assert post.step(last, mem, None) == [pad('down')]
    assert state['end_stall'] == 1
    assert post.step(last, mem, None) == [pad('down')]
    assert state['end_stall'] == 2
    assert post.step(last, mem, None) == [pad('b')]
    assert 'end_stall' not in state
    assert mem['general_statuses']['complete'] is True
    assert list(mem['general_statuses']['statuses']) == list(EIGHT_GENERALS)
    assert state['stage'] == 'process'
    assert not any(r['decision'] == 'post_month_status_unverified'
                   for r in mem['_records'])


def test_end_stall_resets_when_selection_moves():
    mem = memory()
    post.arm(mem)
    state = _full_walk_state(mem, EIGHT_GENERALS)
    last = parse(roster(names=EIGHT_GENERALS, selected=7))
    mid = parse(roster(names=EIGHT_GENERALS, selected=5))
    assert post.step(last, mem, None) == [pad('down')]
    assert state['end_stall'] == 1
    assert post.step(last, mem, None) == [pad('down')]
    assert state['end_stall'] == 2
    # Moving off the last row drops the stall count instead of completing.
    assert post.step(mid, mem, None) == [pad('down')]
    assert 'end_stall' not in state
    assert post.step(last, mem, None) == [pad('down')]
    assert state['end_stall'] == 1
    assert post.step(last, mem, None) == [pad('down')]
    assert state['end_stall'] == 2
    assert post.step(last, mem, None) == [pad('b')]
    assert mem['general_statuses']['complete'] is True


def test_end_stall_never_completes_a_partial_walk():
    mem = memory()
    post.arm(mem)
    state = _full_walk_state(mem, EIGHT_GENERALS[:3])
    last = parse(roster(names=EIGHT_GENERALS, selected=7))
    outs = [post.step(last, mem, None) for _ in range(post.STATUS_RETRIES + 2)]
    assert state['failure'] == 'roster_end_unconfirmed'
    assert [pad('b')] in outs
    # The blocked owner then releases the field (v505): the failure is kept
    # in post_month_status_last without writing anything verified.
    assert post.step(last, mem, None) is None
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['failed'] == 'roster_end_unconfirmed'
    assert 'general_statuses' not in mem
    assert not any(r['decision'] == 'post_month_general_departed'
                   for r in mem['_records'])
    unverified = [r for r in mem['_records']
                  if r['decision'] == 'post_month_status_unverified']
    assert len(unverified) == 1
    assert unverified[0]['observed_metric']['selected'] == 'マカデミア'


def test_lost_march_general_is_rechecked_by_the_next_status_pass():
    # 2026-11-07: a march record expired while a monthly pass was running. The
    # pass committed its snapshot and dropped the queued location dirty flag,
    # so the general stayed in ``general_location_unknown`` and every later
    # month reused the cache: his status was never opened again. The unknown
    # entry itself must request the next individual status confirmation.
    mem = _persistent_memory('どうし', 'ゼウス')
    _world(mem)['garrison']['アルマムーン'] = ['どうし']
    _world(mem)['general_location_unknown'] = ['ゼウス']
    assert mem['general_statuses']['dirty'] == {}
    post.arm(mem)
    state = mem['post_month_status']
    assert state['stage'] == 'await_map'
    assert state['dirty'] == ['ゼウス']
    assert mem['post_month_status'] is state
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    second = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(first, mem, None) == [pad('down')]
    assert post.step(second, mem, None) == []
    assert post.step(second, mem, None) == [pad('a')]
    assert post.step(parse(status('ゼウス', egg='エラベルエッグ4')), mem, None) == [pad('b')]
    assert post.step(second, mem, None) == [pad('down')]
    assert post.step(second, mem, None) == [pad('b')]
    # The status only says しろのなかにいます; the castle name comes from the
    # SELECT locate task, never from the stale pre-march row.
    assert state['tasks'] == [{'kind': 'locate', 'name': 'ゼウス'}]
    assert _world(mem)['general_location_unknown'] == ['ゼウス']


def test_unknown_location_outside_the_verified_roster_is_pruned():
    # g1139 2026-11-07 23:40: a departed general's leftover sortie expired and
    # queued a location recheck. He is not on the verified roster, so no status
    # pass could ever read him; the entry stayed and kept him unavailable.
    mem = _persistent_memory('どうし')
    _world(mem)['general_location_unknown'] = ['セルリアク']
    post.arm(mem)
    assert _world(mem)['general_location_unknown'] == []
    assert 'post_month_status' not in mem
    pruned = [r for r in mem['_records']
              if r['decision'] == 'post_month_unknown_location_pruned']
    assert len(pruned) == 1
    assert pruned[0]['observed_metric']['generals'] == ['セルリアク']


def test_unknown_joined_general_is_kept_until_the_roster_reads_him():
    mem = _persistent_memory('どうし')
    mem['general_statuses']['pending_joins'] = ['ゼウス']
    _world(mem)['general_location_unknown'] = ['ゼウス']
    post.arm(mem)
    assert _world(mem)['general_location_unknown'] == ['ゼウス']
    state = mem['post_month_status']
    assert state['stage'] == 'await_map'  # the pending join forces the walk
    assert 'ゼウス' not in state['dirty']  # read as a new name, not as cached


def test_completed_walk_prunes_unknown_names_the_roster_does_not_hold():
    mem = memory()
    post.arm(mem)
    state = _full_walk_state(mem, EIGHT_GENERALS)
    _world(mem)['general_location_unknown'] = ['セルリアク', EIGHT_GENERALS[0]]
    last = parse(roster(names=EIGHT_GENERALS, selected=7))
    assert post.step(last, mem, None) == [pad('down')]
    assert post.step(last, mem, None) == [pad('down')]
    assert post.step(last, mem, None) == [pad('b')]
    assert mem['general_statuses']['complete'] is True
    assert _world(mem)['general_location_unknown'] == [EIGHT_GENERALS[0]]


def test_departed_general_leaves_the_unknown_location_queue():
    # The purge read the flat key after the world region had migrated it, so a
    # general proven absent from the roster stayed queued for a recheck.
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='roster', names=[EIGHT_GENERALS[0]], dirty=[],
                 markers={}, top_confirmed=True, list_moves=1,
                 statuses={EIGHT_GENERALS[0]: _known_status(EIGHT_GENERALS[0]),
                           'セルリアク': _known_status('セルリアク')})
    _world(mem)['general_location_unknown'] = ['セルリアク']
    short = parse(roster(names=(EIGHT_GENERALS[0],), selected=0))
    assert post.step(short, mem, None) == [pad('b')]
    assert _world(mem)['general_location_unknown'] == []
    departed = [r for r in mem['_records']
                if r['decision'] == 'post_month_general_departed']
    assert [r['general'] for r in departed] == ['セルリアク']


def test_a_recheck_queued_during_the_pass_still_reaches_the_next_month():
    # A march can expire while the monthly walk is running. The pass commits
    # its snapshot with an empty dirty set, so the flag queued by the expiry
    # disappears; the unknown entry must still schedule the next read.
    from docich.hanjuku.strategy import orders
    mem = _persistent_memory('どうし', 'ゼウス')
    # Force the walk without a dirty flag: only the headcount looks wrong.
    mem['country_general_count'] = {'scope': [1, '1-7'], 'identity': None,
                                    'count': 3}
    post.arm(mem)
    state = mem['post_month_status']
    assert state['dirty'] == []
    mem.pop('country_general_count')  # only needed to open the walk
    state.update(stage='roster', names=['どうし', 'ゼウス'], top_confirmed=True,
                 list_moves=2, markers={})
    orders._queue_overdue_location_recheck(
        mem, {'general': 'ゼウス', 'source': 'アルマムーン',
              'target': 'ジョンリギ', 'tick': 1})
    assert mem['general_statuses']['dirty'] == {'ゼウス': ['location']}
    assert _world(mem)['general_location_unknown'] == ['ゼウス']
    short = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(short, mem, None) == [pad('b')]
    assert mem['general_statuses']['dirty'] == {}  # the pass overwrote it
    assert _world(mem)['general_location_unknown'] == ['ゼウス']
    mem['month'] = '1-8'
    post.arm(mem)
    assert mem['post_month_status']['dirty'] == ['ゼウス']



def test_visible_roster_hop_uses_first_frame_for_highlight_gate():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'open_menu'
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    assert post.step(first, mem, None) == []  # first distinct highlight reading
    assert state['stage'] == 'roster'
    assert state['steps'] == 1
    assert post.step(first, mem, None) == [pad('a')]
    assert state['steps'] == 2
    # A waits for a genuinely new status frame; no second input is appended.
    assert post.step(first, mem, None) == []
    assert not state['statuses']


def test_visible_roster_hop_moves_cached_member_without_idle_observation():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='open_menu', statuses={'どうし': {'location': 'castle'}})
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    assert post.step(first, mem, None) == [pad('down')]
    assert state['steps'] == 1
    assert state['names'] == ['どうし']


def test_task_menu_hop_selects_visible_named_actor_once():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_menu', job={'kind': 'locate', 'name': 'ゼウス'})
    frame = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(frame, mem, None) == [pad('select')]
    assert state['stage'] == 'task_jump'
    assert state['steps'] == 1


def _carry_status(name='ゼウス'):
    c = Canvas()
    c.text(24, 39, name)
    c.text(88, 39, 'しょうぐん')
    c.text(24, 55, 'HP')
    c.text(64, 55, '50/ 50 P')
    c.text(24, 119, 'たまご')
    c.text(56, 119, 'エラベルエッグ4')
    c.text(24, 151, 'いまこちらにむかっております')
    return parse(c.frame())


def _general_list(names, selected=0):
    c = Canvas()
    for index, name in enumerate(names):
        c.text(144, 39 + index * 16, name)
    c.hand(120, 33 + selected * 16)
    screen = parse(c.frame())
    screen.kind = 'general_list'
    return screen


def test_chapter_carry_status_is_a_known_waiting_queue():
    screen = _carry_status()
    assert post._location(screen) == 'carry'
    info = post.house.general_status(screen)
    assert info['carry_waiting'] is True
    assert info['location'] == 'field'
    assert info['location_observed'] is True


def test_carry_queue_arms_the_hero_castle_manifest_without_a_status_walk():
    mem = _persistent_memory('どうし', 'ゼウス', 'ヴィーナス')
    for name in ('ゼウス', 'ヴィーナス'):
        mem['general_statuses']['statuses'][name].update(location='carry', castle=None)
    mem['garrison'] = {'アルマムーン': ['どうし']}
    post.arm(mem)
    state = mem['post_month_status']
    assert state['stage'] == 'manifest'
    assert state['dirty'] == []
    assert state['manifest']['castle'] == 'アルマムーン'
    assert state['manifest']['waiting'] == ['ゼウス', 'ヴィーナス']
    assert not any(r['decision'] == 'post_month_status_reused' for r in mem['_records'])


def test_carry_queue_without_a_hero_castle_reuses_the_cache_without_input():
    mem = _persistent_memory('ゼウス')
    mem['general_statuses']['statuses']['ゼウス'].update(location='carry', castle=None)
    mem['garrison'] = {}
    post.arm(mem)
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['result'] == 'unchanged_cached'
    assert mem['post_month_status_last']['carry'] == ['ゼウス']


def test_complete_scan_accepts_carry_and_starts_queue_tracking():
    mem = memory()
    post.arm(mem)
    state = _full_walk_state(mem, EIGHT_GENERALS[:7])
    state['statuses']['ゼウス'] = {**_known_status('ゼウス'),
                                  'location': 'carry', 'castle': None}
    last = parse(roster(names=EIGHT_GENERALS, selected=7))
    assert post.step(last, mem, None) == [pad('down')]
    assert post.step(last, mem, None) == [pad('down')]
    assert post.step(last, mem, None) == [pad('down')]
    assert post.step(last, mem, None) == [pad('b')]
    assert mem['general_statuses']['complete'] is True
    assert mem['general_statuses']['statuses']['ゼウス']['location'] == 'carry'
    assert mem['carry_tracking']['initial'] == 1
    assert mem['carry_tracking']['remaining'] == 1


def test_manifest_binds_residents_and_leaves_unlisted_carry_waiters():
    mem = _persistent_memory('どうし', 'ゼウス', 'ヴィーナス')
    for name in ('ゼウス', 'ヴィーナス'):
        mem['general_statuses']['statuses'][name].update(location='carry', castle=None)
    mem['garrison'] = {'アルマムーン': ['どうし']}
    post.arm(mem)
    state = mem['post_month_status']
    post._apply_manifest(mem, state, ['どうし', 'ゼウス'],
                         complete=True, evidence='wrapped')
    assert state['statuses']['ゼウス']['location'] == 'castle'
    assert state['statuses']['ゼウス']['castle'] == 'アルマムーン'
    assert state['statuses']['ゼウス']['castle_evidence'] == 'hero_castle_manifest'
    assert state['statuses']['ヴィーナス']['location'] == 'carry'
    assert state['dirty'] == []
    assert _world(mem)['garrison']['アルマムーン'] == ['どうし', 'ゼウス']
    assert mem['general_statuses']['statuses']['ゼウス']['location'] == 'castle'
    assert mem['carry_manifest_scope'] == [1, '1-7']
    assert mem['post_month_status_last'] if 'post_month_status_last' in mem else True


def test_manifest_navigation_reaches_the_hero_castle_and_finishes():
    mem = _persistent_memory('どうし', 'ゼウス')
    mem['general_statuses']['statuses']['ゼウス'].update(location='carry', castle=None)
    mem['garrison'] = {'アルマムーン': ['どうし']}
    post.arm(mem)
    state = mem['post_month_status']
    with patch.object(post, 'menu_to', return_value='here'), \
         patch.object(post.map_registration, '_label', return_value='アルマムーン'):
        assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
        assert state['stage'] == 'task_menu'
        main = Screen([], (40, 40), '', kind='main_menu')
        assert post.step(main, mem, None) == [pad('a')]
        assert state['stage'] == 'task_select'
        hero = parse(roster(names=('どうし', 'ゼウス'), selected=0))
        assert post.step(hero, mem, None) == [pad('select')]
        assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('a')]
        castle = Screen([], (40, 184), 'しゅつげき', kind='castle_menu')
        assert post.step(castle, mem, None) == []
        assert post.step(castle, mem, None) == [pad('a')]
        assert state['stage'] == 'manifest_list'
        for index in (0, 1, 0):
            assert post.step(_general_list(('どうし', 'ゼウス'), index),
                             mem, None) is not None
    assert state['statuses']['ゼウス']['location'] == 'castle'
    assert state['stage'] == 'manifest_close'
    assert post.step(Screen([], (40, 184), '', kind='castle_menu'), mem, None) == [pad('b')]
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['manifest_done'] is True


def test_manifest_walks_the_sortie_list_and_finishes_without_individual_status():
    mem = _persistent_memory('どうし', 'ゼウス', 'ヴィーナス')
    for name in ('ゼウス', 'ヴィーナス'):
        mem['general_statuses']['statuses'][name].update(location='carry', castle=None)
    mem['garrison'] = {'アルマムーン': ['どうし']}
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'manifest_list'
    for index in (0, 1, 2, 0):
        assert post.step(_general_list(('どうし', 'ゼウス', 'ヴィーナス'), index),
                         mem, None) is not None
    assert state['manifest']['done'] is True
    assert state['statuses']['ゼウス']['location'] == 'castle'
    assert state['statuses']['ヴィーナス']['location'] == 'castle'
    assert state['stage'] == 'manifest_close'
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert 'post_month_status' not in mem
    assert mem['post_month_status_last']['manifest_done'] is True
    assert not any(r['decision'] == 'post_month_status_complete'
                   for r in mem['_records'])


def test_manifest_satisfied_resident_with_hp_dirty_skips_the_triangle():
    mem = _persistent_memory('どうし', 'ゼウス')
    mem['general_statuses']['statuses']['ゼウス'].update(location='carry', castle=None)
    mem['garrison'] = {'アルマムーン': ['どうし']}
    post.arm(mem)
    state = mem['post_month_status']
    post._apply_manifest(mem, state, ['どうし', 'ゼウス'],
                         complete=True, evidence='wrapped')
    state.update(stage='roster', names=[], dirty=['ゼウス'],
                 top_confirmed=True, list_moves=1)
    frame = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    with patch.object(post, '_marker', side_effect=AssertionError('marker must not be read')):
        assert post.step(frame, mem, None) == []
        assert post.step(frame, mem, None) == [pad('a')]
    assert post.step(parse(status('ゼウス', egg='エラベルエッグ4')), mem, None) == [pad('b')]
    assert state['statuses']['ゼウス']['location'] == 'castle'
    assert 'ゼウス' not in state['dirty']


def test_manifest_state_survives_the_bot_json_write():
    # g1153 11-08 19:26: the manifest stored `manifest_satisfied` as a set in
    # the policy memory. `brains/hanjuku/bot.py` writes that memory as JSON,
    # so every observation raised TypeError, the decided input was dropped and
    # the corner ended as input_stalled. The memory must stay JSON-encodable.
    mem = _persistent_memory('どうし', 'ゼウス')
    mem['general_statuses']['statuses']['ゼウス'].update(location='carry', castle=None)
    mem['garrison'] = {'アルマムーン': ['どうし']}
    post.arm(mem)
    state = mem['post_month_status']
    post._apply_manifest(mem, state, ['どうし', 'ゼウス'],
                         complete=True, evidence='wrapped')
    assert isinstance(state['manifest_satisfied'], list)
    assert set(state['manifest_satisfied']) == {'どうし', 'ゼウス'}
    json.dumps(mem, ensure_ascii=False, sort_keys=True)


def test_waiting_carry_row_can_cross_the_month_boundary():
    row = {'location': 'field', 'carry_waiting': True,
           'location_observed': True, 'broken': False,
           'hp': 50, 'max_hp': 50}
    assert receipts._carry_eligible(row, ()) is True
    assert receipts._carry_eligible(row, ('location',)) is False


def _wait_recall_state(mem, name='ヴィーナス'):
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='wait_recall', started_tick=100,
                 job={'kind': 'recall', 'name': name}, tasks=[])
    return state


def test_interrupted_recall_task_is_requeued_in_the_same_month():
    # 実測 g1153 2026-11-08 17:00/17:05: 目的地ピッカー中に防衛・攻撃イベントが
    # 割り込み、帰還Aを送らないまま recall state が消えた。タスクを完了扱いに
    # すると翌月まで再試行されず、野営の将軍が残る。
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory(month='2-12')
    state = _wait_recall_state(mem)
    _flow(mem).pop('recall', None)
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert state['tasks'] == [{'kind': 'recall', 'name': 'ヴィーナス'}]
    assert state['recall_task_retries'] == {'ヴィーナス': 1}
    assert state['stage'] == 'process'
    assert any(r['decision'] == 'post_month_recall_requeued'
               for r in mem['_records'])


def test_dispatched_recall_task_is_not_requeued():
    # 帰還Aの送信記録がある場合は再送しない（同じ帰還の二重指示を避ける）。
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory(month='2-12')
    state = _wait_recall_state(mem)
    _flow(mem)['recall_verification'] = {
        'general': 'ヴィーナス', 'input_sent': True,
        'status': 'arrival_unconfirmed', 'requested_tick': 120}
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert state['tasks'] == []
    assert not any(r['decision'] == 'post_month_recall_requeued'
                   for r in mem['_records'])


def test_stale_dispatch_receipt_does_not_suppress_the_retry():
    # 前月の同名将軍の送信記録を今月の帰還と取り違えない。
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory(month='2-12')
    state = _wait_recall_state(mem)
    _flow(mem)['recall_verification'] = {
        'general': 'ヴィーナス', 'input_sent': True,
        'status': 'arrival_unconfirmed', 'requested_tick': 50}
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert state['tasks'] == [{'kind': 'recall', 'name': 'ヴィーナス'}]


def test_recall_task_retry_is_bounded():
    from docich.hanjuku.state.regions import flow as _flow
    mem = memory(month='2-12')
    state = _wait_recall_state(mem)
    state['recall_task_retries'] = {'ヴィーナス': post.RECALL_TASK_RETRIES}
    _flow(mem).pop('recall', None)
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert state['tasks'] == []
    assert state['stage'] == 'process'


def test_a_static_unresolvable_marker_is_not_re_read_for_every_general():
    """g1163 2026-11-09: box [59,120,64,125] repeated for twenty generals."""
    state = {}
    first = {'box': [59, 120, 64, 125], 'source': 'roster_triangle_candidate'}
    assert post._static_marker(state, first) is False      # first sighting: retry once
    assert state['static_marker'] == [59, 120, 64, 125]
    second = {'box': [59, 120, 64, 125], 'source': 'roster_triangle_candidate'}
    assert post._static_marker(state, second) is True      # known static: no retries
    moved = {'box': [70, 120, 75, 125], 'source': 'roster_triangle_candidate'}
    assert post._static_marker(state, moved) is False      # a new box may resolve
    resolved = {'box': [70, 120, 75, 125], 'castle_candidate': 'アルマムーン'}
    assert post._static_marker(state, resolved) is False
    assert post._static_marker(state, {}) is False


def test_a_same_month_house_read_skips_the_individual_reopen():
    """g1163 2026-11-09: the house pass read at 09:55, this walk at 09:58."""
    mem = memory(tick=1000, month='2-6')
    mem['house_eggs'] = {'どうし': {'location': 'castle', 'location_observed': True,
                                   'castle': 'アルマムーン', 'month': '2-6',
                                   'observed_tick': 900},
                         'ゼウス': {'location': 'march', 'month': '2-6',
                                   'observed_tick': 100}}
    assert post._house_freshly_read(mem) == {'どうし'}
    mem['month'] = '2-7'                                   # another month says nothing
    assert post._house_freshly_read(mem) == set()
    mem['month'] = '2-6'
    mem['house_eggs']['アテナ'] = {'location': 'castle', 'location_observed': False,
                                  'month': '2-6', 'observed_tick': 990}
    assert 'アテナ' not in post._house_freshly_read(mem)   # the location was not observed
    mem['house_eggs']['エピィ'] = {'location': 'castle', 'month': '2-6', 'observed_tick': 'x'}
    assert 'エピィ' not in post._house_freshly_read(mem)   # an unreadable tick is no evidence
