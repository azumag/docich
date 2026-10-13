"""Original g725 stall: a ritual dialogue retains an inactive recovery list.

Only the original RGB and stopped boundary are native evidence; the caller
replays below do not establish a completed ritual or a resumed game.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_recovery_list_return import confirmed, native


def ritual():
    f = read_png(Path(__file__).parent / 'fixtures/hanjuku/g725-priest-ritual-preparation.png')
    assert f.digest() == '4f62ad547a070229144f9ac256f3971018e8e3da3c24a84042e3cc767a575214'
    s = parse(f)
    assert s.hand is None and s.header['gold'] == 92
    assert s.has('ぎしきの') and s.has('じゅんびを')
    return s


@pytest.mark.parametrize('owner', ['full', 'hero', 'individual'])
def test_background_list_advances_ritual_without_aborting_or_claiming_payment(owner):
    m = confirmed(); sub = m['month_sub']
    m['month'] = sub['key'] = m['shop']['key'] = '1-5'
    sub.update(gold_before=92, presses=3)
    if owner == 'full': sub.update(full_selected=True, hero_selected=False, confirm_actor='ぜんかいふく')
    elif owner == 'individual':
        sub.update(hero_selected=False, individual_selected=True, individual_actor='ココット',
                   individual_identity=deepcopy(m['_run_identity']), individual_chapter=1, confirm_actor='ココット')
    eggs, shop = deepcopy(m['egg_uses']), deepcopy(m['shop'])
    for _ in range(3):
        assert p.month_sub_step(ritual(), m) == [p.pad('a')]
        assert not sub.get('payment_interrupted') and not sub.get('aborted')
        assert not sub.get('recovery_list_return')
        assert m['egg_uses'] == eggs and m['shop'] == shop
    assert sub['presses'] == 6  # caller remains bounded


def test_returned_question_without_hand_waits_without_reopening_quote():
    m = confirmed(); s = native('return-list'); s.hand = None
    assert p.month_sub_step(s, m) == []
    assert not m['month_sub'].get('payment_interrupted')
    assert not m['month_sub'].get('recovery_list_return')


@pytest.mark.parametrize('position', [(114, 41), (162, 177), (10, 105)])
def test_wrong_or_unread_cursor_cannot_close_list_or_confirm_another_purchase(position):
    m = confirmed(); c = Canvas(); c.text(32, 47, 'ぜんかいふく')
    c.text(136, 47, 'しょうぐんぼしゅう'); c.hand(*position)
    s = parse(c.frame())
    assert s.hand is not None
    assert p.month_sub_step(s, m) == []
    assert not m['month_sub'].get('payment_interrupted')
    assert not m['month_sub'].get('recovery_list_return')


def test_real_returned_left_list_closes_b_then_quiet_month_accepts_fee_only():
    m = confirmed()
    assert p.month_sub_step(native('return-list'), m) == [p.pad('b')]
    assert p._finish_month_sub(native('month'), m, m['shop'])
    assert m['shop']['egg_paid_actors'] == [p.NAME]
    assert p.NAME not in m['egg_uses']  # no invented full quantity
    assert m['egg_uses']['ココット'] == 0


@pytest.mark.parametrize('bad', ['hand', 'gold', 'month', 'body', 'full', 'right', 'duplicate', 'kind'])
def test_cold_resume_accepts_only_measured_preparation_foreground(bad):
    from docich.hanjuku_screen import resume_egg_ritual_panel
    s = ritual()
    if bad == 'hand': s.hand = (10, 41, 30, 54)
    elif bad == 'gold': s.header['gold'] = None
    elif bad == 'month': s.header['month'] = 0
    elif bad == 'kind': s.kind = 'battle_menu'
    elif bad == 'duplicate': s.lines.append(deepcopy(next(l for l in s.lines if l.y == 47)))
    else:
        y = 183 if bad == 'body' else 47 if bad == 'full' else 79
        s.lines = [l for l in s.lines if l.y != y]
    assert not resume_egg_ritual_panel(s)


def test_cold_dialogue_owner_is_bounded_and_never_accepts_an_unbound_fee():
    m = {'resume_chapter_pending': True, 'resume_egg_ritual': {'readings': 0, 'month': '1-5'}}
    for _ in range(p.EGG_RITUAL_LIMIT):
        assert p.resume_egg_ritual_step(ritual(), m) == [p.pad('a')]
    assert p.resume_egg_ritual_step(ritual(), m) == []
    assert m['resume_egg_ritual']['readings'] == p.EGG_RITUAL_LIMIT
    assert not m.get('chapter') and not m.get('month_sub') and not m.get('egg_uses')


def test_cold_owner_holds_confirmation_and_releases_only_actual_departure():
    m = {'resume_egg_ritual': {'readings': 0, 'month': '1-9'}}
    assert p.resume_egg_ritual_step(native('fee'), m) == []
    assert p.resume_egg_ritual_step(native('return-list'), m) == [p.pad('b')]
    assert p.resume_egg_ritual_step(native('month'), m) is None
    assert 'resume_egg_ritual' not in m and not m.get('month_sub') and not m.get('egg_uses')


def test_exhausted_cold_owner_cannot_borrow_a_from_generic_frozen_screen_fallback():
    from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g725-priest-ritual-preparation.png')
    state = {'policy': {'name': {'done': True}, 'resume_chapter_pending': True,
                       'resume_egg_ritual': {'readings': p.EGG_RITUAL_LIMIT, 'month': '1-5'}},
             'no_input_frames': [frame.digest()], 'no_input_streak': NO_INPUT_HOLD_MAX + 1}
    for _ in range(3):
        actions, state = decide(frame, state)
        assert actions == []
        assert state['policy']['resume_egg_ritual']['readings'] == p.EGG_RITUAL_LIMIT
        assert not any(r['decision'] == 'no_input_fallback' for r in state['_records'])
