"""g719's paid individual ritual returns to the recovery list, not month menu.

Four original screens cover selection, fee, list return and month return.
The original fee/list inputs had current-frame mismatches; these fixtures
reproduce policy behavior, not complete native input or quantity receipts.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas

ID = {'game': 'hanjuku-hero', 'runtime_id': 'g719-a6227941', 'generation': 719,
      'lease_id': '6231516c-c54f-4cc4-b39a-343a281de2db'}
RGB = {'list': '0b2d8b28a5cf33b189376d085b6714ca2ca55b7ae7572f8ae8436f10aa0343d5',
       'fee': '758e9d64143f0037181327a49ce5d2ef476efffe6585293558d1954a052d5519',
       'return-list': 'e7e6d66f15812afcfcf06ca5e8f938db6c3bd22df2470dd43a95858300e6267b',
       'month': '63de3ae9a5ec8237cd0b11a68b9ad082b8949e838d7e71d9bea84b14dfee1be2'}


def native(which):
    f = read_png(Path(__file__).parent / f'fixtures/hanjuku/g719-priest-{which}.png')
    assert f.digest() == RGB[which]
    return parse(f)


def memory():
    return {'chapter': 1, 'month': '1-9', 'tick': 120, '_run_identity': dict(ID),
            'egg_uses': {p.NAME: 3, 'ゼウス': 3, 'アルファルファ': 2, 'ココット': 0},
            'egg_zero_owners': ['ココット'],
            'shop': {'key': '1-9', 'egg': 'opened', 'reserve': 200, 'egg_priority': True,
                     'soldiers': 50, 'soldiers_done': False},
            'month_sub': {'kind': 'egg', 'key': '1-9', 'gold_before': 85, 'presses': 0,
                          'left_menu': True, 'hero_selected': True, 'full_selected': False,
                          'hero_identity': dict(ID), 'hero_chapter': 1}}


def confirmed():
    m = memory()
    assert p._egg_recovery_step(native('fee'), m, m['month_sub']) == [p.pad('a')]
    assert m['month_sub']['confirm_identity'] == ID
    return m


def test_original_return_list_closes_with_b_instead_of_reopening_full_quote():
    m = confirmed(); sub = m['month_sub']; before = deepcopy(m['egg_uses'])
    assert p.month_sub_step(native('return-list'), m) == [p.pad('b')]
    assert not sub.get('aborted') and not sub.get('payment_interrupted')
    assert sub['quoted_cost'] == 50 and sub['hero_selected'] is True
    assert sub['full_selected'] is False and m['egg_uses'] == before
    assert not m['shop'].get('egg_paid_actors')
    assert m['_records'][-1]['observed_metric']['debit_matches'] is True
    assert p._finish_month_sub(native('month'), m, m['shop'])
    assert m['_records'][-1]['observed_metric']['payment_verified'] is True
    assert m['shop']['egg_paid_actors'] == [p.NAME]
    assert p.NAME not in m['egg_uses']  # absent from list is not an invented 4
    assert m['egg_uses']['ココット'] == 0 and m['egg_zero_owners'] == ['ココット']
    assert m['shop']['reserve'] > 0 and m['shop']['soldiers'] == 0


def test_saved_return_is_idempotent_and_does_not_confirm_another_purchase():
    m = confirmed()
    assert p.month_sub_step(native('return-list'), m) == [p.pad('b')]
    m = json.loads(json.dumps(m))
    assert p.month_sub_step(native('return-list'), m) == [p.pad('b')]
    assert len([r for r in m['_records'] if r['decision'] == 'egg_recover_list_return']) == 1
    assert not m['shop'].get('egg_paid_actors')


@pytest.mark.parametrize('bad', ['run', 'chapter', 'sub_month', 'shop_month', 'header_month',
                                 'missing_confirm', 'wrong_debit', 'duplicate_list',
                                 'mixed_hero_full', 'mixed_hero_individual', 'changed_actor'])
def test_unbound_or_foreign_return_closes_but_cannot_create_payment(bad):
    m = confirmed(); s = native('return-list'); sub = m['month_sub']
    if bad == 'run': m['_run_identity']['lease_id'] = 'other'
    elif bad == 'chapter': m['chapter'] = 2
    elif bad == 'sub_month': sub['key'] = '1-8'
    elif bad == 'shop_month': m['shop']['key'] = '1-8'
    elif bad == 'header_month': s.header['month'] = 8
    elif bad == 'missing_confirm': sub.pop('confirm_identity')
    elif bad == 'wrong_debit': s.header['gold'] = 34
    elif bad == 'duplicate_list': s.lines.append(deepcopy(s.lines[0]))
    elif bad == 'mixed_hero_full': sub['full_selected'] = True
    elif bad == 'mixed_hero_individual': sub['individual_selected'] = True
    else:
        sub.update(hero_selected=False, individual_selected=True, individual_actor='ココット',
                   individual_identity=dict(ID), individual_chapter=1)
    if bad == 'duplicate_list':
        line = next(l for l in s.lines if l.y == 47)
        s.lines[-1] = deepcopy(line)
    before = deepcopy(m['egg_uses'])
    assert p._egg_recovery_step(s, m, sub) == [p.pad('b')]
    assert sub.get('payment_interrupted') is True and m['egg_uses'] == before
    p._finish_month_sub(native('month'), m, m['shop'])
    assert not m['shop'].get('egg_paid_actors')
    assert m['_records'][-1]['observed_metric']['payment_verified'] is False


def test_fading_header_closes_list_without_inventing_a_fee_then_checks_quiet_month():
    m = confirmed(); s = native('return-list'); s.header = None
    assert p._egg_recovery_step(s, m, m['month_sub']) == [p.pad('b')]
    assert not m['month_sub'].get('payment_interrupted')
    assert m['_records'][-1]['observed_metric']['debit_matches'] is False
    assert not m['shop'].get('egg_paid_actors')
    assert p._finish_month_sub(native('month'), m, m['shop'])
    assert m['_records'][-1]['observed_metric']['payment_verified'] is True


def test_unpaid_quote_fade_still_waits_and_ritual_dialogue_still_advances():
    m = confirmed(); before = deepcopy(m['egg_uses'])
    assert p._egg_recovery_step(native('fee'), m, m['month_sub']) == []
    c = Canvas(); c.text(24, 183, 'アマステ!')
    assert p._egg_recovery_step(parse(c.frame()), m, m['month_sub']) == [p.pad('a')]
    assert m['egg_uses'] == before and not m['shop'].get('egg_paid_actors')


@pytest.mark.parametrize('label', ['うむッ!', 'いかんッ!'])
def test_partial_quote_fade_with_list_in_background_never_repeats_a(label):
    m = confirmed(); c = Canvas(); c.text(32, 47, 'ぜんかいふく')
    c.text(184, 183, label); c.hand(162, 177)
    assert p._egg_recovery_step(parse(c.frame()), m, m['month_sub']) == []
    assert not m['month_sub'].get('aborted')


def test_full_confirmation_fade_does_not_repeat_a_either():
    m = confirmed(); m['month_sub'].update(full_selected=True, hero_selected=False)
    assert p._egg_recovery_step(native('fee'), m, m['month_sub']) == []


def test_general_zero_remains_zero_when_omitted_from_a_paid_return_list():
    m = confirmed(); sub = m['month_sub']
    sub.update(hero_selected=False, individual_selected=True, individual_actor='ココット',
               individual_identity=dict(ID), individual_chapter=1, confirm_actor='ココット')
    assert p._egg_recovery_step(native('return-list'), m, sub) == [p.pad('b')]
    assert p._finish_month_sub(native('month'), m, m['shop'])
    assert m['shop']['egg_paid_actors'] == ['ココット']
    assert p._own_egg_empty(m, {'ally': 'ココット'})
    assert m['egg_uses'][p.NAME] == 3 and m['egg_uses']['ゼウス'] == 3


def test_new_readable_other_zero_overwrites_old_positive_and_is_not_paid_for():
    m = confirmed(); c = Canvas(); c.text(48, 15, '1ねん 9のつき 35G')
    c.text(32, 47, 'ぜんかいふく'); c.text(32, 71, 'ゼウス'); c.text(104, 71, '0'); c.hand(10, 41)
    assert p._egg_recovery_step(parse(c.frame()), m, m['month_sub']) == [p.pad('b')]
    assert m['egg_uses']['ゼウス'] == 0 and 'ゼウス' in m['egg_zero_owners']
    assert not m['shop'].get('egg_paid_actors')


@pytest.mark.parametrize('quantity', ['0', '1', '2', '3', '4', None])
def test_selected_actor_still_listed_cannot_receive_another_actors_50g_debit(quantity):
    m = confirmed(); c = Canvas(); c.text(48, 15, '1ねん 9のつき 35G')
    c.text(32, 47, 'ぜんかいふく'); c.text(32, 71, p.NAME)
    if quantity is not None: c.text(104, 71, quantity)
    # Zeus disappeared instead of the confirmed hero. Do not attribute that
    # disappearance or an unrelated identical debit to the hero.
    c.text(32, 95, 'アルファルファ'); c.text(104, 95, '2'); c.hand(10, 41)
    assert p.month_sub_step(parse(c.frame()), m) == [p.pad('b')]
    assert m['month_sub'].get('payment_interrupted') is True
    assert p._finish_month_sub(native('month'), m, m['shop'])
    assert not m['shop'].get('egg_paid_actors')
    assert m['_records'][-1]['observed_metric']['payment_verified'] is False
    assert m['egg_uses'][p.NAME] == (3 if quantity is None else int(quantity))
    assert m['egg_uses']['ゼウス'] == 3 and m['egg_uses']['ココット'] == 0
    assert m['shop']['reserve'] == 200


def test_duplicate_selected_actor_is_not_a_payment_receipt_or_a_new_quantity():
    m = confirmed(); c = Canvas(); c.text(48, 15, '1ねん 9のつき 35G')
    c.text(32, 47, 'ぜんかいふく')
    for y, q in ((71, '3'), (95, '0')):
        c.text(32, y, p.NAME); c.text(104, y, q)
    c.hand(10, 41)
    assert p.month_sub_step(parse(c.frame()), m) == [p.pad('b')]
    assert m['month_sub'].get('payment_interrupted') is True
    assert m['egg_uses'][p.NAME] == 3
    assert p._finish_month_sub(native('month'), m, m['shop'])
    assert not m['shop'].get('egg_paid_actors')


@pytest.mark.parametrize('quantity', ['0', '1', None])
def test_bound_general_still_present_is_not_paid_and_keeps_actual_quantity(quantity):
    from test_hanjuku_native_individual_quote import selected, native as old_native, ready
    m = selected()
    assert p._egg_recovery_step(old_native('fee'), m, m['month_sub']) == [p.pad('a')]
    c = Canvas(); c.text(48, 15, '3ねん 12のつき 165G')
    c.text(32, 47, 'ぜんかいふく'); c.text(32, 71, 'ココット')
    if quantity is not None: c.text(104, 71, quantity)
    c.hand(10, 41)
    assert p.month_sub_step(parse(c.frame()), m) == [p.pad('b')]
    assert m['month_sub'].get('payment_interrupted') is True
    assert p._finish_month_sub(ready(), m, m['shop'])
    assert not m['shop'].get('egg_paid_actors') and m['shop']['reserve'] == 250
    assert m['egg_uses']['ココット'] == (0 if quantity is None else int(quantity))
    assert p._own_egg_empty(m, {'ally': 'ココット'}) is (quantity != '1')
    assert m['egg_uses']['ゼウス'] == 3
