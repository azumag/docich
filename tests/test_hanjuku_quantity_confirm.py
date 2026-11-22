"""The purchase confirmation answers instead of holding.

g1190 2026-11-18 63-5: 「1こで 50Gに なりまんな! よろしでっか?」 with
うむッ!/いかんッ! sat static for minutes: the shop quantity step waited on
its own overlay while the confirmation never got an answer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku import orchestrator as O
from docich.hanjuku_bot import decide
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas

CONFIRM = 'せんかいくくピオーネ2 1こで50Gになりまんな!よろしいでっか?うむッ!いかんッ!'


def test_the_purchase_confirmation_answers_a():
    """The hand sits on うむッ! in the measured frame, so A accepts it."""
    assert O._is_quantity_confirm('shop_quantity', CONFIRM) is True


def test_a_camp_dialog_without_the_choice_buttons_is_not_the_confirm():
    assert O._is_quantity_confirm('shop_quantity', 'いどう ステータス キャンプ きかん よろしいでっか') is False


def test_other_screens_are_not_the_purchase_confirmation():
    assert O._is_quantity_confirm('text', CONFIRM) is False
    assert O._is_quantity_confirm('shop_quantity', 'しょうぐんぼしゅう') is False


# docich#5046: the priest's full-recovery quote reads like a shop quantity
# confirmation (「NこでNGになりまんな! よろしでっか?」 -> kind 'shop_quantity'),
# but a tracked egg month_sub owns it. The generic accept paid unbound, so the
# ritual ran on the wrong press budget and the sub was abandoned 'skipped'
# with the payment unconfirmed (run g1288, ch10 93-1).
ID = {'game': 'hanjuku-hero', 'runtime_id': 'g1288-synthetic', 'generation': 1288,
      'lease_id': 'synthetic-lease'}


def egg_quote(gold=300):
    c = Canvas()
    c.text(72, 15, f'1ねん 9のつき {gold}G')
    # The quote and the choice buttons share y=183 in production, but a
    # single-line synthetic quote ('3こで150Gになりまんな! よろしいでっか?')
    # runs into the うむッ! cell and corrupts it (mojibake), so the quote
    # wording sits one line above the buttons here. classify_text and the
    # quote regex both read the whole screen, so the behaviour is identical.
    c.text(24, 167, '3こで150Gになりまんな!')
    c.text(24, 183, 'よろしいでっか?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    return c.frame()


def egg_sub(gold=300):
    return {'chapter': 1, 'month': '1-9', '_run_identity': dict(ID),
            'survey_run_identity': dict(ID),
            'egg_uses': {},
            'shop': {'key': '1-9', 'egg': 'opened'},
            'month_sub': {'kind': 'egg', 'key': '1-9', 'gold_before': gold,
                          'presses': 0, 'left_menu': True, 'full_selected': True}}


def test_a_live_egg_sub_owns_its_quote_and_binds_the_fee():
    assert parse(egg_quote()).kind == 'shop_quantity'
    actions, state = decide(egg_quote(), {'policy': egg_sub()}, run_identity=dict(ID))
    assert actions == [pad('a')]
    sub = state['policy']['month_sub']
    assert sub['stage'] == 'recovering' and sub['quoted_cost'] == 150
    assert sub['confirm_identity'] == ID and sub['confirm_actor'] == 'ぜんかいふく'
    assert any(r['decision'] == 'egg_recover_confirm' for r in state['_records'])
    assert not any(r['decision'] == 'quantity_confirm_accept' for r in state['_records'])


def test_the_egg_sub_can_still_decline_the_quote_the_shortcut_would_pay():
    # 100G cannot cover a 150G quote plus the spending hold: the owner declines
    # with B where the generic accept would have paid it blind.
    actions, state = decide(egg_quote(gold=100), {'policy': egg_sub(gold=100)},
                            run_identity=dict(ID))
    assert actions == [pad('b')]
    assert state['policy']['month_sub'].get('aborted') is True
    assert not any(r['decision'] == 'quantity_confirm_accept' for r in state['_records'])


def test_the_generic_accept_still_fires_without_an_egg_sub():
    actions, state = decide(egg_quote(), {'policy': {'chapter': 1, 'month': '1-9',
                                                     '_run_identity': dict(ID),
                                                     'survey_run_identity': dict(ID)}},
                            run_identity=dict(ID))
    assert actions == [pad('a')]
    assert any(r['decision'] == 'quantity_confirm_accept' for r in state['_records'])


def test_the_generic_accept_still_fires_for_other_subs():
    mem = egg_sub()
    mem['month_sub'] = {'kind': 'recruit', 'key': '1-9', 'gold_before': 300,
                        'presses': 0, 'left_menu': True}
    actions, state = decide(egg_quote(), {'policy': mem}, run_identity=dict(ID))
    assert actions == [pad('a')]
    assert any(r['decision'] == 'quantity_confirm_accept' for r in state['_records'])
