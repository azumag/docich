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
from docich.hanjuku.state.regions import plan as _plan_region


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
    order = _plan_region(mem)['chart_plan']['orders'][0]
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
    _plan_region(mem)['active'] = None
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
    _plan_region(mem)['active'] = None
    mem['nav_search'] = True
    mem['uncertain'] = True
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    mem['castle_jump']['phase'] = 'confirm'
    screen = _screen('map', [], cursor=(80, 90))
    assert M.castle_jump_step(screen, mem, None) is None

    _allow_interrupted_order(monkeypatch)
    # A real executable order contains the planned general; the lightweight
    # castle-jump-only fixture intentionally omits it.
    order = {**_plan_region(mem)['chart_plan']['orders'][0], 'general': 'どうし'}
    monkeypatch.setattr(M, '_world_map_wanted', lambda _mem: False)
    monkeypatch.setattr(M, 'next_order', lambda _mem: order)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: [M.pad('right')])
    actions = M.resume_reanchored_owner(screen, mem, None)
    assert actions == [M.pad('right')]
    assert _plan_region(mem)['active'] == 'J1'
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
    _plan_region(mem)['active'] = None
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
    order = {**_plan_region(mem)['chart_plan']['orders'][0], 'general': 'どうし'}
    monkeypatch.setattr(M, 'next_order', lambda _mem: order)
    monkeypatch.setattr(M, 'nav_step', lambda *_args, **_kwargs: None)
    assert M.map_step(screen, mem, None) == []
    mem['tick'] += 1
    assert M.map_step(screen, mem, None) == [M.pad('x')]
    assert _plan_region(mem)['active'] == 'J1'
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
    _plan_region(mem)['active'] = 'OTHER'
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


def test_an_empty_active_order_does_not_end_the_trip():
    """Between plans the active order is briefly empty (g1163 2026-11-10 06:40)."""
    from docich.hanjuku.flows import map as map_flow

    mem = {'chapter': 8, 'active': None, 'orders': {}, 'chart_plan': {'orders': []},
           'sorties': {}, 'world': {},
           'castle_jump': {'step': 'A:old:M1', 'source': 'あるまむーん',
                           'target': 'しまばら', 'phase': 'menu', 'age': 0, 'total': 0,
                           'menu_presses': 1}}
    screen = type('Screen', (), {'kind': 'unknown', 'hand': None, 'lines': [], 'text': ''})()
    out = map_flow.castle_jump_step(screen, mem, None)
    assert out == [] and mem['castle_jump']['orphan'] == 1


def test_an_unresolvable_active_step_holds_the_trip():
    """The active step survived a re-plan as a bare id (g1163 07:01)."""
    from docich.hanjuku.flows import map as map_flow

    mem = {'chapter': 8, 'active': 'A:ghost:M1', 'orders': {}, 'chart_plan': {'orders': []},
           'sorties': {}, 'world': {},
           'castle_jump': {'step': 'A:old:M1', 'source': 'あるまむーん',
                           'target': 'しまばら', 'phase': 'menu', 'age': 0, 'total': 0,
                           'menu_presses': 1}}
    screen = type('Screen', (), {'kind': 'unknown', 'hand': None, 'lines': [], 'text': ''})()
    assert map_flow.castle_jump_step(screen, mem, None) == []
    assert mem['castle_jump']['orphan'] == 1


# ---------------------------------------------------------------- v648
# A source castle whose read general list holds the hero is reached with a
# field SELECT alone (g1163 2026-11-10 23:20: X, three Downs, A and SELECT
# every sortie from あるまむーん, about 11 s, to reach the hero's castle).

def _hero_home_memory(monkeypatch, order=None):
    mem = _memory(order)
    monkeypatch.setattr(M, '_hero_castle', lambda _mem: 'アルマムーン')
    return mem


def test_hero_castle_source_jumps_with_select_only(monkeypatch):
    mem = _hero_home_memory(monkeypatch)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    assert M._start_castle_jump(mem, order, order['source']) == [M.pad('select')]
    jump = mem['castle_jump']
    assert jump['mode'] == 'hero_select' and jump['phase'] == 'confirm'
    record = mem['_records'][-1]
    assert record['decision'] == 'castle_jump_open'
    assert record['strategy_variant'] == 'hero_select'


