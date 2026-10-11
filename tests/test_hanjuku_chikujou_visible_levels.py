"""Already-maxed visible rows must not block the hidden fifth castle."""
from dataclasses import replace
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_font import TextLine
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chikujou_scroll import holdings, NAMES
from test_hanjuku_multi_chikujou import castles, open_build


def native():
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g684-chikujou-four-maxed.png')
    assert frame.digest() == '6ad02099720259888efe3deb8c3e7778cfa784a0fc401c7618e72624e44784fa'
    return parse(frame)


def test_native_four_maxed_rows_scroll_without_selecting_another_unavailable_castle():
    mem = holdings(NAMES[:5], key='3-12')
    mem['month'] = '3-12'
    sub = open_build(mem, 34)
    assert p.month_sub_step(native(), mem) == [p.pad('down')]
    assert sub['rows_tried'] == list(NAMES[:4])
    assert not sub.get('chosen') and not sub.get('declined')
    assert mem['_records'][-1]['decision'] == 'chikujou_scroll'
    assert not mem['shop'].get('chikujou_upgrades')


def test_hidden_lower_level_castle_remains_eligible_after_the_visible_maxed_rows():
    mem = holdings(NAMES[:5], key='3-12')
    mem['month'] = '3-12'
    sub = open_build(mem, 34)
    assert p.month_sub_step(native(), mem) == [p.pad('down')]
    screen = castles(names=NAMES[1:5], on=NAMES[4])
    for i, name in enumerate(NAMES[1:5]):
        screen.lines.append(TextLine(39 + i * 16, ((232, '1' if name == NAMES[4] else '5'),)))
    assert p.month_sub_step(screen, mem) == [p.pad('a')]
    assert sub['chosen'] == NAMES[4]


def test_hotloaded_selection_of_a_visible_level_five_does_not_wait_on_identical_refusal():
    mem = holdings(NAMES[:5], key='3-12')
    mem['month'] = '3-12'
    sub = open_build(mem, 34)
    screen = native()
    sub.update(chosen=NAMES[0], rejected_frame=screen.text)
    assert p.month_sub_step(screen, mem) == [p.pad('down')]
    assert sub['rows_tried'] == list(NAMES[:4])
    assert not sub.get('chosen') and not sub.get('declined')


@pytest.mark.parametrize('bad', ['cursor', 'column', 'unknown_level', 'duplicate_level'])
def test_ambiguous_level_or_selection_cannot_remove_visible_candidates(bad):
    screen = native()
    if bad == 'cursor':
        screen.hand = None
    elif bad == 'column':
        screen.lines = [replace(l, cells=tuple((x - 8 if x == 232 else x, c) for x, c in l.cells))
                        for l in screen.lines]
    elif bad == 'unknown_level':
        screen.lines = [replace(l, cells=tuple((x, '?' if x == 232 else c) for x, c in l.cells))
                        if l.y == 39 else l for l in screen.lines]
    else:
        screen.lines.append(TextLine(39, ((232, '4'),)))
    assert p._chikujou_visible_maxed(screen, {'chapter': 1}) == []


def test_maxed_rows_are_not_saved_as_permanent_castle_caps():
    mem = holdings(NAMES[:5], key='3-12')
    mem['month'] = '3-12'
    sub = open_build(mem, 34)
    p.month_sub_step(native(), mem)
    assert sub['rows_tried'] == list(NAMES[:4])
    # A recaptured castle can be level 1 in the next month's fresh state.
    fresh = holdings(NAMES[:5], key='4-1')
    fresh['month'] = '4-1'
    new_sub = open_build(fresh, 34)
    assert p.month_sub_step(castles(names=NAMES[:4]), fresh) == [p.pad('a')]
    assert new_sub['chosen'] == NAMES[0]
