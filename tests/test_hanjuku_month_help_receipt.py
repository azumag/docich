"""g684 2-6 14:24:52: 130G -> 80G after a 50G recovery, but the month menu's
しょうにん help line counted as an open dialogue, so the egg stayed
'unverified' and recruitment was deferred again."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID


def menu(gold, on='しょうにん', help_text='きりふだをうる しょうにんを よびつけますぞ!'):
    c = Canvas()
    c.text(48, 15, f'2ねん 6のつき {gold}G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    x, y = MONTH_GRID[on]
    c.hand(x - 22, y - 6)
    if help_text:
        c.text(24, 183, help_text)
    return parse(c.frame())


def finish(screen):
    sub = {'kind': 'egg', 'gold_before': 130, 'quoted_cost': 50, 'full_selected': True,
           'left_menu': True, 'key': '2-6', 'presses': 25}
    mem = {'month': '2-6', 'chapter': 1, 'month_sub': sub, 'shop': {'key': '2-6', 'egg': 'opened'}}
    assert p._finish_month_sub(screen, mem, mem['shop']) is True
    return mem['shop']['egg']


def test_the_selected_rows_help_line_does_not_void_the_receipt():
    screen = menu(80)
    assert screen.kind == 'month_menu' and screen.selected == 'しょうにん'
    assert finish(screen) == 'done'


@pytest.mark.parametrize('help_text', ['それではこうこくをうたせていただきます。', 'たまごを かいふく しますぞ'])
def test_any_other_dialogue_still_leaves_the_receipt_unverified(help_text):
    assert finish(menu(80, help_text=help_text)) == 'unverified'


def test_a_wrong_debit_is_still_unverified():
    assert finish(menu(90)) == 'unverified'
