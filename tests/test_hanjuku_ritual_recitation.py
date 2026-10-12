"""g731 original recital: false orange hand, no input and no receipt."""
from copy import deepcopy
from pathlib import Path

import pytest
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import (
    parse, find_hand, egg_ritual_recitation_panel, resume_egg_ritual_panel)
from docich.hanjuku.flows.egg_recovery import resume_egg_ritual_step, _egg_recovery_step
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.strategy.economy import EGG_RITUAL_LIMIT
from test_hanjuku_recovery_list_return import confirmed
from test_hanjuku_chart_bot import Canvas


def native():
    f = read_png(Path(__file__).parent / 'fixtures/hanjuku/g731-priest-namaste.png')
    assert f.digest() == '7b0af6558effa6e61a270c3de4bd1bff7815d00e41fc23a44452c2159a37817a'
    return f


def test_native_priest_sprite_is_not_a_selectable_cursor():
    f = native()
    assert find_hand(f) == (116, 82, 137, 96)
    for phase in (None, 'concert'):
        s = parse(f, phase=phase)
        assert s.kind == 'text' and s.hand is None and s.selected is None
        assert egg_ritual_recitation_panel(s) and resume_egg_ritual_panel(s)
        m = {'resume_egg_ritual': {'readings': 0, 'month': '1-5'}}
        assert resume_egg_ritual_step(s, m) == [pad('a')]
        assert not m.get('egg_uses') and not m.get('month_sub')
    # No new meaning is assigned to an explicitly different foreground.
    assert parse(f, phase='battle').hand == (116, 82, 137, 96)


@pytest.mark.parametrize('bad', ['body', 'extra', 'hand', 'year', 'month', 'gold', 'kind'])
def test_recital_match_cannot_hide_an_unknown_option_or_header(bad):
    s = parse(native(), phase='battle')
    if bad == 'body': s.lines = [l for l in s.lines if l.y != 151]
    elif bad == 'extra':
        c = Canvas(); c.text(160, 175, 'うむッ!')
        s.lines += parse(c.frame()).lines
    elif bad == 'hand': s.hand = (162, 177, 182, 190)
    elif bad == 'year': s.header['year'] = True
    elif bad == 'month': s.header['month'] = 0
    elif bad == 'gold': s.header['gold'] = None
    else: s.kind = 'yes_no'
    assert not egg_ritual_recitation_panel(s)
    assert not resume_egg_ritual_panel(s)


def test_live_transaction_recital_preserves_unpaid_quantities_and_reserves():
    m = confirmed(); sub = m['month_sub']
    m['month'] = sub['key'] = m['shop']['key'] = '1-5'
    before = deepcopy((m['egg_uses'], m['shop']))
    assert _egg_recovery_step(parse(native()), m, sub) == [pad('a')]
    assert (m['egg_uses'], m['shop']) == before
    assert not sub.get('recovery_list_return') and not sub.get('payment_verified')


def test_cold_recital_keeps_the_existing_finite_bound():
    m = {'resume_egg_ritual': {'readings': EGG_RITUAL_LIMIT, 'month': '1-5'}}
    assert resume_egg_ritual_step(parse(native()), m) == []
    assert m['resume_egg_ritual']['readings'] == EGG_RITUAL_LIMIT
