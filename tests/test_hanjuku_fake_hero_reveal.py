"""Native g700 input-stall regression; animated background is immaterial."""
from dataclasses import replace
from pathlib import Path

import pytest

from docich.hanjuku_bot import decide
from docich.hanjuku_font import TextLine
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import fake_hero_announcement_chapter, parse


def frame():
    return read_png(Path(__file__).parent / 'fixtures/hanjuku/g700-fake-hero-announcement.png').resized()


def test_native_reveal_confirms_even_with_previous_foreground_and_changing_hashes():
    native = frame()
    assert native.digest() == '9ac6aa2f702c267ef417f735790bfcc42179b87ec55265a37aab267700f1efba'
    screen = parse(native)
    assert screen.kind == 'text' and screen.header is None and screen.hand is None
    assert fake_hero_announcement_chapter(screen) == 2
    state = {'phase': 'field', 'no_input_streak': 1, 'no_input_frames': ['water-a', 'water-b'],
             'policy': {'chapter': 2, 'name': {'done': True},
                        'house': {'stage': 'travel'}, 'recall': {'stage': 'await_dispatch'},
                        'egg_menu_stage': 'choose',
                        'battle': {'ally': 'どうし', 'ally_hp': 90, 'enemy_hp': 0,
                                   'enemy': 'デーメーテール', 'castle': 'スペランザ',
                                   'side': 'attack', 'away': 1}}}
    actions, updated = decide(native, state)
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert updated['no_input_streak'] == 0 and updated['no_input_frames'] == []
    assert 'battle' not in updated['policy'] and 'egg_menu_stage' not in updated['policy']
    assert 'スペランザ' in updated['policy']['captured']
    assert any(r['decision'] == 'fake_hero_reveal' for r in updated['_records'])


def test_cold_resume_reveal_never_requires_old_roster_or_battle():
    actions, updated = decide(frame(), {'policy': {'chapter': 2, 'name': {'done': True}}})
    assert actions[0]['buttons'] == ['a']
    assert not updated['policy'].get('captured')
    assert not updated['policy'].get('battle')


def test_native_paused_slot_osd_never_weakens_chapter_evidence():
    native = read_png(Path(__file__).parent / 'fixtures/hanjuku/g704-resume-reveal-slot-osd.png').resized()
    assert native.digest() == '97897876700acf85183017d067e9cce624cdc4b4a40d93540434ecfe45840286'
    assert fake_hero_announcement_chapter(parse(native)) is None


@pytest.mark.parametrize('change', ['partial', 'wrong-row', 'unknown', 'other-text',
                                    'header', 'hand', 'other-kind', 'extra-row'])
def test_only_complete_native_reveal_is_a_chapter_cue(change):
    screen = parse(frame())
    nonempty = [line for line in screen.lines if line.y in (167, 183)]
    if change == 'partial': screen = replace(screen, lines=nonempty[:1])
    elif change == 'wrong-row': screen = replace(screen, lines=[replace(nonempty[0], y=159), nonempty[1]])
    elif change in {'unknown', 'other-text'}:
        cells = nonempty[1].cells
        line = replace(nonempty[1], cells=cells[:1] + ((cells[1][0], '�' if change == 'unknown' else 'お'),) + cells[2:])
        screen = replace(screen, lines=[nonempty[0], line])
    elif change == 'header': screen = replace(screen, header={'chapter': None})
    elif change == 'hand': screen = replace(screen, hand=(1, 2, 3, 4))
    elif change == 'other-kind': screen = replace(screen, kind='map')
    else: screen = replace(screen, lines=nonempty + [TextLine(199, ((72, 'よ'),))])
    assert fake_hero_announcement_chapter(screen) is None
