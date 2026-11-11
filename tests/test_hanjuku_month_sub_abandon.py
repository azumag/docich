"""An aborted month sub-action must end, not press B forever.

g1163 2026-11-11 23:08-23:26: たまごのかいふく opened, the cursor would not reach
the row (menu_nav_stuck), the flow aborted, and then the month menu and the shop
list flipped every 1.5 s for 18 minutes — the abort path had no exit, so nothing
ever cleared ``month_sub`` and the repetition detector latched screen_stalled.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from docich.hanjuku.strategy.economy import MONTH_SUB_ABANDON_BACKS
from test_hanjuku_chart_bot import Canvas


def text_frame(gold=None, lines=('「おめにかかれて こうえいです。',)):
    c = Canvas()
    if gold is not None:
        c.text(72, 15, f'1ねん 10のつき {gold}G')
    for i, line in enumerate(lines):
        c.text(24, 151 + 16 * i, line)
    return parse(c.frame())


def memory(**sub):
    return {'month': '22-8', 'chapter': 9,
            'shop': {'key': '22-8', 'egg': 'opened', 'items': [], 'soldiers': 0},
            'month_sub': {'kind': 'egg', 'gold_before': 200, 'presses': 40, 'key': '22-8',
                          'left_menu': True, 'aborted': True, **sub}}


def test_after_the_abandon_bound_the_sub_action_is_dropped():
    mem = memory()
    screen = text_frame(200)
    buttons = [policy.month_sub_step(screen, mem)[0]['buttons'][0]
               for _ in range(MONTH_SUB_ABANDON_BACKS)]
    # Plain text past the B limit may take bounded A presses (g662); the point
    # is that the sub-action is dropped instead of being retried forever.
    assert buttons[-1] == 'b'
    assert buttons.count('b') > buttons.count('a')
    assert 'month_sub' not in mem
    assert mem['shop']['egg'] == 'skipped'
    assert any(r['decision'] == 'month_sub_abandoned' for r in mem['_records'])


def test_before_the_bound_the_sub_action_stays_tracked():
    mem = memory()
    screen = text_frame(200)
    for _ in range(MONTH_SUB_ABANDON_BACKS - 2):
        policy.month_sub_step(screen, mem)
    assert mem.get('month_sub', {}).get('kind') == 'egg'
    assert mem['shop']['egg'] == 'opened'
