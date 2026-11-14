"""Locally calibrated shop digits get earlier feedback, never earlier payment."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from docich.hanjuku.flows.shop import quantity_step
from docich.hanjuku.os_layer.commands import menu_navigation_reading, pad
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from test_hanjuku_menu_cursor_tempo import entry, state

FIX = Path(__file__).parent / 'fixtures/hanjuku'


def native(digit='ones'):
    return parse(read_png(FIX / f'local-merchant-quantity-{digit}.png'))


@pytest.mark.parametrize('digit,x,directions', [('ones', 224, ['up', 'down', 'left']),
                                              ('tens', 216, ['up', 'down', 'right'])])
def test_native_digit_cursor_and_quote_enable_only_one_existing_short_move(digit, x, directions):
    screen = native(digit)
    before = deepcopy(screen)
    assert menu_navigation_reading(screen) == {'kind': 'shop_quantity',
        'menu': 'merchant_quantity', 'cursor': [x, 199], 'directions': directions}
    for b in directions:
        assert entry.observation_interval_ms(state(screen), [pad(b)]) == 500
    for actions in ([], [pad('a')], [pad('b')], [pad('up', 7)], [pad('up'), pad('up')],
                    [pad('up'), {'type': 'wait', 'ms': 50}, pad('up')]):
        assert entry.observation_interval_ms(state(screen), actions) == 1500
    assert screen == before


def test_merchant_quantity_policy_still_reobserves_before_a_and_payment():
    mem = {'chapter': 9, 'month': '22-7', 'gold': 184,
           'shop': {'key': '22-7', 'want': ['クースカン', 2, 12]}}
    actions = quantity_step(native(), mem)
    assert actions == [pad('up')]
    assert entry.observation_interval_ms(state(native()), actions) == 500
    assert not mem['shop'].get('bought') and not mem['shop'].get('last_buy')
    screen = native('two')
    assert quantity_step(screen, mem) == [pad('a')]
    assert entry.observation_interval_ms(state(screen), [pad('a')]) == 1500
    assert not mem['shop'].get('bought') and not mem['shop'].get('last_buy')


@pytest.mark.parametrize('change', ['prompt', 'quote', 'digit', 'duplicate_number', 'duplicate_quote',
                                   'duplicate_digit', 'no_hand', 'x', 'y', 'wide', 'kind'])
def test_unread_or_wrong_payment_layout_keeps_normal_cadence(change):
    screen = native()
    if change in ('prompt', 'quote', 'digit', 'duplicate_digit'):
        y = 183 if change == 'quote' else 199
        x = 32 if change == 'quote' else 224 if change in ('digit', 'duplicate_digit') else 24
        def alter(line):
            cells = tuple((cx, UNKNOWN if cx == x else ch) for cx, ch in line.cells)
            if change == 'duplicate_digit': cells = line.cells + ((224, '1'),)
            return replace(line, cells=cells)
        screen.lines = [alter(l) if l.y == y else l for l in screen.lines]
    elif change.startswith('duplicate_'):
        screen.lines.append(next(l for l in screen.lines if l.y == (183 if change == 'duplicate_quote' else 199)))
    elif change == 'no_hand': screen.hand = None
    elif change == 'x': screen.hand = (130, 197, 150, 210)
    elif change == 'y': screen.hand = (202, 181, 222, 194)
    elif change == 'wide': screen.hand = (180, 197, 222, 210)
    elif change == 'kind': screen.kind = 'text'
    reading = menu_navigation_reading(screen)
    assert not reading or reading.get('menu') != 'merchant_quantity'
    assert entry.observation_interval_ms(state(screen), [pad('up')]) == 1500
