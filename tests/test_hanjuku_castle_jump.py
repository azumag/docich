"""Source-castle jumps use the X menu's しろ list, not the Y world map.

Owner instruction 2026-11-06: while planning a sortie from castle A to B, do
not fly to A through the Y world map. Open X, choose しろ, choose the castle
name and press SELECT (manual p.15: 将軍と同じく、セレクトボタンでその城へ飛べる).
The measured list is the right-hand panel of the main menu: names at x=168 on
16 px rows from y=39 with the orange hand, and the help line
"Aボタンでステータスひょうじ Bボタンでコマンドにもどりますじゃ!".
"""

from docich.hanjuku.flows import map as M
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.screen import Screen


def _line(y, x, text):
    return TextLine(y, tuple((x + 8 * i, ch) for i, ch in enumerate(text)))


def _screen(kind, rows, *, hand=None, text='', cursor=None):
    lines = [_line(y, x, word) for y, x, word in rows]
    return Screen(lines=lines, hand=hand, text=text or ' '.join(w for _, _, w in rows),
                  kind=kind, cursor=cursor)


def _main_menu(hand_y=39):
    rows = [(39, 168, 'ステータス'), (55, 168, 'しょうぐん'), (71, 168, 'しろ'),
            (87, 168, 'きりふだ'), (103, 168, 'システム')]
    return _screen('main_menu', rows, hand=(146, hand_y - 6, 166, hand_y + 7),
                   text='ステータス しょうぐん しろ きりふだ システム')


def _castle_list(names, hand_y=39):
    rows = [(y, 168, name) for y, name in zip(range(39, 39 + 16 * len(names), 16), names)]
    rows += [(183, 24, 'Aボタンでステータスひょうじ'),
             (199, 24, 'コマンドにもどりますじゃ!')]
    text = 'Aボタンでステータスひょうじ Bボタンで コマンドにもどりますじゃ!'
    return _screen('text', rows, hand=(146, hand_y - 6, 166, hand_y + 7), text=text)


def _order(source='アルマムーン', step='J1'):
    return {'step': step, 'source': source, 'target': 'ナキューメラ',
            'cards': [], 'note': 'test', 'after': ()}


def _memory(order=None):
    order = order or _order()
    return {'chapter': 1, 'active': order['step'], 'tick': 10, '_records': [],
            'orders': {order['step']: 'pending'},
            'chart_plan': {'orders': [order]}, 'cursor': [400, 400]}


def test_list_reads_source_name_across_the_long_vowel_mark():
    screen = _castle_list(['アルマームン', 'ナキューメラ'])
    assert M._castle_list_rows(screen)
    assert M._castle_list_target(screen, 'アルマムーン') == 'アルマームン'


def test_list_is_not_a_general_roster():
    screen = _castle_list(['ゼウス', 'ヴィーナス'])
    assert M._castle_list_target(screen, 'アルマムーン') is None


def test_source_far_away_starts_the_x_menu():
    mem = _memory()
    order = mem['chart_plan']['orders'][0]
    goal = M.chart.castles(1)[order['source']]
    assert M._want_castle_jump(mem, order, order['source'], goal)
    actions = M._start_castle_jump(mem, order, order['source'])
    assert actions == [M.pad('x')]
    assert mem['castle_jump']['phase'] == 'menu'


def test_main_menu_moves_to_shiro_and_confirms_it():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'menu',
                          'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 1}
    # hand on ステータス: one Down reaches しょうぐん, then しろ
    screen = _main_menu(hand_y=39)
    assert M.castle_jump_step(screen, mem, None) == [M.pad('down')]
    screen = _main_menu(hand_y=71)
    assert M.castle_jump_step(screen, mem, None) == [M.pad('a')]
    assert mem['castle_jump']['phase'] == 'list'


