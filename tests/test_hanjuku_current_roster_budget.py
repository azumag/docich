"""Real monthly loop; new troop purchases are counterfactual, not live receipts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle, month
from docich.hanjuku.strategy import economy
from docich.hanjuku.os_layer import roster
from docich.hanjuku_font import TextLine
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_soldier_receipts import quantity

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
E = json.loads((ROOT / 'g747-month-refill-evidence.json').read_text())


def memory():
    return deepcopy(E['snapshot'])


def native(uid):
    e = next(e for e in E['evidence'] if e['decision']['decision_id'].endswith(':' + str(uid)))
    f = read_png(ROOT / e['fixture'])
    assert f.digest() == e['rgb_sha256'] == e['decision']['frame_sha256']
    return parse(f)


def editor(value=50, gold=70):
    s = quantity(gold)
    s.header.update(year=2, month=4)
    tens, ones = divmod(value, 10)
    s.lines = [TextLine(199, tuple((24+i*8, ch) for i, ch in enumerate('かわりますぞ'))
                             + ((216, str(tens)), (224, str(ones))))]
    return s  # synthetic editor, never a native new payment


def test_native_free_count_and_exit_refusal_have_exact_inputs():
    assert 'へいしすうは0めい' in native(9154).text.replace(' ', '')
    # The count's A and the exit-cursor Down are exact. The later cancel A
    # shares an ID but not the current frame, so exclude it from that claim.
    cancel = next(e for e in E['evidence'] if e['decision']['decision_id'].endswith(':9337'))
    assert not any(r['frame_sha256'] == r['decision_frame_sha256'] == cancel['rgb_sha256']
                   for r in cancel['input_receipts'])
    for uid, button in [(9154, 'a'), (9336, 'down')]:
        e = next(e for e in E['evidence'] if e['decision']['decision_id'].endswith(':' + str(uid)))
        assert any(r['buttons'] == [button] and r['decision_id'] == e['decision']['decision_id']
                   and r['frame_sha256'] == r['decision_frame_sha256'] == e['rgb_sha256']
                   for r in e['input_receipts'])
    assert native(9337).selected == 'いかんッ!'


def test_current_complete_roster_releases_ghost_reserve_then_opens_real_zero_refill():
    mem = memory(); stock = deepcopy(mem['egg_uses']); recheck = deepcopy(mem['egg_recheck'])
    assert roster.completed_survey(mem) is not None
    assert economy._egg_recovery_wage_floor(mem) == 6
    assert p.month_step(native(9331), mem) == [p.pad('down')]
    assert mem['shop']['reserve'] == 0 and mem['shop']['soldiers'] == 50
    assert mem['shop']['soldiers_done'] is False and not mem['shop']['closed']
    assert p.month_step(native(9332), mem) == [p.pad('a')]
    assert mem['army_count_check']['refill_requested'] is True
    assert mem['egg_uses'] == stock and mem['egg_recheck'] == recheck
    assert mem['soldiers_seen'] == 0  # opening is not payment/new troop count
    assert not mem['shop'].get('soldier_receipt')
    assert any(q['decision'] == 'egg_reserve_roster_reconciled' for q in mem['_records'])


def test_editor_clamps_saved_plan_before_payment_and_paid_receipt_is_not_repeated():
    mem = memory(); p.month_step(native(9331), mem); p.month_step(native(9332), mem)
    mem['shop']['soldiers'] = 99  # saved optimistic quantity cannot spend all cash
    assert p.quantity_step(editor(), mem, soldiers=True) == [p.pad('a')]
    r = mem['shop']['soldier_receipt']
    assert r['qty'] == 50 and r['gold_before'] == 70 and r['status'] == 'pending'
    assert mem['soldiers_seen'] == 0
    pending = deepcopy(r)
    assert p.quantity_step(editor(), mem, soldiers=True) == []
    assert mem['shop']['soldier_receipt'] == dict(pending, quantity_waits=1)
    # Synthetic matching debit proves only this test's settlement logic.
    returned = native(9332); returned.header['gold'] = 20
    p.observe_events(returned, mem)
    assert r['status'] == 'paid'
    before = deepcopy(r)
    mem = json.loads(json.dumps(mem))
    assert p.quantity_step(editor(gold=20), mem, soldiers=True) == [p.pad('b')]
    assert mem['shop']['soldier_receipt'] == before


def test_absent_zero_and_recheck_history_remain_and_reappear_when_membership_expires():
    mem = memory(); mem['egg_uses']['アルテミス'] = 0; mem['egg_zero_owners'] = ['アルテミス']
    before = deepcopy(mem)
    economy._refresh_egg_priority(mem, mem['shop'], native(9331).header)
    assert economy._egg_recovery_targets(mem) == []
    assert mem['egg_uses'] == before['egg_uses'] and mem['egg_recheck'] == before['egg_recheck']
    assert p._own_egg_empty(mem, {'ally': 'アルテミス'})
    assert mem['egg_zero_owners'] == ['アルテミス']
    roster.invalidate(mem)
    assert 'アルテミス' in economy._egg_recovery_targets(mem)


@pytest.mark.parametrize('change', ['partial', 'foreign', 'month', 'chapter', 'malformed',
                                  'duplicate', 'new_scan', 'pending_employee'])
def test_unconfirmed_membership_keeps_owners_but_skipped_recovery_may_fund_troops(change):
    mem = memory(); survey = mem['roster_survey']
    if change == 'partial': survey['receipt']['complete'] = False
    elif change == 'foreign': mem['_run_identity']['lease_id'] = 'foreign'
    elif change == 'month': survey['receipt']['month'] = '2-3'
    elif change == 'chapter': survey['receipt']['chapter'] = 2
    elif change == 'malformed': mem['roster_survey'] = True
    elif change == 'duplicate': survey['receipt']['names'].append(p.NAME)
    elif change == 'new_scan': mem['house'] = {'roster_started': mem['tick']}
    else: mem['recruit_payroll_pending'] = ['シナモン']
    stock = deepcopy(mem['egg_uses']); recheck = deepcopy(mem['egg_recheck'])
    targets = economy._egg_recovery_targets(mem)
    assert 'アルテミス' in targets
    header = native(9331).header
    economy._refresh_egg_priority(mem, mem['shop'], header)
    # A budget reallocation is never proof of roster membership or recovery.
    assert mem['egg_uses'] == stock and mem['egg_recheck'] == recheck
    assert economy._egg_recovery_targets(mem) == targets
    assert mem['shop']['egg_priority'] and mem['shop']['egg'] == 'skipped'
    assert not any(r['decision'] == 'egg_reserve_roster_reconciled' for r in mem.get('_records', []))
    if change in ('foreign', 'pending_employee'):
        assert mem['shop']['reserve'] >= 40  # no current army / unresolved payroll
    else:
        # Owner 2026-10-15: this fresh measured zero army may be refilled
        # after a known skip even while historical egg membership is unknown.
        assert mem['shop']['reserve'] == 0
        assert 0 < mem['shop']['soldiers'] <= 50
        assert header['gold'] - mem['shop']['soldiers'] >= economy._soldier_wage_hold(mem)
        assert mem['soldiers_seen'] == 0 and not mem['shop'].get('soldier_receipt')
        assert any(r['decision'] == 'soldier_refill_after_egg_skip' for r in mem['_records'])


@pytest.mark.parametrize('flag', ['opened', 'unverified', 'tracked'])
def test_pending_recovery_keeps_its_existing_reservation(flag):
    mem = memory()
    if flag == 'tracked': mem['month_sub'] = {'kind': 'egg'}
    else: mem['shop']['egg'] = flag
    economy._refresh_egg_priority(mem, mem['shop'], native(9331).header)
    assert mem['shop']['reserve'] == 40 and mem['shop']['egg_priority']


def test_actual_current_member_low_stock_waits_behind_a_zero_army_refill():
    # Owner (2026-10-17): twenty or fewer soldiers are refilled before eggs,
    # the hero's included. The recovery is not paid, released or forgotten.
    mem = memory(); mem['egg_uses'][p.NAME] = 0
    assert p.NAME in economy._egg_recovery_targets(mem)
    shop = mem['shop']; reserve = shop['reserve']
    economy._low_army_refill(mem, shop, native(9331).header)
    assert shop['soldiers'] == 50 and shop['soldiers_done'] is False
    assert not shop.get('soldier_receipt')
    assert shop['reserve'] == reserve and shop['egg'] == 'skipped'
    assert p.NAME in economy._egg_recovery_targets(mem)
    assert p._own_egg_empty(mem, {'ally': p.NAME})


def test_independent_pre_recruit_probe_is_preserved_without_claiming_eggs_full():
    mem = memory(); mem['shop']['egg_before_recruit'] = True
    economy._refresh_egg_priority(mem, mem['shop'], native(9331).header)
    assert mem['shop']['egg'] == 'check'  # not inferred not_needed/done
    assert economy._egg_before_soldiers(mem['shop'])


@pytest.mark.parametrize('gold,reserve,expected', [(70,40,24), (70,0,64), (45,40,0)])
def test_known_full_six_gold_payroll_is_the_same_before_and_at_editor(gold, reserve, expected):
    mem = memory(); shop = mem['shop']; shop.update(reserve=reserve, egg='done', egg_priority=False)
    h = {'year':2, 'month':4, 'gold':gold}
    assert economy._soldier_spendable(mem, shop, h) == expected
    shop.update(soldiers_target=50, soldiers=99, soldiers_done=False)
    month._recalc_soldiers(editor(gold=gold), mem, shop)
    assert shop['soldiers'] == min(50, expected)
    shop.update(soldiers=99, soldiers_done=False)
    economy._limit_soldier_refill(mem, shop, h, payment=True)
    assert shop['soldiers'] == min(50, expected)


def test_complete_large_bill_keeps_the_shortfall_floor_and_every_other_reserve():
    mem = memory(); roster.invalidate(mem)
    names = ['どうし','ゼウス','ヴィーナス','ココット','シナモン','ミント']
    mem['recruit_roster'] = {'chapter':1, 'month':'2-4', 'identity':deepcopy(mem['_run_identity']),
        'tick':mem['tick'], 'complete':True, 'names':names,
        'wages':dict(zip(names, [0,15,15,15,15,2]))}
    assert economy._egg_recovery_wage_floor(mem) == 62
    # Owner (2026-10-14): a measured army is not starved by reserving the whole
    # payroll; a refill keeps the documented shortfall floor in hand instead.
    assert economy._soldier_wage_hold(mem) == economy.WAGE_RESERVE
    shop = mem['shop']; shop.update(reserve=50, items=[['イッテツーン', 1]],
        merchant_done=False, general_repair_reserve=50, hero_repair_reserve=0, recruit_reserve=42)
    price = p.KNOWN_PRICES['イッテツーン']
    assert economy._soldier_spendable(mem, shop, {'year':2,'month':4,'gold':300}) \
        == 300-economy.WAGE_RESERVE-50-50-42-price
    assert economy._soldier_spendable(mem, shop, {'year':2,'month':4,'gold':70}) == 0


def test_measured_zero_army_is_refilled_past_a_large_payroll():
    """g749 4-10: 99G in hand and a 90G payroll refilled only 9 of 50 soldiers.

    The measured 0 army must reach the target while the egg and repair reserves
    keep their own money.
    """
    mem = large_payroll_memory()
    shop = mem['shop']
    shop.update(egg='not_needed', egg_priority=False, reserve=0, recruit_reserve=0,
                general_repair_reserve=0, hero_repair_reserve=0, items=[], merchant_done=False,
                soldiers=0, soldier_receipt=False, recruit='not_needed', key='2-4')
    mem['soldiers_seen'] = 0
    mem['soldiers_seen_key'] = '2-4'
    mem['soldiers_seen_scope'] = economy._soldier_count_scope(mem, {'year':2,'month':4,'gold':99})
    economy._low_army_refill(mem, shop, {'year':2,'month':4,'gold':99})
    assert shop['soldiers'] == economy.SOLDIER_ENOUGH
    assert shop['soldiers_target'] == economy.SOLDIER_ENOUGH


@pytest.mark.parametrize('change', ['no_survey', 'partial', 'missing_wage', 'bool_wage', 'foreign'])
def test_unknown_payroll_falls_back_to_thirty(change):
    mem = memory(); roster.invalidate(mem)
    if change == 'no_survey': mem.pop('recruit_roster', None)
    else:
        mem['recruit_roster'] = deepcopy(E['snapshot']['recruit_roster'])
        mem['recruit_roster']['tick'] = mem['tick']
        if change == 'partial': mem['recruit_roster']['complete'] = False
        elif change == 'missing_wage': mem['recruit_roster']['wages'].pop(p.NAME)
        elif change == 'bool_wage': mem['recruit_roster']['wages'][p.NAME] = True
        else: mem['recruit_roster']['identity']['lease_id'] = 'other'
    assert economy._egg_recovery_wage_floor(mem) == 30
    assert economy._soldier_spendable(mem, mem['shop'], {'year':2,'month':4,'gold':70}) == 0


@pytest.mark.parametrize('change', ['gold', 'bool_gold', 'month', 'price', 'qty', 'repair', 'hiring', 'pending'])
def test_unread_or_unpriced_budget_never_confirms_a_saved_quantity(change):
    mem = memory(); shop = mem['shop']; shop.update(egg='done', egg_priority=False,
        reserve=0, soldiers=50, soldiers_done=False)
    s = editor()
    if change == 'gold': s.header['gold'] = None
    elif change == 'bool_gold': s.header['gold'] = True
    elif change == 'month': s.header['month'] = 5
    elif change == 'price': shop.update(items=[['未読',1]], merchant_done=False)
    elif change == 'qty': shop.update(items=[['イッテツーン',True]], merchant_done=False)
    elif change == 'repair': shop['hero_repair_reserve'] = True
    elif change == 'hiring': shop['recruit_reserve'] = '42'
    else: mem['recruit_payroll_pending'] = ['ゼウス']
    assert economy._soldier_spendable(mem, shop, s.header) is None
    assert p.quantity_step(s, mem, soldiers=True) != [p.pad('a')]
    assert not shop.get('soldier_receipt')


def test_new_actual_general_invalidates_membership_without_declaring_a_death():
    from docich.hanjuku_screen import Battle, Screen
    mem = memory(); cur = {'ally':'ゼウス','enemy':'タピオカ'}
    s = Screen(lines=[], hand=None, text='', kind='battle', battle=Battle('タピオカ',50,'ゼウス',85))
    battle._card_battle_reading(s, cur, mem)
    assert roster.completed_survey(mem) is None
    assert mem['egg_uses']['アルテミス'] == E['snapshot']['egg_uses']['アルテミス']


def test_saved_completed_membership_and_pending_payment_keep_their_boundaries():
    mem = json.loads(json.dumps(memory()))
    p.month_step(native(9331), mem)
    assert mem['shop']['soldiers'] == 50
    mem['shop']['soldier_receipt'] = {'status':'pending','qty':50,'gold_before':70,'key':'2-4'}
    before = deepcopy(mem['shop']['soldier_receipt'])
    economy._low_army_refill(mem, mem['shop'], native(9331).header)
    economy._limit_soldier_refill(mem, mem['shop'], native(9331).header)
    assert mem['shop']['soldier_receipt'] == before


def test_saved_done_flag_without_payment_receipt_cannot_confirm_again():
    mem = memory(); mem['shop'].update(egg='done', egg_priority=False, reserve=0,
                                      soldiers=50, soldiers_done=True)
    assert p.quantity_step(editor(), mem, soldiers=True) == [p.pad('b')]
    assert not mem['shop'].get('soldier_receipt')


def large_payroll_memory():
    mem = memory(); survey = mem['roster_survey']; receipt = survey['receipt']
    template = deepcopy(survey['statuses'][p.NAME])
    names = [p.NAME, 'ココット', 'ヴィーナス', 'シナモン', 'ミント', 'パプリカ']
    receipt['names'] = names; receipt['wages'] = dict(zip(names, [0,15,15,15,15,2]))
    survey['statuses'] = {n: dict(template, general=n) for n in names}
    survey['dirty'] = {}; survey.pop('read_revisions', None); survey.pop('dirty_revisions', None)
    assert roster.completed_survey(mem) is not None
    return mem


def test_new_actual_member_keeps_the_shortfall_floor_for_soldiers_and_deduplicates():
    mem = large_payroll_memory()
    assert economy._egg_recovery_wage_floor(mem) == 62
    roster.observed_member(mem, ['ゼウス'])
    assert roster.completed_survey(mem) is None
    assert economy._egg_recovery_wage_floor(mem) == 66
    assert economy._hero_egg_wage_floor(mem) == 66
    roster.observed_member(mem, ['ゼウス'])
    assert roster.payroll_hold(mem) == 66
    mem = json.loads(json.dumps(mem))
    # The egg-recovery floor keeps the whole 66G bill; the soldier refill keeps
    # the documented shortfall floor and the shop's own egg reserve.
    assert economy._soldier_wage_hold(mem) == economy.WAGE_RESERVE
    assert economy._soldier_spendable(mem, mem['shop'], {'year':2,'month':4,'gold':100}) \
        == 100 - economy.WAGE_RESERVE - mem['shop']['reserve']


@pytest.mark.parametrize('change', ['identity', 'month', 'bool', 'oversize', 'shape'])
def test_payroll_hold_rejects_foreign_or_malformed_saved_values(change):
    mem = large_payroll_memory(); roster.observed_member(mem, ['ゼウス'])
    hold = mem['payroll_membership_hold']
    if change == 'identity': hold['identity']['lease_id'] = 'other'
    elif change == 'month': hold['scope'][1] = '2-3'
    elif change == 'bool': hold['wages'][p.NAME] = True
    elif change == 'oversize': hold['wages'][p.NAME] = 99
    else: hold['wages'] = []
    assert roster.payroll_hold(mem) == 0
    assert economy._egg_recovery_wage_floor(mem) == 30


def test_new_actual_recovery_row_restores_owner_priority_without_erasing_zero():
    from docich.hanjuku.flows.egg_recovery import _observe_recovery_list_quantities
    from docich.hanjuku_screen import Screen
    mem = large_payroll_memory(); mem['egg_uses']['ゼウス'] = 0
    s = Screen(lines=[TextLine(55, ((32,'ゼウス'), (104,'0')))], hand=None,text='',kind='unknown')
    _observe_recovery_list_quantities(s, mem, {'key':'2-4'})
    assert roster.completed_survey(mem) is None
    assert economy._egg_recovery_wage_floor(mem) == 66
    assert 'ゼウス' in economy._egg_recovery_targets(mem)
    assert 'ゼウス' in mem['egg_zero_owners']


def test_new_complete_small_payroll_supersedes_and_clears_retained_bill():
    mem = large_payroll_memory(); roster.observed_member(mem, ['ゼウス'])
    original = memory()
    receipt = deepcopy(original['roster_survey']['receipt'])
    receipt['tick'] = mem['tick']; receipt['complete'] = False
    mem['recruit_roster'] = receipt
    mem['house_eggs'] = deepcopy(original['roster_survey']['statuses'])
    for row in mem['house_eggs'].values(): row['observed_tick'] = mem['tick']
    state = {'roster_started':mem['tick'], 'seen':receipt['names']}
    assert roster.complete(mem, state)
    assert 'payroll_membership_hold' not in mem
    assert economy._egg_recovery_wage_floor(mem) == 6


def test_no_debit_after_confirmation_leaves_editor_without_double_payment():
    mem = memory(); p.month_step(native(9331), mem); p.month_step(native(9332), mem)
    assert p.quantity_step(editor(), mem, soldiers=True) == [p.pad('a')]
    for _ in range(3):
        assert p.quantity_step(editor(), mem, soldiers=True) == []
    assert p.quantity_step(editor(), mem, soldiers=True) == [p.pad('b')]
    assert mem['shop']['soldier_receipt']['status'] == 'unverified'
    assert mem['soldiers_seen'] == 0
    assert not mem['shop']['soldier_receipt'].get('payment_seen')
    assert p.quantity_step(editor(), mem, soldiers=True) == [p.pad('b')]
