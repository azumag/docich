"""A slot reel must be stopped, never chased as an Elabel menu."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import egg_choice_names, parse
from test_hanjuku_chart_bot import Canvas, egg_choice_frame

LIVE = ('メトロノーム', 'ハズレ', 'レッドドラゴン')
OTHER = ('ぼーぼーどり', 'ゲーラス', 'ガーコイル')


@pytest.mark.parametrize('cursor', [0, 1, 2, None])
def test_live_slot_reel_stops_without_navigating_even_when_cursor_blinks(cursor):
    frame = egg_choice_frame(LIVE, cursor=cursor)
    screen = parse(frame)
    assert screen.kind == 'egg_choice_menu'
    assert egg_choice_names(screen) == list(LIVE)
    actions, state = decide(frame, {'policy': {'chapter': 8}})
    assert actions == [policy.pad('a')]
    assert state['screen_kind'] == 'egg_choice_menu'
    row = state['_records'][-1]
    assert row['observed_metric']['mode'] == 'slot'
    assert 'choice' not in row['observed_metric']
    assert row['resulting_event'] == 'slot_stop_requested_not_yet_confirmed'


def test_actor_observed_slot_type_identifies_reel_with_only_monster_names():
    mem = {'battle': {'ally': 'バゲット'}, 'egg_types': {'バゲット': 'スロットエッグ'}}
    screen = parse(egg_choice_frame(OTHER, cursor=2))
    assert policy.egg_choice_step(screen, mem) == [policy.pad('a')]
    assert mem['egg_choice']['mode'] == 'slot'


@pytest.mark.parametrize('mem', [
    {},
    {'battle': {'ally': 'バゲット'}, 'egg_types': {'バゲット': 'エラベルエッグ'}},
    {'battle': {'ally': 'どうし'}, 'egg_types': {'バゲット': 'スロットエッグ'}},
])
def test_three_monster_rows_alone_or_another_actors_type_do_not_prove_slot(mem):
    screen = parse(egg_choice_frame(OTHER, cursor=2))
    assert policy.egg_choice_step(screen, deepcopy(mem)) == [policy.pad('up')]


def test_rotating_names_and_cursor_do_not_reset_stop_budget_or_lose_slot_mode():
    mem = {}
    actions = []
    for i in range(12):
        names = LIVE if i == 0 else (OTHER if i % 2 else OTHER[::-1])
        screen = parse(egg_choice_frame(names, cursor=i % 3))
        actions.append(policy.egg_choice_step(screen, mem))
    assert sum(action == [policy.pad('a')] for action in actions) == 2
    assert all(action in ([], [policy.pad('a')]) for action in actions)
    assert mem['_records'][-1]['decision'] == 'egg_choice_unconfirmed'


def test_old_elabel_hold_is_replaced_when_slot_is_first_proven():
    mem = {'egg_choice': {'names': list(LIVE), 'selected': 2, 'wait': 10, 'held': True}}
    assert policy.egg_choice_step(parse(egg_choice_frame(LIVE, cursor=1)), mem) == [policy.pad('a')]
    assert mem['egg_choice']['selected'] == 1


def test_unread_reel_frame_keeps_budget_but_a_settled_battle_exit_clears_it():
    _, state = decide(egg_choice_frame(LIVE), {'policy': {'chapter': 8}})
    _, state = decide(Canvas().frame(), state)
    assert state['policy']['egg_choice']['mode'] == 'slot'
    assert state['policy']['egg_choice']['selected'] == 1
    # A later reel contains only monster names but is the same episode.
    for _ in range(8):
        _, state = decide(egg_choice_frame(OTHER, cursor=2), state)
    assert state['policy']['egg_choice']['selected'] == 2
    from test_hanjuku_chart_bot import monster_menu_frame
    _, state = decide(monster_menu_frame(('たたく', 'へんしん'),
                                        ally=('エッグマン', 10), enemy=('クミン', 26)), state)
    assert 'egg_choice' not in state['policy']


@pytest.mark.parametrize('names,x', [
    (('メトロノーム', 'ハズレ'), 176),
    (('メトロノーム', 'ハズレ', 'しらない'), 176),
    (LIVE, 40),
])
def test_incomplete_unknown_or_misplaced_reels_do_not_authorise_stop(names, x):
    screen = parse(egg_choice_frame(names, x=x))
    mem = {'egg_choice': {'mode': 'slot', 'selected': 0, 'wait': 0}}
    assert policy.egg_choice_step(screen, mem) == []


def test_a_miss_only_reel_still_requests_stop():
    assert policy.egg_choice_step(parse(egg_choice_frame(('ハズレ',) * 3)), {}) == [policy.pad('a')]