def test_castle_row_sends_select():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'list',
                          'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 2}
    screen = _castle_list(['アルマームン', 'ナキューメラ'])
    assert M.castle_jump_step(screen, mem, None) == [M.pad('select')]
    assert mem['castle_jump']['phase'] == 'confirm'


def test_map_after_select_opens_the_source_castle():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('map', [], cursor=(80, 90))
    assert M.castle_jump_step(screen, mem, None) == [M.pad('a')]
    assert 'castle_jump' not in mem
    assert mem['expect_menu'] is True


def test_map_after_select_opens_castle_when_roof_is_occluded(monkeypatch):
    # A live frame can hide the roof underneath the cursor; the previous
    # implementation waited three times and cancelled instead of entering.
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('map', [], cursor=(80, 90))
    monkeypatch.setattr(M, '_roof_under_cursor', lambda _screen, _frame: False)
    assert M.castle_jump_step(screen, mem, object()) == [M.pad('a')]
    assert 'castle_jump' not in mem
    assert mem['expect_menu'] is True
    assert 'castle_jump_skip' not in mem


def test_status_opened_by_select_backs_out_once():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('castle_info', [])
    assert M.castle_jump_step(screen, mem, None) == [M.pad('b')]
    assert mem['castle_jump']['phase'] == 'list'


def test_missing_castle_scrolls_then_gives_up():
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'list',
                          'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 2,
                          'scrolls': M.CASTLE_JUMP_SCROLL_LIMIT}
    screen = _castle_list(['ナキューメラ'])
    actions = M.castle_jump_step(screen, mem, None)
    assert actions == [M.pad('b')]
    assert 'castle_jump' not in mem
    assert mem['castle_jump_skip']['step'] == 'J1'


def test_lost_cursor_uses_x_castle_list_without_an_active_order_or_roof(monkeypatch):
    mem = _memory()
    mem['active'] = None
    mem['nav_search'] = True
    mem['uncertain'] = True
    mem['anchor'] = None
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    assert mem['castle_jump']['mode'] == 'reanchor'
    assert M.castle_jump_step(_main_menu(hand_y=39), mem, None) == [M.pad('down')]
    assert M.castle_jump_step(_main_menu(hand_y=71), mem, None) == [M.pad('a')]
    assert M.castle_jump_step(_castle_list(['アルマームン', 'ナキューメラ']), mem, None) == [M.pad('select')]

    def must_not_check_roof(*_args):
        raise AssertionError('the re-anchor must not inspect roof pixels')

    monkeypatch.setattr(M, '_roof_under_cursor', must_not_check_roof)
    assert M.castle_jump_step(_screen('map', [], cursor=(80, 90)), mem, object()) is None
    assert mem['cursor'] == list(M.chart.castles(1)['アルマムーン'])
    assert mem['anchor'] == 'アルマムーン'
    assert mem['uncertain'] is False
    assert mem['nav_last']['screen'] == [80, 90]
    assert mem['goal_anchor_lock'] == 'アルマムーン'
    assert 'nav_search' not in mem
    assert 'cursor_still' not in mem
    assert 'castle_jump' not in mem
    assert 'expect_menu' not in mem
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_confirm'


def _confirm_reanchor_with_active_order():
    mem = _memory()
    mem['nav_search'] = True
    mem['uncertain'] = True
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    mem['castle_jump']['phase'] = 'confirm'
    screen = _screen('map', [], cursor=(80, 90))
    assert M.castle_jump_step(screen, mem, None) is None
    assert mem['reanchor_resume']['active_before'] == 'J1'
    assert mem['reanchor_resume']['owner'] == 'sortie_plan'
    return mem, screen


def _allow_interrupted_order(monkeypatch):
    monkeypatch.setattr(M, '_ready', lambda *_args: True)
    monkeypatch.setattr(M, '_owned', lambda _mem: {'アルマムーン'})
    monkeypatch.setattr(M, '_near_goal', lambda *_args: False)
    monkeypatch.setattr(M, '_want_castle_jump', lambda *_args: False)


