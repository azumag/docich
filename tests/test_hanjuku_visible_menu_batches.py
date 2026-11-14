"""Bounded paths in each measured pane; only the next real cursor may confirm."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from docich.hanjuku.flows import egg_recovery, house, recall, starter
from docich.hanjuku.os_layer import commands
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from test_hanjuku_fort_camp import fort_recall, memory as fort_memory, screen as fort_screen
from test_hanjuku_house import roster
from test_hanjuku_menu_cursor_tempo import entry, state
from test_hanjuku_partial_egg_recovery import memory as egg_memory
from test_hanjuku_starter import picture
from test_hanjuku_survey_cache import MAP, walked_large_roster
from test_hanjuku_visible_castle_batch import taps
from docich import hanjuku_roster as receipts

FIX = Path(__file__).parent / 'fixtures/hanjuku'
NAMES = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'チコリ', 'ビーツ', 'リーキ', 'アテナ']


def panel(menu):
    if menu == 'global_roster':
        return parse(roster(NAMES)), commands.roster_list_batch, NAMES[6]
    if menu == 'egg_recovery':
        screen = parse(read_png(FIX / 'g719-priest-list.png').resized())
        screen.hand = (10, 41, 30, 54)
        return screen, commands.egg_recovery_list_batch, 'アルファルファ'
    if menu == 'fort_roster':
        screen = fort_screen('g1097-probe-fort-list.png')
        # Only two native members are available. Extra visible rows here are
        # synthetic geometry coverage, not a native multiple-input receipt.
        line = next(l for l in screen.lines if l.y == 55)
        extra = replace(line, y=71, cells=tuple((144 + 8*i, ch) for i, ch in enumerate('ゼウス')))
        screen.lines.append(extra)
        return screen, commands.fort_list_batch, 'ゼウス'
    screen = parse(picture(('チコリ', 'ビーツ', 'リーキ', 'ゼウス')).frame())
    return screen, commands.starter_list_batch, 'ゼウス'


@pytest.mark.parametrize('menu,count', [('global_roster', 4), ('egg_recovery', 3),
                                      ('fort_roster', 2), ('starter_upper', 3)])
def test_each_visible_layout_batches_with_releases_and_current_feedback(menu, count):
    screen, batch, target = panel(menu)
    before = deepcopy(screen)
    actions = batch(screen, target)
    assert actions == taps('down', count)
    assert entry.observation_interval_ms(state(screen), actions) == 500
    assert screen == before
    assert entry.observation_interval_ms(state(screen), [commands.pad('a')]) == 1500
    assert entry.observation_interval_ms(state(screen), [commands.pad('b')]) == 1500
    assert entry.observation_interval_ms(state(screen), [commands.pad('down')]*count) == 1500


@pytest.mark.parametrize('menu', ['global_roster', 'egg_recovery', 'fort_roster', 'starter_upper'])
@pytest.mark.parametrize('change', ['no_hand', 'gap', 'duplicate', 'unknown', 'wrong_kind'])
def test_unread_ambiguous_or_wrong_pane_never_batches(menu, change):
    screen, batch, target = panel(menu)
    reading = commands.menu_navigation_reading(screen)
    x, y = reading['cursor']
    if change == 'no_hand':
        screen.hand = None
    elif change == 'wrong_kind':
        screen.kind = 'unknown'
    elif change == 'duplicate':
        screen.lines.append(next(l for l in screen.lines if dict(l.spans()).get(x) == target))
    else:
        next_y = 71 if menu == 'egg_recovery' else y + 16
        if change == 'gap':
            screen.lines = [l for l in screen.lines if l.y != next_y]
        else:
            screen.lines = [replace(l, cells=tuple((cx, UNKNOWN if cx == x else ch)
                              for cx, ch in l.cells)) if l.y == next_y else l for l in screen.lines]
    assert batch(screen, target) is None


def test_partial_survey_rechecks_actual_selection_before_opening_status():
    mem = walked_large_roster()
    receipts.dirty_status(mem, ['ゼウス'], fields=('egg',))
    assert house.step(MAP, mem, None) == [commands.pad('x')]
    mem['house']['phase'] = 'roster'
    from test_hanjuku_recruit_roster import NAMES as large_names
    names = list(large_names[:8])
    names[1], names[2] = names[2], names[1]
    assert house.step(parse(roster(names)), mem, None) == taps('down', 2)
    assert mem['house']['phase'] == 'roster'
    assert not mem['house'].get('selected')
    # One dropped arrow is corrected, never interpreted as target arrival.
    assert house.step(parse(roster(names, 1)), mem, None) == [commands.pad('down')]
    assert house.step(parse(roster(names, 2)), mem, None) == [commands.pad('a')]
    assert mem['house']['selected'] == 'ゼウス'
    assert mem['house']['phase'] == 'status'
    assert receipts.recheck_targets(mem) == ['ゼウス']


def test_egg_full_recovery_moves_up_across_the_nonuniform_first_gap_then_confirms():
    screen, batch, _ = panel('egg_recovery')
    screen.hand = (10, 97, 30, 110)
    mem = egg_memory(gold=9999)
    mem['shop']['reserve'] = 150
    actions = egg_recovery._egg_recovery_step(screen, mem, mem['month_sub'])
    assert actions == taps('up', 3)
    assert not mem['month_sub'].get('full_selected')
    screen.hand = (10, 41, 30, 54)
    assert egg_recovery._egg_recovery_step(screen, mem, mem['month_sub']) == [commands.pad('a')]
    assert mem['month_sub']['full_selected']
    assert not mem['month_sub'].get('quoted_cost')


def test_fort_actor_does_not_advance_stage_until_the_real_cursor_arrives():
    screen, _, target = panel('fort_roster')
    mem = fort_memory()
    sub = fort_recall(stage='fort_list', fort_actor=target)
    assert recall._fort_step(screen, mem, sub) == taps('down', 2)
    assert sub['stage'] == 'fort_list'
    assert 'general' not in sub
    screen.hand = (122, 65, 142, 78)
    assert recall._fort_step(screen, mem, sub) == [commands.pad('a')]
    assert sub['general'] == target and sub['stage'] == 'fort_status'


def test_starter_batches_count_each_press_and_keep_existing_retry_budget():
    screen, _, target = panel('starter_upper')
    mem = {'chapter': 1, 'tick': 100}
    sub = {}
    def move():
        return starter._navigation(mem, sub, commands.pad('down'), target,
                                   screen.hand, [], screen=screen)
    assert move() == taps('down', 3)
    assert sub['nav']['presses'] == sub['nav']['burst'] == 3
    for _ in range(starter.NAV_WAIT):
        assert move() == []
    assert move() == taps('down', 3)
    for _ in range(starter.NAV_WAIT):
        assert move() == []
    assert move() == taps('down', 3)
    assert move() == []
    assert sub['nav']['presses'] == starter.NAV_PRESS_LIMIT


def test_starter_keeps_one_press_when_only_one_remains_in_the_burst():
    screen, _, target = panel('starter_upper')
    sub = {'nav': {'signature': [target, list(screen.hand)], 'presses': 2, 'burst': 2, 'waits': 0}}
    assert starter._navigation({}, sub, commands.pad('down'), target, screen.hand, [],
                               screen=screen) == [commands.pad('down')]
    assert sub['nav']['presses'] == sub['nav']['burst'] == 3


def test_starter_step_requires_actual_upper_cursor_and_lower_selection_after_the_batch():
    names = ('チコリ', 'ビーツ', 'リーキ', 'ゼウス')
    mem = {'chapter': 1, 'tick': 100}
    frame = picture(names).frame()
    actions = starter.step(parse(frame), mem, frame)
    assert actions == taps('down', 3)
    assert mem['starter_party']['selected'] == []
    assert not mem['starter_party'].get('pending')
    frame = picture(names, on='ゼウス').frame()
    assert starter.step(parse(frame), mem, frame) == [commands.pad('a')]
    assert mem['starter_party']['pending'] == 'ゼウス'
    assert mem['starter_party']['selected'] == []


def test_global_full_survey_still_opens_the_actual_first_general_instead_of_batching():
    from test_hanjuku_house import memory
    mem = memory('roster')
    assert house.step(parse(roster(NAMES)), mem, None) == [commands.pad('a')]
    assert mem['house']['phase'] == 'status'
    assert mem['house']['selected'] == 'どうし'