def test_other_source_keeps_the_named_shiro_list(monkeypatch):
    mem = _hero_home_memory(monkeypatch, _order(source='ナキューメラ'))
    order = _plan_region(mem)['chart_plan']['orders'][0]
    assert M._start_castle_jump(mem, order, 'ナキューメラ') == [M.pad('x')]
    assert mem['castle_jump']['phase'] == 'menu'
    assert 'mode' not in mem['castle_jump']


def test_hero_away_keeps_the_named_shiro_list(monkeypatch):
    mem = _memory()
    monkeypatch.setattr(M, '_hero_castle', lambda _mem: None)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    assert M._start_castle_jump(mem, order, order['source']) == [M.pad('x')]


def test_source_mismatch_on_the_step_falls_back_to_the_list(monkeypatch):
    mem = _hero_home_memory(monkeypatch)
    mem['source_miss'] = {'J1': 1}
    order = _plan_region(mem)['chart_plan']['orders'][0]
    assert M._start_castle_jump(mem, order, order['source']) == [M.pad('x')]


def test_first_map_frame_after_field_select_is_not_trusted(monkeypatch):
    mem = _hero_home_memory(monkeypatch)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    M._start_castle_jump(mem, order, order['source'])
    screen = _screen('map', [], cursor=(80, 90))
    # The first reading may still be the camera before SELECT moved it.
    assert M.castle_jump_step(screen, mem, None) == []
    assert 'expect_menu' not in mem
    # The settled field return opens the castle; deploy checks its name.
    assert M.castle_jump_step(screen, mem, None) == [M.pad('a')]
    assert 'castle_jump' not in mem and mem['expect_menu'] is True
    confirm = mem['_records'][-1]
    assert confirm['decision'] == 'castle_jump_confirm'
    assert confirm['strategy_variant'] == 'hero_select'


def test_fade_frame_then_map_opens_the_castle(monkeypatch):
    mem = _hero_home_memory(monkeypatch)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    M._start_castle_jump(mem, order, order['source'])
    assert M.castle_jump_step(_screen('unknown', []), mem, None) == []
    assert M.castle_jump_step(_screen('map', [], cursor=(80, 90)), mem, None) == [M.pad('a')]


def test_panel_after_field_select_backs_out_and_uses_the_list_next(monkeypatch):
    mem = _hero_home_memory(monkeypatch)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    M._start_castle_jump(mem, order, order['source'])
    assert M.castle_jump_step(_screen('main_menu', []), mem, None) == [M.pad('b')]
    assert 'castle_jump' not in mem
    assert 'J1' in mem['hero_select_miss']
    # No cooldown: the next attempt walks the named list at once.
    assert 'castle_jump_skip' not in mem
    assert M._start_castle_jump(mem, order, order['source']) == [M.pad('x')]


def test_no_field_return_gives_up_the_select_within_bound(monkeypatch):
    mem = _hero_home_memory(monkeypatch)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    M._start_castle_jump(mem, order, order['source'])
    unknown = _screen('unknown', [])
    for _ in range(M.HERO_SELECT_WAIT_LIMIT - 1):
        assert M.castle_jump_step(unknown, mem, None) == []
        assert 'castle_jump' in mem
    assert M.castle_jump_step(unknown, mem, None) == []
    assert 'castle_jump' not in mem
    assert mem['_records'][-1]['observed_metric']['why'] == 'hero_select_no_field'
    assert M._start_castle_jump(mem, order, order['source']) == [M.pad('x')]


def test_hero_select_miss_is_bounded(monkeypatch):
    mem = _memory()
    for i in range(M.HERO_SELECT_MISS_KEEP + 5):
        mem['tick'] = i
        M._mark_hero_select_miss(mem, f'S{i}')
    assert len(mem['hero_select_miss']) == M.HERO_SELECT_MISS_KEEP
    assert f'S{M.HERO_SELECT_MISS_KEEP + 4}' in mem['hero_select_miss']
    assert 'S0' not in mem['hero_select_miss']


def test_real_garrison_with_the_hero_selects_the_home_castle():
    from docich.hanjuku.state.regions import world
    mem = _memory()
    world(mem)['garrison'] = {'アルマムーン': [M.NAME, 'ゼウス']}
    mem['castle_owners'] = {'アルマムーン': 'own'}
    order = _plan_region(mem)['chart_plan']['orders'][0]
    assert M._hero_castle(mem) == 'アルマムーン'
    assert M._start_castle_jump(mem, order, order['source']) == [M.pad('select')]