def test_reanchor_success_releases_same_frame_to_interrupted_order(monkeypatch):
    mem, screen = _confirm_reanchor_with_active_order()
    _allow_interrupted_order(monkeypatch)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: [M.pad('right')])

    # The normal map policy, previously starved by castle_jump_step's [],
    # can immediately choose the old order's navigation input.
    actions = M.resume_reanchored_owner(screen, mem, None)
    assert actions == [M.pad('right')]
    M.note_reanchor_followup(screen, mem, actions)
    assert 'reanchor_resume' not in mem
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_resumed'
    assert mem['_records'][-1]['observed_metric']['active_after'] == 'J1'


def test_reanchor_without_active_order_picks_next_ready_plan(monkeypatch):
    mem = _memory()
    mem['active'] = None
    mem['nav_search'] = True
    mem['uncertain'] = True
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    mem['castle_jump']['phase'] = 'confirm'
    screen = _screen('map', [], cursor=(80, 90))
    assert M.castle_jump_step(screen, mem, None) is None

    _allow_interrupted_order(monkeypatch)
    # A real executable order contains the planned general; the lightweight
    # castle-jump-only fixture intentionally omits it.
    order = {**mem['chart_plan']['orders'][0], 'general': 'どうし'}
    monkeypatch.setattr(M, '_world_map_wanted', lambda _mem: False)
    monkeypatch.setattr(M, 'next_order', lambda _mem: order)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: [M.pad('right')])
    actions = M.resume_reanchored_owner(screen, mem, None)
    assert actions == [M.pad('right')]
    assert mem['active'] == 'J1'
    M.note_reanchor_followup(screen, mem, actions)
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_resumed'


def test_reanchor_named_source_opens_even_when_cursor_hides_roof(monkeypatch):
    mem, screen = _confirm_reanchor_with_active_order()
    _allow_interrupted_order(monkeypatch)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: 'arrived')

    def roof_hidden(*_args):
        raise AssertionError('named SELECT should not require a visible roof')

    monkeypatch.setattr(M, '_roof_under_cursor', roof_hidden)
    actions = M.resume_reanchored_owner(screen, mem, None)
    assert actions == [M.pad('a')]
    assert mem['expect_menu'] is True
    M.note_reanchor_followup(screen, mem, actions)
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_resumed'


def test_reanchor_without_ready_order_reports_hold_and_rechecks(monkeypatch):
    mem = _memory()
    mem['active'] = None
    mem['reanchor_resume'] = {'source': 'アルマムーン', 'tick': 10, 'active_before': None}
    mem['map_idle_tick'] = 10
    screen = _screen('map', [], cursor=(80, 90))
    M.note_reanchor_followup(screen, mem, [])
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_wait'
    assert mem['_records'][-1]['observed_metric']['blocked_by'] == 'no_ready_order'
    assert mem['reanchor_resume']['wait_report_tick'] == 10

    # Holds are reported periodically, without flooding the decision log.
    length = len(mem['_records'])
    mem['tick'] = 11
    M.note_reanchor_followup(screen, mem, [])
    assert len(mem['_records']) == length
    mem['tick'] = 30
    mem['post_month_status'] = {'stage': 'blocked'}
    M.note_reanchor_followup(screen, mem, [])
    assert mem['_records'][-1]['observed_metric']['blocked_by'] == 'post_month_status_blocked'
    assert len(mem['_records']) == length + 1
    M.note_reanchor_followup(screen, mem, [M.pad('x')])
    assert 'reanchor_resume' not in mem
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_resumed'


def test_map_starts_reanchor_instead_of_inland_search(monkeypatch):
    mem = _memory()
    _allow_interrupted_order(monkeypatch)
    mem['nav_search'] = True
    mem['uncertain'] = True
    screen = _screen('map', [], cursor=(80, 90))
    assert M.map_step(screen, mem, None) == [M.pad('x')]
    assert mem['castle_jump']['source'] == 'アルマムーン'
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_open'


