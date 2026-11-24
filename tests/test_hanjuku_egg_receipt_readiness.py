"""g700: the exact 100G recovery debit returned before the menu cursor."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID

FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/g700-egg-recovery-return-no-cursor.png'


def memory():
    return {'chapter': 2, 'month': '3-3', 'egg_uses': {'ココット': 2},
            'month_sub': {'kind': 'egg', 'gold_before': 134, 'quoted_cost': 100,
                          'full_selected': True, 'left_menu': True, 'key': '3-3', 'presses': 22, 'stage': 'recovering'},
            'shop': {'key': '3-3', 'egg': 'opened', 'egg_priority': True, 'reserve': 100,
                     'soldiers': 34, 'soldiers_done': False}}


def ready_menu():
    c = Canvas()
    c.text(48, 15, '3ねん 3のつき 34G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    x, y = MONTH_GRID['しょうにん']
    c.hand(x - 22, y - 6)
    return parse(c.frame())


def native_return():
    frame = read_png(FIXTURE).resized()
    assert hashlib.sha256(frame.rgb).hexdigest() == '6b7f6f750ab3aaecb15ef075b47a158a9d0f36b1b9475b309cf73d2d5fa44a2a'
    screen = parse(frame)
    assert screen.kind == 'month_menu' and screen.hand is None
    assert screen.header == {'year': 3, 'month': 3, 'gold': 34, 'chapter': None}
    return screen


def test_native_paid_return_waits_for_cursor_then_releases_only_the_paid_reserve():
    mem = memory()
    sub = mem['month_sub']
    assert p._finish_month_sub(native_return(), mem, mem['shop']) is False
    assert mem['month_sub'] is sub and mem['shop']['egg'] == 'opened'
    assert mem['egg_uses'] == {'ココット': 2} and mem['shop']['reserve'] == 100
    assert p._finish_month_sub(ready_menu(), mem, mem['shop']) is True
    assert 'month_sub' not in mem and mem['shop']['egg'] == 'done'
    assert mem['egg_uses'] == {} and mem['shop']['reserve'] == 0
    assert mem['shop']['soldiers'] == 34  # 84 - 50 repair, no wage hold
    receipt = mem['_records'][-1]['observed_metric']
    assert receipt['payment_verified'] is True and receipt['menu_ready'] is True


def test_a_cursor_that_never_returns_has_a_bounded_unverified_exit():
    mem = memory()
    screen = native_return()
    for _ in range(p.MONTH_SUB_MENU_WAIT - 1):
        assert p._finish_month_sub(screen, mem, mem['shop']) is False
    assert p._finish_month_sub(screen, mem, mem['shop']) is True
    assert 'month_sub' not in mem and mem['shop']['egg'] == 'unverified'
    assert mem['egg_uses'] == {'ココット': 2} and mem['shop']['reserve'] == 100
    receipt = mem['_records'][-1]['observed_metric']
    assert receipt['payment_verified'] is False and receipt['menu_ready'] is False


@pytest.mark.parametrize('changes', [
    {'full_selected': False}, {'left_menu': False}, {'aborted': True},
    {'payment_interrupted': True}, {'quoted_cost': 50}, {'quoted_cost': True},
    {'key': '3-2'}, {'gold_before': 133}, {'stage': 'choosing'},
])
def test_missing_or_conflicting_payment_evidence_cannot_gain_a_cursor_wait(changes):
    mem = memory()
    mem['month_sub'].update(deepcopy(changes))
    # The existing unchanged-menu wait still applies to left_menu=False.
    if changes == {'left_menu': False}:
        mem['month_sub']['menu_wait'] = p.MONTH_SUB_MENU_WAIT
    assert p._finish_month_sub(native_return(), mem, mem['shop']) is True
    assert mem['shop']['egg'] == 'unverified' and mem['egg_uses'] == {'ココット': 2}


def test_hero_only_recovery_uses_the_same_return_readiness_guard():
    mem = memory()
    mem['month_sub'].update(full_selected=False, hero_selected=True, gold_before=84, quoted_cost=50)
    assert p._finish_month_sub(native_return(), mem, mem['shop']) is False
    assert p._finish_month_sub(ready_menu(), mem, mem['shop']) is True
    assert mem['shop']['egg'] == 'check'
    assert mem['egg_uses'] == {'ココット': 2}
    assert mem['shop']['reserve'] == 50


def test_month_step_sends_no_input_while_the_paid_menu_cursor_is_missing():
    mem = memory()
    mem['shop'].update(merchant_done=True, items=[])
    assert p.month_step(native_return(), mem) == []
    assert mem['month_sub']['stage'] == 'recovering'
    assert mem['shop']['egg'] == 'opened'
