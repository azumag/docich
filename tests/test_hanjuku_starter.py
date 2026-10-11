"""Synthetic measured glyphs; web/manual layout, native acceptance pending."""
import json

import pytest

from docich import hanjuku_policy as p, hanjuku_starter as starter
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas as BaseCanvas, MARK


class Canvas(BaseCanvas):
    def text(self, x, y, text, color=(255, 255, 255)):
        for i, ch in enumerate(text):
            super().text(x + 8 * i, y, 'ウ' if ch == 'ヴ' else ch, color)
            if ch == 'ヴ':
                self.tile(x + 8 * i, y - 8, MARK['゛'], color)


def picture(available=('ゼウス', 'ヴィーナス', 'ココット'), selected=(),
            *, on=None, confirm=False, hand=True, prompt=True):
    c = Canvas()
    c.text(64, 15, '1ねん6のつき151G')
    for i, name in enumerate(available):
        c.text(48, 47 + 16 * i, name)
    for i, name in enumerate(selected):
        c.text(48, 127 + 16 * i, name)
    c.text(144, 47, available[0] if available else 'ゼウス')
    c.text(144, 63, 'HP85P')
    c.text(144, 79, 'たまご あり')
    if confirm:
        c.text(16, 191, 'よろしいですか?')
        c.text(176, 191, 'うむッ!')
        c.text(176, 207, 'いかんッ!')
        if hand:
            c.hand(152, 185 if on != 'いかんッ!' else 201)
    elif prompt:
        c.text(16, 191, 'つぎのたたかいに そなえて')
        c.text(16, 207, 'どのしょうぐんを せんぱつたいに')
        if hand and available:
            c.hand(24, 41 + 16 * available.index(on or available[0]))
    return c


def state(**mem):
    return {'policy': {'chapter': 1, 'tick': 100, **mem}}


def keys(actions):
    return [b for a in actions for b in a.get('buttons', [])]


def test_three_selections_require_observed_lower_panel_then_final_confirmation():
    s = state(); selected = []
    names = ('ゼウス', 'ヴィーナス', 'ココット')
    for name in names:
        available = tuple(n for n in names if n not in selected)
        actions, s = decide(picture(available, selected, on=name).frame(), s)
        assert keys(actions) == ['a']
        assert s['policy']['starter_party']['selected'] == selected
        assert not s['policy'].get('garrison') and not s['policy'].get('sorties')
        # A planned input alone is not a selected-person receipt.
        actions, s = decide(picture(available, selected, on=name).frame(), s)
        assert actions == []
        selected.append(name)
    actions, s = decide(picture((), selected, confirm=True).frame(), s)
    assert keys(actions) == ['a']
    assert s['policy']['starter_party']['confirm_sent'] is True
    assert s['policy']['starter_party']['selected'] == selected
    assert s['_records'][-1]['decision'] == 'starter_confirm'
    assert not s['policy'].get('garrison')


@pytest.mark.parametrize('selected', [(), ('ゼウス',), ('ゼウス', 'ヴィーナス')])
def test_hotload_never_accepts_underfilled_confirmation(selected):
    frame = picture(('ゼウス', 'ヴィーナス', 'ココット'), selected, confirm=True).frame()
    assert parse(frame).kind == 'starter_confirm'
    actions, s = decide(frame, state())
    assert keys(actions) == ['down']
    assert not s['policy']['starter_party'].get('confirm_sent')
    frame = picture(('ゼウス', 'ヴィーナス', 'ココット'), selected,
                    confirm=True, on='いかんッ!').frame()
    actions, s = decide(frame, json.loads(json.dumps(s)))
    assert keys(actions) == ['a']
    assert s['_records'][-1]['decision'] == 'starter_underfilled_rejected'


def test_hotload_accepts_three_actual_names_without_old_planned_selection():
    actions, s = decide(picture((), ('ゼウス', 'ヴィーナス', 'ココット'), confirm=True).frame(), state())
    assert keys(actions) == ['a']
    assert s['_records'][-1]['observed_metric']['selected'] == ['ゼウス', 'ヴィーナス', 'ココット']


@pytest.mark.parametrize('bad', ['hand', 'unknown', 'duplicate'])
def test_unread_party_cannot_borrow_shop_or_frozen_screen_fallback(bad):
    c = picture(hand=bad != 'hand', selected=('ゼウス', 'ゼウス') if bad == 'duplicate' else ())
    if bad == 'unknown':
        c.tile(72, 47, 0xfffffff0fffffff0)
    frame = c.frame(); s = state()
    for _ in range(35):
        actions, s = decide(frame, s)
        assert actions == []
        assert s['no_input_streak'] == 0
        assert not s['policy']['starter_party'].get('confirm_sent')


def test_previous_repair_monthly_and_recall_transactions_do_not_own_the_party():
    s = state(house={'phase': 'close'}, month_sub={'kind': 'recruit'}, recall={'stage': 'dest'})
    actions, s = decide(picture().frame(), s)
    assert keys(actions) == ['a']
    assert s['_records'][-1]['decision'] == 'starter_select'
    assert s['policy']['house'] == {'phase': 'close'}