def test_idle_map_with_stale_search_does_not_repeat_reanchor(monkeypatch):
    mem = _memory()
    mem.update(active=None, nav_search=True, uncertain=True, anchor=None)
    screen = _screen('map', [], cursor=(80, 90))
    monkeypatch.setattr(M, '_world_map_wanted', lambda _mem: False)
    monkeypatch.setattr(M, 'next_order', lambda _mem: None)
    monkeypatch.setattr(M, '_off_chart', lambda _mem: None)
    monkeypatch.setattr(M, 'update_world', lambda *_args: mem['cursor'])
    for tick in range(10, 10 + M.CASTLE_JUMP_COOLDOWN * 2):
        mem['tick'] = tick
        assert M.map_step(screen, mem, None) == []
    assert 'castle_jump' not in mem
    assert mem['nav_search'] is True
    assert all(r['decision'] != 'cursor_reanchor_open' for r in mem['_records'])

    # Recovery is deferred until there is real work, rather than discarded.
    _allow_interrupted_order(monkeypatch)
    order = {**mem['chart_plan']['orders'][0], 'general': 'どうし'}
    monkeypatch.setattr(M, 'next_order', lambda _mem: order)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: None)
    assert M.map_step(screen, mem, None) == []
    mem['tick'] += 1
    assert M.map_step(screen, mem, None) == [M.pad('x')]
    assert mem['active'] == 'J1'
    assert mem['castle_jump']['return_to']['active_step'] == 'J1'


def test_failed_reanchor_keeps_search_as_fallback_but_is_cooldown_limited():
    mem = _memory()
    mem['nav_search'] = True
    mem['uncertain'] = True
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    jump = mem['castle_jump']
    jump['phase'] = 'list'
    jump['scrolls'] = M.CASTLE_JUMP_SCROLL_LIMIT
    assert M.castle_jump_step(_castle_list(['ナキューメラ']), mem, None) == [M.pad('b')]
    assert mem['nav_search'] is True
    assert mem['castle_jump_skip']['mode'] == 'reanchor'
    assert M._start_reanchor_jump(mem) is None
    mem['tick'] += M.CASTLE_JUMP_COOLDOWN
    assert M._start_reanchor_jump(mem) == [M.pad('x')]


def _monthly_reanchor(stage, **fields):
    """Real month owner launches recovery, then receives its own map again."""
    from docich.hanjuku.flows import post_month_status as post

    mem = _memory()
    mem.update(month='2-6', nav_search=True, uncertain=True)
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage=stage, **fields)
    screen = _screen('map', [], cursor=(80, 90))
    assert post.step(screen, mem, None) == [M.pad('x')]
    assert mem['castle_jump']['return_to']['owner'] == 'post_month_status'
    assert mem['castle_jump']['return_to']['stage'] == stage
    mem['castle_jump']['phase'] = 'confirm'
    assert M.castle_jump_step(screen, mem, None) is None
    assert mem['reanchor_resume']['owner'] == 'post_month_status'
    assert mem['reanchor_resume']['stage'] == stage
    return mem, screen, state


def test_reanchor_resumes_month_status_without_replaying_old_selection():
    mem, screen, state = _monthly_reanchor(
        'status', selected='ゼウス', names=['どうし'], list_moves=1,
        top_confirmed=True, dirty=['ゼウス'],
        statuses={'どうし': {'location': 'castle', 'castle': 'アルマムーン'}})
    # SELECT recovery lands at home, so the status panel cannot be
    # interpreted as the old general.  Reopen X and rescan membership.
    actions = M.resume_reanchored_owner(screen, mem, None)
    assert actions == [M.pad('x')]
    assert state['stage'] == 'open_menu'
    assert state['names'] == []
    assert state['top_confirmed'] is False
    assert state['statuses']['どうし']['castle'] == 'アルマムーン'
    assert state['dirty'] == ['ゼウス']
    assert mem['_records'][-1]['decision'] == 'post_month_reanchor_reenter'
    M.note_reanchor_followup(screen, mem, actions)
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_resumed'
    assert mem['_records'][-1]['observed_metric']['owner'] == 'post_month_status'
    assert 'reanchor_resume' not in mem