# ---------------------------------------------------------------- v650
def test_named_list_jump_moves_the_cursor_estimate_to_the_source():
    mem = _memory(_order(source='ナキューメラ'))
    mem['castle_jump'] = {'step': 'J1', 'source': 'ナキューメラ', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    assert M.castle_jump_step(_screen('map', [], cursor=(80, 90)), mem, None) == [M.pad('a')]
    assert mem['cursor'] == list(M.chart.castles(1)['ナキューメラ'])
    assert mem['uncertain'] is False


def test_named_list_confirm_keeps_the_policy_variant_in_the_log():
    mem = _memory()
    mem['variant'] = 'chart_adjusted'
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    M.castle_jump_step(_screen('map', [], cursor=(80, 90)), mem, None)
    assert mem['_records'][-1]['strategy_variant'] == 'chart_adjusted'


def test_a_named_row_select_marks_the_source_verified():
    """g1163 2026-11-12 23:1x: every sortie re-read ステータス for the same name."""
    from docich.hanjuku.flows import deploy
    from docich.hanjuku.os_layer.screen import Screen

    mem = {'chapter': 9, 'tick': 5000, '_records': [], 'orders': {},
           'castle_jump': {'step': 'I:c9:A>B:1', 'source': 'A', 'phase': 'field',
                           'selects': 1, 'age': 1}}
    mem['castle_verified'] = 'I:c9:A>B:1'
    order = {'step': 'I:c9:A>B:1', 'source': 'A', 'target': 'B', 'cards': [],
             'general': '—', 'purpose': 'move', 'corridor': True}
    screen = Screen([], None, '', kind='castle_menu')     # no status text yet
    assert deploy._check_source_castle(screen, mem, order) is None


def test_a_castle_jump_field_frame_records_the_visible_camps(monkeypatch):
    """The jump frames are field frames; the camp count is the evidence."""
    mem = _hero_home_memory(monkeypatch)
    order = _plan_region(mem)['chart_plan']['orders'][0]
    M._start_castle_jump(mem, order, order['source'])
    screen = _screen('map', [], cursor=(80, 90))
    M.castle_jump_step(screen, mem, None)
    assert M.castle_jump_step(screen, mem, None) == [M.pad('a')]
    confirm = mem['_records'][-1]
    assert confirm['observed_metric']['camps'] == 0


def test_a_visible_camp_takes_the_castle_jump_field_frame(monkeypatch):
    """g1163 2026-11-14 05:28: camps=1 but the jump's A took the field frame."""
    from docich.hanjuku.os_layer.pixels import Frame
    from docich.hanjuku.flows import recall as recall_flow
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('map', [], cursor=(80, 90))
    monkeypatch.setattr(M, 'own_camps', lambda frame: [{'target': (10, 10)}])
    monkeypatch.setattr(recall_flow, 'camp_recall_step',
                        lambda s, m, f: [M.pad('select')])
    frame = Frame(256, 224, b'\x00' * 256 * 224 * 3)
    assert M.castle_jump_step(screen, mem, frame) == [M.pad('select')]
    assert 'castle_jump' in mem


def test_a_visible_camp_under_the_skip_is_recorded(monkeypatch):
    """The camp is visible but the failure skip holds it back: say so."""
    from docich.hanjuku.os_layer.pixels import Frame
    from docich.hanjuku.flows import recall as recall_flow
    mem = _memory()
    mem['castle_jump'] = {'step': 'J1', 'source': 'アルマムーン', 'phase': 'confirm',
                          'age': 0, 'total': 0, 'tick': 0}
    screen = _screen('map', [], cursor=(80, 90))
    monkeypatch.setattr(M, 'own_camps', lambda frame: [{'target': (10, 10)}])
    monkeypatch.setattr(recall_flow, '_filter_recall_skip', lambda m, camps: [])
    monkeypatch.setattr(recall_flow, 'camp_recall_step', lambda s, m, f: None)
    frame = Frame(256, 224, b'\x00' * 256 * 224 * 3)
    assert M.castle_jump_step(screen, mem, frame) == [M.pad('a')]
    confirm = mem['_records'][-1]
    assert confirm['observed_metric']['camp_skipped'] is True
