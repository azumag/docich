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


def animated_recital():
    # The live g1147 greeting retained the same false hand, but did not match
    # the old sprite hash. One changed art pixel reproduces that distinction.
    c = Canvas(); c.rgb = bytearray(native().rgb)
    c.put(108, 80, (1, 2, 3))
    return c.frame()


def live_native():
    f = read_png(Path(__file__).parent / 'fixtures/hanjuku/g1147-priest-namaste.png')
    assert f.digest() == 'e9ca5d1bbb17579b79be76d3959df9d843070c0e584fbb7175f20fe9dc1a6188'
    return f


def test_live_greeting_with_continue_marker_advances_without_escape():
    from docich.hanjuku_bot import decide
    f = live_native()
    assert find_hand(f) == (116, 82, 137, 96)
    s = parse(f, phase='concert')
    assert s.priest_ritual_dialogue and s.hand is None
    assert egg_ritual_recitation_panel(s)
    m = confirmed()
    identity = {'game': 'hanjuku-hero', 'generation': 7,
                'runtime_id': 'g7-synthetic', 'lease_id': 'synthetic-lease'}
    m.update(month='1-6', _run_identity=identity, survey_run_identity=identity)
    m['shop']['key'] = m['month_sub']['key'] = '1-6'
    before = deepcopy(m['egg_uses'])
    actions, state = decide(f, {'policy': m}, run_identity=identity)
    assert actions == [pad('a')]
    assert state['policy']['egg_uses'] == before
    assert not any(r['decision'] == 'event_text_advance' for r in state['_records'])
    assert resume_egg_ritual_step(s, {'resume_egg_ritual': {'readings': 0, 'month': '1-6'}}) == [pad('a')]


@pytest.mark.parametrize('bad', ['indicator', 'glyph', 'choice', 'battle'])
def test_live_continue_marker_never_hides_unread_text_or_another_foreground(bad):
    c = Canvas(); c.rgb = bytearray(live_native().rgb)
    if bad == 'indicator': c.put(120, 207, (1, 2, 3))
    elif bad == 'glyph': c.tile(128, 175, 0x0123456789ABCDEF)
    elif bad == 'choice': c.text(160, 175, 'うむッ!')
    s = parse(c.frame(), phase='battle' if bad == 'battle' else 'concert')
    assert not s.priest_ritual_dialogue
    assert not egg_ritual_recitation_panel(s)
    m = confirmed()
    assert _egg_recovery_step(s, m, m['month_sub']) == []
    assert resume_egg_ritual_step(s, {'resume_egg_ritual': {'readings': 0, 'month': '1-6'}}) == []


@pytest.mark.parametrize('owner', ['live', 'resume'])
def test_known_recital_advances_even_when_priest_art_hash_changes(owner):
    s = parse(animated_recital(), phase='concert')
    assert not s.priest_ritual_scene
    assert s.hand == (116, 82, 137, 96)
    assert egg_ritual_recitation_panel(s)
    if owner == 'live':
        m = confirmed(); sub = m['month_sub']
        m['month'] = sub['key'] = m['shop']['key'] = '1-5'
        before = deepcopy((m['egg_uses'], m['shop'], sub))
        assert _egg_recovery_step(s, m, sub) == [pad('a')]
        assert (m['egg_uses'], m['shop'], sub) == before
    else:
        m = {'resume_egg_ritual': {'readings': 0, 'month': '1-5'}}
        assert resume_egg_ritual_step(s, m) == [pad('a')]
        assert m['resume_egg_ritual']['readings'] == 1
        assert not m.get('month_sub') and not m.get('egg_uses')


def test_live_orchestrator_sends_first_recital_a_without_waiting_for_escape():
    from docich.hanjuku_bot import decide
    m = confirmed()
    identity = {'game': 'hanjuku-hero', 'generation': 7,
                'runtime_id': 'g7-synthetic', 'lease_id': 'synthetic-lease'}
    m.update(month='1-5', _run_identity=identity, survey_run_identity=identity)
    m['shop']['key'] = m['month_sub']['key'] = '1-5'
    before = deepcopy(m['egg_uses'])
    actions, state = decide(animated_recital(), {'policy': m}, run_identity=identity)
    assert actions == [pad('a')]
    assert state['policy']['egg_uses'] == before
    assert state['policy']['month_sub']['presses'] == 1
    assert not any(r['decision'] == 'event_text_advance' for r in state['_records'])


@pytest.mark.parametrize('bad', ['choice', 'glyph', 'hand', 'header'])
def test_changed_art_does_not_acknowledge_an_unknown_or_paid_option(bad):
    c = Canvas(); c.rgb = bytearray(animated_recital().rgb)
    if bad == 'choice': c.text(160, 175, 'うむッ!')
    elif bad == 'glyph': c.tile(128, 175, 0x0123456789ABCDEF)
    s = parse(c.frame(), phase='concert')
    if bad == 'hand': s.hand = (162, 177, 182, 190)
    elif bad == 'header': s.header['gold'] = None
    assert not egg_ritual_recitation_panel(s)
    m = confirmed()
    assert _egg_recovery_step(s, m, m['month_sub']) == []
    assert resume_egg_ritual_step(s, {'resume_egg_ritual': {'readings': 0, 'month': '1-5'}}) == []


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


@pytest.mark.parametrize('y', [151, 175])
def test_unknown_message_glyph_is_not_dropped_to_accept_the_recital(y):
    c = Canvas()
    c.rgb = bytearray(native().rgb)
    c.tile(128, y, 0x0123456789ABCDEF)
    s = parse(c.frame(), phase='concert')
    assert not egg_ritual_recitation_panel(s)
    assert not resume_egg_ritual_panel(s)
    assert s.hand is not None  # no cursor suppression on a partial reading