def test_partial_prompt_after_selection_stays_owned_and_never_exits_zero():
    _, s = decide(picture().frame(), state())
    c = picture(prompt=False)
    c.text(16, 191, 'つぎのたたかいに')
    for _ in range(30):
        actions, s = decide(c.frame(), s)
        assert actions == []
        assert not s['policy']['starter_party'].get('confirm_sent')


def test_stat_name_and_upper_duplicate_are_never_counted_as_selected_people():
    c = picture(selected=('ゼウス',))
    screen = parse(c.frame())
    assert [r[2] for r in starter.rows(screen, selected=True)] == ['ゼウス']
    assert len(starter.rows(screen)) == 3


def test_next_screen_proves_only_departure_not_next_chapter_garrison():
    _, s = decide(picture((), ('ゼウス', 'ヴィーナス', 'ココット'), confirm=True).frame(), state())
    c = Canvas(); c.text(16, 191, 'だいじん')
    _, s = decide(c.frame(), s)
    assert 'starter_party' not in s['policy']
    assert any(r['decision'] == 'starter_departed_observed' for r in s['_records'])
    assert not s['policy'].get('garrison') and s['policy']['chapter'] == 1


def test_visible_durable_egg_holder_is_chosen_and_selected_duplicate_ignored():
    c = picture(('ココット', 'ゼウス', 'ヴィーナス'), selected=('ヴィーナス',))
    actions, s = decide(c.frame(), state())
    assert keys(actions) == ['down']
    assert not s['policy']['starter_party'].get('pending')


def test_selected_grey_upper_slots_leave_gaps_and_are_not_toggled_out():
    c = picture(('ゼウス', 'ココット', 'ヴィーナス'), selected=('ココット',), on='ゼウス')
    # The actual play video keeps chosen names in place but greys them out.
    # An unread grey row may leave two readable rows 32 pixels apart.
    for y in range(55, 72):
        for x in range(48, 88):
            c.put(x, y, (0, 0, 0))
    c.text(48, 63, 'ココット', color=(128, 128, 128))
    screen = parse(c.frame())
    assert [r[2] for r in starter.rows(screen)] == ['ゼウス', 'ヴィーナス']
    actions, s = decide(c.frame(), state())
    assert keys(actions) == ['a']
    assert s['policy']['starter_party']['pending'] == 'ゼウス'


def test_duplicate_actual_confirmation_rows_are_not_counted_as_three():
    frame = picture((), ('ゼウス', 'ゼウス', 'ヴィーナス'), confirm=True).frame()
    assert parse(frame).kind == 'starter_confirm'
    actions, s = decide(frame, state())
    assert keys(actions) == ['down']
    assert not s['policy']['starter_party'].get('confirm_sent')


def test_hotload_all_unread_lower_names_cannot_fall_back_to_generic_yes():
    c = picture((), ('ゼウス', 'ヴィーナス', 'ココット'), confirm=True)
    for y in (127, 143, 159):
        c.tile(48, y, 0xfffffff0fffffff0)
    assert starter.rows(parse(c.frame()), selected=True) is None
    actions, s = decide(c.frame(), state())
    assert keys(actions) == ['down']
    assert s['screen_kind'] == 'starter_confirm'
    assert not s['policy']['starter_party'].get('confirm_sent')


def test_stalled_cursor_has_a_finite_budget_and_resumes_on_observed_movement():
    frame = picture(('ココット', 'ゼウス', 'ヴィーナス')).frame()
    s = state()
    sent = []
    for i in range(35):
        actions, s = decide(frame, s)
        assert keys(actions) in (['down'], [])
        sent += keys(actions)
    assert sent == ['down'] * starter.NAV_PRESS_LIMIT
    actions, s = decide(picture(('ココット', 'ゼウス', 'ヴィーナス'), on='ゼウス').frame(), s)
    assert keys(actions) == ['a']


def test_underfilled_confirmation_cannot_repeat_ignored_arrows_forever():
    frame = picture(selected=(), confirm=True).frame(); s = state(); sent = []
    for _ in range(35):
        actions, s = decide(frame, s); sent += keys(actions)
    assert sent == ['down'] * starter.NAV_PRESS_LIMIT
    actions, s = decide(picture(selected=(), confirm=True, on='いかんッ!').frame(), s)
    assert keys(actions) == ['a']
    assert s['_records'][-1]['decision'] == 'starter_underfilled_rejected'


def test_short_first_prompt_is_owned_before_selection_and_before_generic_cancel():
    c = picture(prompt=False); c.text(16, 191, 'つぎのたたかいに'); c.hand(24, 41)
    actions, s = decide(c.frame(), state())
    assert keys(actions) == ['a']
    assert s['screen_kind'] == 'starter_list'


def test_chapter_and_new_run_discard_old_unconfirmed_party():
    _, s = decide(picture().frame(), state())
    p._enter_chapter(s['policy'], 2, reason='test')
    assert 'starter_party' not in s['policy']
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g1', 'generation': 1, 'lease_id': 'lease1'}
    _, s = decide(picture().frame(), state(), run_identity=identity)
    c = Canvas(); c.text(16, 191, 'だいじん')
    _, s = decide(c.frame(), s, run_identity={**identity, 'runtime_id': 'g2', 'generation': 2})
    assert 'starter_party' not in s['policy']
