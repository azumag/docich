"""Post-month roster-first policy: sequence ordering and conservative evidence."""
from unittest.mock import patch

from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.os_layer import roster as receipts
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen, parse

from test_hanjuku_house import Canvas, roster, status
from docich.hanjuku.state.regions import world as _world


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


def test_repair_uses_roster_select_then_verified_castle_entry():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'repair', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle', 'broken': True,
                                     'castle': 'アルマムーン'}})
    with patch.object(post, '_roof_under_cursor', return_value=True):
        assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('a')]
    assert state['stage'] == 'wait_repair'
    assert mem['house']['phase'] == 'castle_verify'
    assert mem['house']['general'] == 'ゼウス'


def test_unknown_status_never_allows_planning():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='status', selected='ゼウス', phase_reads=post.STATUS_RETRIES)
    assert post.step(Screen([], None, '', kind='unknown'), mem, None) == [pad('b')]
    assert state['stage'] == 'blocked'
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
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
    with patch.object(post, '_roof_under_cursor', return_value=False):
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
    assert post.step(roster_screen, mem, None) == []
    assert state['stage'] == 'task_select'


def test_blocked_status_releases_scene_but_keeps_holding_the_map():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='blocked')
    assert post.step(parse(event_dialogue_frame()), mem, None) is None
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []


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


def test_blocked_status_still_backs_out_of_a_stray_castle_menu():
    mem = memory()
    post.arm(mem)
    mem['post_month_status'].update(stage='blocked')
    assert post.step(_castle_menu_with_hand(), mem, None) == [pad('b')]
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []   # the map hold is unchanged


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
    assert not mem.get('chart_plan')
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
