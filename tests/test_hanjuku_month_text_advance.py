"""1-11 (2026-10-11): after the おねだり gift a message box stayed over the
month menu; down and B changed nothing and the bot held for 150 s."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID

BODY = 'ありがとう! だいじにするね。'


def menu(on='しょうにん', body=BODY, choices=False):
    c = Canvas()
    c.text(48, 15, '1ねん 11のつき 163G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    if body:
        c.text(24, 183, body)
    if choices:
        c.text(184, 183, 'うむッ!')
        c.text(184, 199, 'いかんッ!')
    x, y = MONTH_GRID[on]
    c.hand(x - 22, y - 6)
    return parse(c.frame())


def stuck(mem, screen, label='へいしほじゅう'):
    """Drive the guarded navigation until it has cancelled and holds."""
    out = []
    for _ in range(policy.MENU_NAV_PRESS_LIMIT + 3):
        out.append(policy._month_menu_move(screen, mem, label))
    return out


def test_message_over_the_month_menu_is_advanced_with_a_once_navigation_is_stuck():
    screen = menu()
    assert screen.kind == 'month_menu' and policy._month_dialog_body(screen)
    mem = {'month': '1-11', '_records': []}
    moves = stuck(mem, screen)
    assert moves[:policy.MENU_NAV_PRESS_LIMIT] == [policy.pad('down')] * policy.MENU_NAV_PRESS_LIMIT
    assert moves[policy.MENU_NAV_PRESS_LIMIT] == [policy.pad('b')]
    assert moves[policy.MENU_NAV_PRESS_LIMIT + 1] == [policy.pad('a')]
    assert any(r['decision'] == 'month_text_advance' for r in mem['_records'])


def test_text_advance_is_bounded_per_message():
    screen = menu()
    mem = {'month': '1-11', '_records': []}
    presses = 0
    for _ in range(200):
        if policy._month_menu_move(screen, mem, 'へいしほじゅう') == [policy.pad('a')]:
            presses += 1
    assert presses == policy.MONTH_TEXT_ADVANCE_LIMIT


def test_plain_menu_without_text_still_holds():
    screen = menu(body=None)
    assert not policy._month_dialog_body(screen)
    mem = {'month': '1-11', '_records': []}
    assert stuck(mem, screen)[-1] == []


def test_a_choice_is_never_confirmed_by_the_advance():
    screen = menu(choices=True)
    mem = {'month': '1-11', '_records': []}
    assert all(m != [policy.pad('a')] for m in stuck(mem, screen))


def test_tracked_monthly_flows_keep_their_own_handling():
    screen = menu()
    mem = {'month': '1-11', '_records': [], 'month_sub': {'kind': 'egg'}}
    assert stuck(mem, screen)[-1] == []
