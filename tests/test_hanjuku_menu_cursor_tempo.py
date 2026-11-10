"""Cadence for actual list cursors; each move still needs a new observation."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from docich.hanjuku.os_layer.commands import menu_navigation_feedback, menu_navigation_reading, pad
from docich.hanjuku.os_layer.font import TextLine, UNKNOWN
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import Screen, parse
from docich.hanjuku_bot import decide
from test_hanjuku_house import roster
from test_hanjuku_starter import state as party_state

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures/hanjuku'
spec = importlib.util.spec_from_file_location('cursor_tempo_entry', ROOT / 'brains/hanjuku/bot.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def state(screen):
    reading = menu_navigation_reading(screen)
    return {'step': 8, 'screen_kind': screen.kind, 'policy': {},
            'menu_navigation_reading': {**reading, 'step': 8} if reading else None}


@pytest.mark.parametrize('name,menu', [
    ('g719-priest-list.png', 'egg_recovery'),
    ('g713-individual-priest-list.png', 'egg_recovery'),
    ('g719-priest-return-list.png', 'egg_recovery'),
    ('g1097-probe-fort-list.png', 'fort_roster'),
    ('g1097-fort-menu-over-dome.png', 'fort_commands'),
    ('g684-castora-kooskan-list.png', 'battle_cards'),
    ('g692-venus-artemis-kooskan-list.png', 'battle_cards'),
    ('g684-starter-empty.png', 'starter_upper'),
    ('g692-starter-one-selected.png', 'starter_upper'),
])
def test_native_menu_cursor_gets_feedback_in_its_own_pane(name, menu):
    frame = read_png(FIX / name).resized()
    screen = parse(frame, phase=classify(frame))
    reading = menu_navigation_reading(screen)
    assert reading is not None and reading['menu'] == menu
    before = deepcopy(screen)
    for direction in reading['directions']:
        assert entry.observation_interval_ms(state(screen), [pad(direction)]) == 500
    assert screen == before
    assert entry.observation_interval_ms(state(screen), [pad('a')]) == 1500
    assert entry.observation_interval_ms(state(screen), [pad('b')]) == 1500
    assert entry.observation_interval_ms(state(screen), []) == 1500


def test_global_roster_keeps_fast_single_row_feedback_through_the_bottom():
    # The last visible row can scroll. It must not fall back to 1.5 s merely
    # because the following row is not yet visible; do not infer a new member.
    names = ('どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'チコリ', 'ビーツ', 'リーキ', 'アテナ')
    for selected in range(8):
        screen = parse(roster(names=names, selected=selected))
        assert screen.kind == 'text'
        reading = menu_navigation_reading(screen)
        assert reading['menu'] == 'global_roster'
        assert reading['cursor'] == [168, 39 + 16 * selected]
        assert reading['directions'] == ['up', 'down']
        assert entry.observation_interval_ms(state(screen), [pad('down')]) == 500


def test_starter_dimmed_row_does_not_borrow_the_right_status_name():
    frame = read_png(FIX / 'g692-starter-one-selected.png').resized()
    actions, got = decide(frame, party_state())
    assert actions == [pad('down')]
    assert got['menu_navigation_reading']['cursor'] == [48, 47]
    assert entry.observation_interval_ms(got, actions) == 500
    assert got['policy']['starter_party']['selected'] == ['ゼウス']
    assert not got['policy']['starter_party'].get('confirm_sent')


@pytest.mark.parametrize('change', ['no_prompt', 'unknown_name', 'no_hand', 'wrong_pane'])
def test_roster_needs_its_prompt_clean_names_and_actual_hand(change):
    screen = parse(roster())
    if change == 'no_prompt':
        screen.text = ''
    elif change == 'unknown_name':
        screen.lines = [replace(line, cells=tuple((x, UNKNOWN if x == 168 else ch)
                                                 for x, ch in line.cells))
                        if line.y == 39 else line for line in screen.lines]
    elif change == 'no_hand':
        screen.hand = None
    else:
        screen.hand = (26, 33, 46, 46)
    assert menu_navigation_reading(screen) is None


def test_text_card_name_without_a_knight_cannot_enable_fast_feedback():
    screen = parse(read_png(FIX / 'g684-castora-kooskan-list.png'))
    screen.menu_cursor = None
    assert menu_navigation_reading(screen) is None


@pytest.mark.parametrize('value', [None, '', 'unknown', [], {'menu': 'global_roster'}])
def test_arbitrary_text_kind_without_a_valid_menu_discriminator_is_slow(value):
    reading = {'kind': 'text', 'menu': value, 'step': 8, 'cursor': [168, 39], 'directions': ['down']}
    s = {'screen_kind': 'text', 'step': 8, 'menu_navigation_reading': reading}
    assert not menu_navigation_feedback(s, [pad('down')])


@pytest.mark.parametrize('actions', [[pad('down'), pad('down')], [pad('down'), pad('a')],
    [{**pad('down'), 'hold_ms': 200}], [{**pad('down'), 'buttons': ['down', 'a']}]])
def test_new_menu_feedback_never_speeds_batches_long_holds_or_confirms(actions):
    screen = parse(roster())
    assert entry.observation_interval_ms(state(screen), actions) == 1500


def test_old_step_or_changed_screen_cannot_reuse_a_roster_reading():
    s = state(parse(roster()))
    s['step'] += 1
    assert not menu_navigation_feedback(s, [pad('down')])
    s['step'] -= 1
    s['screen_kind'] = 'unknown'
    assert not menu_navigation_feedback(s, [pad('down')])


@pytest.mark.parametrize('hand', [(146, 33), (146, 33, True, 46), 'unknown'])
def test_malformed_hand_does_not_create_a_navigation_reading(hand):
    screen = parse(roster())
    screen.hand = hand
    assert menu_navigation_reading(screen) is None


def test_cached_roster_walk_keeps_the_same_eight_single_row_inputs():
    from docich.hanjuku.flows import post_month_status as post
    from test_hanjuku_post_month_status import memory
    names = ('どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'チコリ', 'ビーツ', 'リーキ', 'アテナ')
    mem = memory(month='1-6')
    post.arm(mem)
    owner = mem['post_month_status']
    owner.update(stage='roster', statuses={name: {'location': 'castle'} for name in names})
    intervals = []
    for selected in range(8):
        frame = roster(names=names, selected=selected)
        screen = parse(frame)
        actions = post.step(screen, mem, frame)
        assert actions == [pad('down')]
        intervals.append(entry.observation_interval_ms(state(screen), actions))
        assert owner['names'] == list(names[:selected + 1])
        assert owner['stage'] == 'roster'  # never opens an individual status
    assert intervals == [500] * 8
    # Scheduling budget only; this does not simulate emulator response time.
    assert sum(intervals) == 4000  # previously eight 1500ms observations


def test_camp_commands_use_only_the_four_readable_rows():
    from test_hanjuku_house import Canvas
    c = Canvas()
    for i, label in enumerate(('いどう', 'ステータス', 'キャンプ', 'きかん')):
        c.text(64, 47 + i * 16, label)
    c.hand(42, 57)
    screen = parse(c.frame())
    screen.kind = 'text'
    reading = menu_navigation_reading(screen)
    assert reading['menu'] == 'camp_commands'
    assert reading['directions'] == ['up', 'down']
    assert entry.observation_interval_ms(state(screen), [pad('down')]) == 500
    assert entry.observation_interval_ms(state(screen), [pad('right')]) == 1500



def test_castle_roster_bottom_keeps_fast_feedback_without_inventing_a_next_member():
    from test_hanjuku_house import Canvas
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    c.text(144, 39, 'ゼウス')
    c.text(144, 55, 'ココット')
    c.hand(122, 49)
    screen = parse(c.frame())
    assert screen.kind == 'general_list'
    reading = menu_navigation_reading(screen)
    assert reading['menu'] == 'castle_roster'
    assert reading['cursor'] == [144, 55]
    assert entry.observation_interval_ms(state(screen), [pad('down')]) == 500
    assert entry.observation_interval_ms(state(screen), [pad('a')]) == 1500


def test_measured_sortie_card_picker_can_scroll_from_its_visible_bottom():
    screen = parse(read_png(FIX / 'g706-resume-card-select.png').resized())
    reading = menu_navigation_reading(screen)
    assert screen.kind == 'card_select'
    assert reading['menu'] == 'sortie_cards'
    assert reading['directions'] == ['up', 'down']
    assert entry.observation_interval_ms(state(screen), [pad('down')]) == 500