def test_reanchor_month_task_jump_reselects_named_general_before_castle_a():
    job = {'kind': 'locate', 'name': 'ゼウス'}
    mem, screen, state = _monthly_reanchor(
        'task_jump', job=job, tasks=[],
        statuses={'ゼウス': {'location': 'castle'}})
    # The cursor is now at home, NOT the position of ゼウス.  Never use
    # task_jump's old map -> A shortcut on the recovered frame.
    assert M.resume_reanchored_owner(screen, mem, None) == [M.pad('x')]
    assert state['stage'] == 'task_menu'
    assert state['job'] == job
    assert state['statuses']['ゼウス']['location'] == 'castle'


def test_reanchor_owner_receipt_requires_owner_action_not_unrelated_input():
    mem, screen, state = _monthly_reanchor('roster', names=['どうし'])
    M.note_reanchor_followup(screen, mem, [M.pad('b')])
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_wait'
    assert mem['_records'][-1]['observed_metric']['blocked_by'] == 'post_month_status:roster'
    assert mem['reanchor_resume']['owner'] == 'post_month_status'


def test_reanchor_discards_old_owner_when_month_scope_changes():
    mem, screen, state = _monthly_reanchor('status')
    mem['month'] = '2-7'
    assert M.resume_reanchored_owner(screen, mem, None) is None
    assert 'reanchor_resume' not in mem
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_owner_expired'
    assert state['stage'] == 'status'


def test_reanchor_sortie_is_not_stolen_by_background_monthly_owner(monkeypatch):
    mem, screen = _confirm_reanchor_with_active_order()
    mem['post_month_status'] = {'stage': 'blocked'}
    _allow_interrupted_order(monkeypatch)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: [M.pad('right')])
    actions = M.resume_reanchored_owner(screen, mem, None)
    assert actions == [M.pad('right')]
    M.note_reanchor_followup(screen, mem, actions)
    assert mem['_records'][-1]['observed_metric']['owner'] == 'sortie_plan'


def test_reanchor_does_not_replay_sortie_replaced_during_jump():
    mem = _memory()
    mem['nav_search'] = True
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    mem['active'] = 'OTHER'
    mem['castle_jump']['phase'] = 'confirm'
    screen = _screen('map', [], cursor=(80, 90))
    assert M.castle_jump_step(screen, mem, None) is None
    assert mem['reanchor_resume']['active_before'] == 'J1'
    assert M.resume_reanchored_owner(screen, mem, None) is None
    assert 'reanchor_resume' not in mem
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_owner_expired'


# --- blinking menu hand (g1139 2026-11-07) --------------------------------
def _blind_main_menu():
    rows = [(39, 168, 'ステータス'), (55, 168, 'しょうぐん'), (71, 168, 'しろ'),
            (87, 168, 'きりふだ'), (103, 168, 'システム')]
    return _screen('main_menu', rows, hand=None,
                   text='ステータス しょうぐん しろ きりふだ システム')


def _blind_castle_list(names):
    rows = [(y, 168, name) for y, name in zip(range(39, 39 + 16 * len(names), 16), names)]
    rows += [(183, 24, 'Aボタンでステータスひょうじ'),
             (199, 24, 'コマンドにもどりますじゃ!')]
    return _screen('text', rows, hand=None,
                   text='Aボタンでステータスひょうじ Bボタンで コマンドにもどりますじゃ!')


def _jump(phase, **fields):
    jump = {'step': 'J1', 'source': 'アルマムーン', 'phase': phase,
            'age': 0, 'total': 0, 'tick': 0, 'menu_presses': 1}
    jump.update(fields)
    return jump


