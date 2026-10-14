"""Synthetic native-panel regressions calibrated on passive battle captures."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_bot
from docich.hanjuku_font import read_lines, dark
from docich.hanjuku_screen import Battle, parse
from test_hanjuku_chart_bot import Canvas

INK = (32, 32, 32)
BACKGROUND = (238, 238, 238)


def panel(enemy_hp=32, ally_hp=90):
    c = Canvas(BACKGROUND)
    c.text(24, 176, 'ミント', color=INK)
    c.text(152, 176, 'どうし', color=INK)
    for hp, units in ((enemy_hp, 104), (ally_hp, 232)):
        c.text(units - 8 * (len(str(hp)) - 1), 176, str(hp), color=INK)
    return c


@pytest.mark.parametrize('enemy_hp,ally_hp', [(0, 90), (7, 0), (32, 90), (70, 82), (500, 100)])
def test_visible_right_aligned_hp_accepts_one_two_and_three_digits(enemy_hp, ally_hp):
    assert parse(panel(enemy_hp, ally_hp).frame()).battle == Battle('ミント', enemy_hp, 'どうし', ally_hp)


@pytest.mark.parametrize('x', [88, 96, 104, 216, 224, 232])
def test_sprite_colour_in_any_hp_cell_invalidates_panel(x):
    c = panel()
    # Measured green in the sprite covering the 3 of 32 (capture frame 0016).
    c.put(x, 179, (57, 165, 123))
    assert parse(c.frame()).battle is None


@pytest.mark.parametrize('x', [96, 232])
def test_unknown_digit_cannot_be_skipped_to_form_a_lower_hp(x):
    c = panel()
    # Destroy just one glyph while keeping the adjacent digit intact.
    for y in range(176, 184):
        for px in range(x, x + 8):
            c.put(px, y, INK)
    assert parse(c.frame()).battle is None


def test_a_missing_units_digit_is_not_a_one_digit_hp():
    c = panel()
    for y in range(176, 184):
        for x in range(232, 240):
            c.put(x, y, BACKGROUND)
    assert parse(c.frame()).battle is None  # 90 must not become 9 in the tens column


def test_fully_covered_tens_digit_cannot_become_a_valid_one_digit_hp():
    c = panel(32, 90)
    for y in range(176, 184):
        for x in range(96, 104):
            c.put(x, y, (57, 165, 123))
    frame = c.frame()
    row = next(l for l in read_lines(frame, predicate=dark, rect=(0, 160, 256, 200))
               if l.y == 176)
    assert row.words(0, 128)[-1] == '2'  # reproduces the old reader's false HP
    assert parse(frame).battle is None


def test_non_hp_sprite_colours_do_not_disable_a_clear_panel():
    c = panel()
    c.put(80, 170, (57, 165, 123))
    assert parse(c.frame()).battle == Battle('ミント', 32, 'どうし', 90)


def test_covered_hp_cannot_unlock_card_clash_or_false_hero_retreat(monkeypatch):
    monkeypatch.setattr(hanjuku_bot, 'classify', lambda f: 'battle')
    clear = panel()
    _, state = hanjuku_bot.decide(clear.frame(), {})
    _, state = hanjuku_bot.decide(clear.frame(), state)
    assert state['policy']['battle']['ally_hp'] == 90
    covered = panel()
    covered.put(232, 179, (57, 165, 123))
    actions, state = hanjuku_bot.decide(covered.frame(), state)
    assert actions == []
    assert state['policy']['battle']['ally_hp'] == 90
    assert not state['policy']['battle'].get('clashed')
    assert not state['policy']['battle'].get('hero_retreat')
    assert not any(r['decision'] == 'battle_hero_retreat_open' for r in state['_records'])


def test_digits_outside_the_measured_human_field_are_not_truncated():
    assert parse(panel(1234, 90).frame()).battle is None
