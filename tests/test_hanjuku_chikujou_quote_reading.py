"""Native quote geometry with controlled occlusions, never live input proof."""
from pathlib import Path
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_recovery_payroll import ID

FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/g706-cocotte-repair-fund-building.png'
# Live g889 frame: a one-digit 5G quote is right-aligned (blank cell at x24, 5 at x32).
SINGLE_DIGIT = Path(__file__).parent / 'fixtures/hanjuku/g889-chikujou-single-digit-quote.png'


def quote(occlusion=None):
    native = read_png(FIXTURE)
    if occlusion is None: return parse(native)
    c = Canvas(); c.rgb = bytearray(native.rgb)
    x = 24 if occlusion == 'first_digit' else 32
    bg = native.pixel(20, 200)
    for y in range(199, 207):
        for xx in range(x, x + 8):
            # A first digit hidden by something drawn over it leaves an unreadable
            # tile; a plain background cell there is a genuine right-aligned space.
            c.put(xx, y, (255, 255, 255) if occlusion == 'first_digit' and (xx + y) % 2 else bg)
    return parse(c.frame())


def memory():
    return {'chapter': 2, 'month': '3-4', 'tick': 100, '_run_identity': deepcopy(ID),
        'captured': ['フーリック'], 'garrison': {'フーリック': ['ソーピニヨン']},
        'shop': {'key': '3-4', 'chikujou': 'opened', 'soldiers_done': True, 'merchant_done': True,
                 'items': [], 'reserve': 0, 'egg': 'done', 'recruit_reserve': 0},
        'month_sub': {'kind': 'chikujou', 'key': '3-4', 'chikujou_version': 2,
            'chikujou_identity': deepcopy(ID), 'chikujou_chapter': 2, 'gold_before': 84,
            'chosen': 'フーリック', 'presses': 0, 'left_menu': True}}


@pytest.mark.parametrize('occlusion', ['first_digit', 'second_digit'])
def test_a_missing_quote_digit_cannot_become_a_cheaper_affordable_quote(occlusion):
    s = quote(occlusion); mem = memory()
    # The aggregate retains a syntactically plausible 6G or1G, while the
    # calibrated raw price row has an unreadable/blank cell in the original16G number.
    assert ('6Gかかりますが' if occlusion == 'first_digit' else '1Gかかりますが') in s.text
    assert p.month_sub_step(s, mem) != [p.pad('a')]
    assert not mem['month_sub'].get('confirm_sent')
    for _ in range(8):
        mem = json.loads(json.dumps(mem))
        assert p.month_sub_step(s, mem) != [p.pad('a')]
    assert not mem['shop'].get('chikujou_upgrades')


def test_the_unchanged_native_quote_keeps_its_exact_price_and_single_confirmation():
    s = quote(); mem = memory()
    assert p.month_sub_step(s, mem) == [p.pad('a')]
    assert mem['month_sub']['quoted_cost'] == 16
    assert mem['month_sub']['confirm_gold'] == 84
    assert p.month_sub_step(s, mem) != [p.pad('a')]


def test_the_live_right_aligned_one_digit_quote_is_read_and_confirmed():
    # g889 declined every 築城 with cost=None because only x24 was accepted as the first digit.
    s = parse(read_png(SINGLE_DIGIT)); mem = memory()
    assert [(x, ch) for x, ch in next(r for r in s.lines if r.y == 199).cells][:2] == [(32, '5'), (40, 'G')]
    mem['month'] = mem['shop']['key'] = mem['month_sub']['key'] = '3-8'
    mem['captured'] = []; mem['month_sub'].update(chosen='アルマムーン', gold_before=132)
    assert p.month_sub_step(s, mem) == [p.pad('a')]
    assert mem['month_sub']['quoted_cost'] == 5
    assert mem['month_sub']['confirm_gold'] == 132
    assert p.month_sub_step(s, mem) != [p.pad('a')]