def test_blinking_hand_holds_the_main_menu_instead_of_cancelling():
    mem = _memory()
    mem['castle_jump'] = _jump('menu')
    # A live frame can catch the hand in its hidden phase while every row is
    # drawn; the jump must wait for a readable frame, not abort.
    assert M.castle_jump_step(_blind_main_menu(), mem, None) == []
    assert mem['castle_jump']['phase'] == 'menu'
    assert mem['castle_jump']['blind'] == 1
    assert not [r for r in mem['_records'] if r['decision'] == 'castle_jump_failed']
    # The next observation shows the hand: the same trip continues.
    assert M.castle_jump_step(_main_menu(hand_y=39), mem, None) == [M.pad('down')]
    assert mem['castle_jump']['blind'] == 0


def test_hidden_hand_gives_up_only_after_the_bounded_wait():
    mem = _memory()
    mem['castle_jump'] = _jump('menu')
    screen = _blind_main_menu()
    for _ in range(M.CASTLE_JUMP_CURSOR_BLIND):
        assert M.castle_jump_step(screen, mem, None) == []
    actions = M.castle_jump_step(screen, mem, None)
    assert actions == [M.pad('b')]
    assert 'castle_jump' not in mem
    assert mem['castle_jump_skip']['step'] == 'J1'
    failed = [r for r in mem['_records'] if r['decision'] == 'castle_jump_failed']
    assert failed and failed[-1]['observed_metric']['why'] == 'hand_hidden'


def test_blinking_hand_on_the_castle_list_holds_then_selects():
    mem = _memory()
    mem['castle_jump'] = _jump('list', menu_presses=2)
    assert M.castle_jump_step(_blind_castle_list(['アルマームン']), mem, None) == []
    assert mem['castle_jump']['phase'] == 'list'
    assert mem['castle_jump']['blind'] == 1
    # The hand returns on the row naming the source: SELECT flies there.
    assert M.castle_jump_step(_castle_list(['アルマームン']), mem, None) == [M.pad('select')]
    assert mem['castle_jump']['phase'] == 'confirm'
    assert mem['castle_jump']['blind'] == 0


def test_fade_frame_before_the_menu_holds_and_continues():
    mem = _memory()
    mem['castle_jump'] = _jump('menu')
    # X fades the map into the menu; the frame in between reads as nothing.
    assert M.castle_jump_step(_screen('unknown', []), mem, None) == []
    assert mem['castle_jump']['phase'] == 'menu'
    assert mem['castle_jump']['fade'] == 1
    assert M.castle_jump_step(_main_menu(hand_y=39), mem, None) == [M.pad('down')]
    assert mem['castle_jump']['fade'] == 0


def test_fade_frames_end_the_trip_when_the_menu_never_appears():
    mem = _memory()
    mem['castle_jump'] = _jump('menu')
    screen = _screen('unknown', [])
    for _ in range(M.CASTLE_JUMP_FADE_BLIND):
        assert M.castle_jump_step(screen, mem, None) == []
    assert M.castle_jump_step(screen, mem, None) == []
    assert 'castle_jump' not in mem
    assert mem['castle_jump_skip']['step'] == 'J1'
    failed = [r for r in mem['_records'] if r['decision'] == 'castle_jump_failed']
    assert failed and failed[-1]['observed_metric']['why'] == 'unexpected_menu'


# --- long castle rosters (g1133 ユージア, g1163 アブダレ) -------------------
CHAPTER_6_LONG = [
    'アルマムーン', 'アルブラーカ', 'イカリア', 'カバルッサ', 'オケアナ',
    'バビラリー', 'ダーラント', 'セレーネ', 'グルーポフ', 'ユージア',
    'アブダレ', 'トウトウ',
]


