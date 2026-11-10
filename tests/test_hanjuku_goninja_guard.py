"""Goninja guard: degraded offer frames decline by measured row order.

Synthetic native glyphs/cursors only; no game or input transport is involved.
The 2026-11-06 incident class: the monthly goninja offer's OCR degraded the
choice labels so the frame never became a clean yes_no, and the generic phase
input stalled the red-curtain screen. The guard arms on the offer's own
wording, applies the same 300G cash floor, and after a bounded hold declines
by the measured row order (upper accept / lower refuse) — never a blind A.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as policy
from docich.hanjuku_bot import decide
from test_hanjuku_chart_bot import Canvas, GREEN
from test_hanjuku_goninja_cash_floor import offer, owned_state


def degraded_offer(gold, cursor='accept'):
    """The measured offer with the choice labels misread as other glyphs.

    The prompt wording and the two band rows stay visible, but neither
    うむッ! nor いかんッ! survives, so classify() never yields 'yes_no' and
    yes_no_step's cash floor cannot run. This is the frame shape that froze.
    """
    c = Canvas((50, 30, 10))
    for y in range(0, 32):
        for x in range(256):
            c.put(x, y, (50, 30, 10))
    if gold is not None:
        c.text(16, 15, f'2ねん11のつき{gold}G')
    for y in range(128, 207):
        for x in range(18, 236):
            c.put(x, y, GREEN)
    c.text(24, 151, '50Gで てきの ティピオカ')
    c.text(24, 167, 'うちとってみせるが いかがかな!?')
    c.text(184, 183, 'ああああ')
    c.text(184, 199, 'いいいい')
    if cursor == 'accept':
        c.hand(162, 177)
    elif cursor == 'decline':
        c.hand(162, 193)
    return c.frame()


def _run(state, frame, count):
    actions = []
    for _ in range(count):
        out, state = decide(frame, state)
        actions.append(out)
    return actions, state


def test_degraded_offer_holds_then_declines_by_position_never_accepts():
    state = owned_state('none')
    frame = degraded_offer(60)
    actions, state = _run(state, frame, policy.GONINJA_GUARD_HOLD_MAX + 1)
    # Held observations send nothing; the bound then moves toward the lower
    # (refusal) row. No press ever confirms the upper accept row.
    assert all(a == [] for a in actions[:-1])
    assert actions[-1] == [policy.pad('down')]
    records = state['policy'].get('_records') or state.get('_records') or []
    assert not any(r.get('strategy_variant') == 'accept_goninja' for r in records)

    out, state = decide(degraded_offer(60, cursor='decline'), state)
    assert out == [policy.pad('a')]
    records = state['policy'].get('_records') or state.get('_records') or []
    rec = records[-1]
    assert rec['strategy_variant'] == 'decline_goninja_position'
    assert rec['choice'] == 'いかんッ!（位置）'
    # The guard is spent; the next observation releases it.
    _, state = decide(degraded_offer(60, cursor='decline'), state)
    assert 'goninja_guard' not in state['policy']


def test_degraded_offer_above_floor_still_declines_by_position():
    # Fail closed: an unreadable choice position never spends the 50G, even
    # when the readable cash floor would allow the request.
    state = owned_state('none')
    frame = degraded_offer(5000)
    _, state = _run(state, frame, policy.GONINJA_GUARD_HOLD_MAX + 1)
    out, state = decide(degraded_offer(5000, cursor='decline'), state)
    assert out == [policy.pad('a')]
    records = state['policy'].get('_records') or state.get('_records') or []
    assert records[-1]['strategy_variant'] == 'decline_goninja_position'
    assert not any(r.get('strategy_variant') == 'accept_goninja' for r in records)


def test_clean_yes_no_offer_still_uses_the_cash_floor():
    # Regression: a fully readable offer keeps the measured yes/no path; the
    # guard observes but never intercepts kind 'yes_no'.
    state = owned_state('none')
    out, state = decide(offer(301, cursor='accept'), state)
    assert out == [policy.pad('a')]
    records = state['policy'].get('_records') or state.get('_records') or []
    assert records[-1]['strategy_variant'] == 'accept_goninja'
    # The acceptance record above verifies the clean yes/no path without
    # imposing an internal guard-lifetime requirement.


def test_narration_page_without_choice_band_is_not_owned():
    # Offer wording with no hand and no band rows is a reading page; the
    # generic phase input advances it and the guard does not hold.
    c = Canvas((50, 30, 10))
    for y in range(0, 32):
        for x in range(256):
            c.put(x, y, (50, 30, 10))
    c.text(16, 15, '2ねん11のつき5000G')
    for y in range(128, 207):
        for x in range(18, 236):
            c.put(x, y, GREEN)
    c.text(24, 151, '50Gで てきの ティピオカ')
    c.text(24, 167, 'うちとってみせるが いかがかな!?')
    state = owned_state('none')
    out, state = decide(c.frame(), state)
    assert out != []
    assert 'goninja_guard' not in state['policy'] or state['policy']['goninja_guard'].get('choice_seen') is not True


def test_pending_month_sub_keeps_its_owner():
    state = owned_state('recruit')
    out, state = decide(degraded_offer(5000), state)
    assert 'goninja_guard' not in state['policy']


def test_unrelated_month_choice_with_same_rows_is_not_owned():
    # Cursor and two visible rows alone are not evidence of a goninja offer.
    # A degraded unrelated monthly prompt must not be captured by this guard.
    c = Canvas((50, 30, 10))
    for y in range(0, 32):
        for x in range(256):
            c.put(x, y, (50, 30, 10))
    c.text(16, 15, '2ねん11のつき5000G')
    for y in range(128, 207):
        for x in range(18, 236):
            c.put(x, y, GREEN)
    c.text(24, 151, 'こんげつの おしらせです')
    c.text(24, 167, 'どうしますか?')
    c.text(184, 183, 'ああああ')
    c.text(184, 199, 'いいいい')
    c.hand(162, 177)
    state = owned_state('none')
    _, state = decide(c.frame(), state)
    assert 'goninja_guard' not in state['policy']


def test_armed_guard_releases_on_map_transition():
    # Unlike the unrelated-month-choice test, start with an already armed
    # guard. A definitive map transition must release stale prompt ownership.
    from types import SimpleNamespace
    from docich.hanjuku.flows.prompts import goninja_guard_step

    state = owned_state('none')
    _, state = decide(degraded_offer(5000), state)
    guard = state['policy'].get('goninja_guard')
    assert isinstance(guard, dict) and guard.get('choice_seen') is True

    map_screen = SimpleNamespace(kind='map', text='')
    assert goninja_guard_step(map_screen, state['policy']) is None
    assert 'goninja_guard' not in state['policy']
