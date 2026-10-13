"""Recruitment return ownership, with original v206 RGB/input evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows.month import month_sub_step, recruit_input_guard, RECRUIT_RETURN_LIMIT
from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse, Screen
from test_hanjuku_chart_bot import Canvas, paid_recruit_screen, month_canvas

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g749-recruit-return-evidence.json').read_text())


def native(uid):
    row = next(r for r in EVIDENCE['frames'] if r['uid'] == uid)
    f = read_png(ROOT / row['fixture'])
    assert f.digest() == row['rgb_sha256'] == row['decision']['frame_sha256']
    assert any(i['decision_id'] == row['decision']['decision_id'] and
               i['decision_frame_sha256'] == i['frame_sha256'] == f.digest()
               for i in row['input_receipts'])
    return f


def memory(paid=True):
    sub = {'kind': 'recruit', 'key': '1-7', 'gold_before': 232,
           'left_menu': True, 'presses': 15}
    if paid:
        sub.update(recruit_paid_gold=182, paid_candidates=True,
                   candidate_names=['ロックフォール'], joined_names=['ロックフォール'])
    return {'chapter': 1, 'month': '1-7', 'month_sub': sub,
            'shop': {'key': '1-7', 'recruit': 'opened', 'items': [['イッテツーン', 1]],
                     'merchant_done': False, 'soldiers': 50, 'soldiers_done': False,
                     'reserve': 50, 'repair_reserve': 50, 'recruit_reserve': 50},
            'egg_zero_owners': ['ココット'], 'egg_uses': {'ココット': 0}}


@pytest.mark.parametrize('uid', [1883, 1885, 1886])
@pytest.mark.parametrize('paid', [True, False])
def test_native_blank_or_fade_does_not_confirm_or_erase_receipt(uid, paid):
    m = memory(paid); old = deepcopy(m)
    assert month_sub_step(parse(native(uid)), m) == []
    assert m['month_sub'].get('recruit_paid_gold') == old['month_sub'].get('recruit_paid_gold')
    assert m['shop'] == old['shop'] and m['egg_zero_owners'] == old['egg_zero_owners']
    assert m['month_sub']['presses'] == 15
    assert not m['month_sub'].get('paid_receipt_recovered')


def test_native_merchant_cancels_before_paid_receipt_can_revive_old_recruitment():
    m = memory(); m['month_sub']['aborted'] = True; oldshop = deepcopy(m['shop'])
    assert month_sub_step(parse(native(1927)), m) == [policy.pad('b')]
    assert m['month_sub']['aborted'] and m['month_sub']['recruit_foreign_screen']
    assert not m['month_sub'].get('paid_receipt_recovered')
    assert m['month_sub']['recruit_paid_gold'] == 182
    assert m['shop'] == oldshop  # no fabricated buy receipt or removed card reservation


@pytest.mark.parametrize('kind', ['shop_quantity_prompt', 'shop_quantity'])
def test_unrelated_quantity_and_purchase_confirmation_cannot_take_a(kind):
    m = memory()
    s = Screen([], (10, 10, 20, 20), 'おいくつよろしいでっかうむッ!いかんッ!', kind=kind)
    assert month_sub_step(s, m) == [policy.pad('b')]
    assert not m['month_sub'].get('paid_receipt_recovered')


def test_unrelated_soldier_editor_does_not_use_recruit_confirmation():
    m = memory(); s = Screen([], None, 'じゅうじキー', kind='month_menu')
    assert month_sub_step(s, m) == [policy.pad('b')]
    assert not m['shop'].get('soldier_receipt')


def shop_exit(cursor=True):
    c = Canvas(); c.text(16, 175, 'みせじまいでよろしいでっか?')
    c.text(32, 191, 'うむッ!'); c.text(144, 191, 'いかんッ!')
    if cursor: c.hand(12, 185)
    return parse(c.frame())


def test_only_readable_shop_closing_confirmation_may_take_a():
    m = memory(); assert month_sub_step(parse(native(1927)), m) == [policy.pad('b')]
    assert month_sub_step(shop_exit(False), m) == []
    s = shop_exit(); assert s.kind == 'shop_exit_confirm'
    assert month_sub_step(s, m) == [policy.pad('a')]
    assert m['shop']['items'] == [['イッテツーン', 1]]


@pytest.mark.parametrize('bad_count', [-1, True, '2', None, 999])
def test_invalid_or_exhausted_persisted_budget_does_not_send_keys(bad_count):
    m = memory(); m['month_sub']['return_observations'] = bad_count
    assert month_sub_step(parse(native(1927)), m) == []
    assert m['month_sub']['return_observations'] == RECRUIT_RETURN_LIMIT


def test_json_roundtrip_keeps_finite_budget_and_no_blind_orchestrator_fallback():
    f = native(1886); state = {'policy': memory(), 'name_entered': True,
                              'no_input_streak': NO_INPUT_HOLD_MAX,
                              'no_input_frames': [f.digest()]}
    for _ in range(40):
        actions, state = decide(f, json.loads(json.dumps(state)))
        assert actions == []
        assert not any(r['decision'] == 'no_input_fallback' for r in state['_records'])
    assert state['policy']['month_sub']['return_observations'] == RECRUIT_RETURN_LIMIT


def test_real_quiet_return_settles_receipt_before_pending_merchant_navigation():
    m = memory(); month_sub_step(parse(native(1927)), m)
    policy.month_step(parse(native(1936)), m)
    assert 'month_sub' not in m
    assert m['shop']['recruit'] == 'done' and m['shop']['recruit_reserve'] == 0
    assert m['recruit_verification']['candidates'] == ['ロックフォール']
    assert m['recruit_verification']['placement'] == 'unclassified'
    assert m['egg_zero_owners'] == ['ココット'] and m['egg_uses']['ココット'] == 0


def test_fade_then_readable_paid_candidate_still_advances():
    m = memory(False); m['month_sub']['gold_before'] = 158
    assert month_sub_step(parse(native(1885)), m) == []
    assert month_sub_step(paid_recruit_screen(gold=108), m) == [policy.pad('a')]
    assert m['month_sub']['recruit_paid_gold'] == 108


def test_old_paid_intro_abort_recovery_is_preserved_outside_foreign_shop():
    m = memory(False); m['month_sub'].update(gold_before=158, aborted=True)
    c = Canvas(); c.text(16, 15, '1ねん7のつき108G')
    c.text(16, 175, 'おめにかかれてこうえいです')
    assert month_sub_step(parse(c.frame()), m) == [policy.pad('a')]
    assert m['month_sub']['paid_receipt_recovered']


def test_other_flow_and_real_battle_keep_their_own_owner():
    s = parse(native(1885)); m = memory(); m['month_sub']['kind'] = 'egg'
    assert recruit_input_guard(s, m) is None
    m = memory(); assert month_sub_step(Screen([], None, '', kind='battle'), m) is None
    assert 'month_sub' not in m


def test_repeated_blank_logs_only_first_and_bound_and_readable_intro_recovers():
    m = memory(); s = parse(native(1885))
    for _ in range(100):
        m = json.loads(json.dumps(m))
        assert month_sub_step(s, m) == []
    assert len([r for r in m['_records'] if r['decision'] == 'recruit_return_guard']) == 2
    assert month_sub_step(paid_recruit_screen(gold=182), m) == [policy.pad('a')]