def _castle_list_window(names, offset, selected):
    """An eight-row page of the しろ list with the hand on the selected row."""
    hand_y = 39 + 16 * (selected - offset)
    return _castle_list(names[offset:offset + 8], hand_y=hand_y)


def test_last_page_source_is_reached_past_the_old_eight_row_bound():
    """g1163 2026-11-09: the list stopped at row 8, so アブダレ (row 10) failed.

    The list opens on the home castle at row 0 and shows eight rows. Reaching
    row 10 needs ten Downs and one page shift; the old bound of eight ended
    every trip with ``castle_missing`` before the row could appear.
    """
    mem = _memory()
    mem['castle_jump'] = _jump('list', menu_presses=2, source='アブダレ')
    selected = offset = downs = 0
    for _ in range(20):
        screen = _castle_list_window(CHAPTER_6_LONG, offset, selected)
        actions = M.castle_jump_step(screen, mem, None)
        if actions == [M.pad('select')]:
            break
        assert actions == [M.pad('down')]
        downs += 1
        selected = min(selected + 1, len(CHAPTER_6_LONG) - 1)
        if selected - offset >= 8:
            offset = selected - 7   # native eight-row window scrolls at the bottom
    else:
        raise AssertionError('the source row was never reached')
    assert downs == 10
    assert mem['castle_jump']['phase'] == 'confirm'
    assert not [r for r in mem['_records'] if r['decision'] == 'castle_jump_failed']


def test_scroll_bound_covers_the_longest_chapter_roster():
    longest = max(len(names) for names in M.chart.CASTLE_NAMES.values())
    assert M.CASTLE_JUMP_SCROLL_LIMIT >= longest - 1


def test_a_replan_does_not_abort_the_trip_to_the_source_castle():
    """Five jumps died at age 1 with order_changed (g1163 2026-11-10 05:38).

    The chart worker renumbers every step when it re-plans, but the walk to the
    same departure castle for the same target by the same general is still the
    right physical action.
    """
    from docich.hanjuku.flows import map as map_flow

    old = {'step': 'A:old:M1', 'general': 'シュゼット', 'source': 'あるまむーん',
           'target': 'しまばら'}
    new = {'step': 'A:new:M1', 'general': 'シュゼット', 'source': 'あるまむーん',
           'target': 'しまばら'}
    mem = {'chapter': 8, 'active': 'A:new:M1', 'orders': {'A:new:M1': 'pending'},
           'chart_plan': {'orders': [new]}, 'sorties': {}, 'world': {}}
    assert map_flow._jump_successor(mem, old) is new
    other_target = dict(new, target='ひめじ')
    mem2 = dict(mem, chart_plan={'orders': [other_target]})
    assert map_flow._jump_successor(mem2, old) is None
    other_general = dict(new, general='アテナ')
    mem3 = dict(mem, chart_plan={'orders': [other_general]})
    # A re-plan may pick another actor for the same castle trip; the walk is
    # still the right action (a substitute may depart from there).
    assert map_flow._jump_successor(mem3, old) is other_general


def test_a_jump_survives_its_step_disappearing_from_the_plan():
    """The jump died at age 1 whenever a re-plan dropped its step (2026-11-10).

    The jump itself keeps the departure castle and target, so the trip can
    continue to the new active order for the same castle.
    """
    from docich.hanjuku.flows import map as map_flow

    active = {'step': 'A:new:M1', 'general': 'マンゴスチン', 'source': 'あるまむーん',
              'target': 'しまばら'}
    mem = {'chapter': 8, 'active': 'A:new:M1', 'orders': {'A:new:M1': 'pending'},
           'chart_plan': {'orders': [active]}, 'sorties': {}, 'world': {}}
    jump = {'step': 'A:old:M1', 'source': 'あるまむーん', 'target': 'しまばら'}
    assert map_flow._jump_successor(mem, None, jump) is active
    jump_other = dict(jump, target='ひめじ')
    assert map_flow._jump_successor(mem, None, jump_other) is None
