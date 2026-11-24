"""A recovery quote is paid from current gold; payroll is never reserved.

Issue #2367 (owner 2026-10-20): 給与のための資金温存を廃止する。The measured
payroll remains an observation (``_egg_recovery_wage_floor``) but no longer
blocks a necessary recovery. The real quote and the current balance still do.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from docich import hanjuku_policy as p, hanjuku_roster as r
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ID = {'game': 'hanjuku-hero', 'runtime_id': 'g713-58fb2ff1', 'generation': 713,
      'lease_id': 'b21bde1f-f8bf-49a6-95da-9288f9605f69'}
F = Path(__file__).parent / 'fixtures/hanjuku/g713-recovery-100g-112g.png'


def memory():
    names = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット']
    return {'chapter': 1, 'month': '1-6', 'tick': 1500, '_run_identity': dict(ID),
            'egg_uses': {'どうし': 4, 'ゼウス': 3, 'ヴィーナス': 4, 'ココット': 1},
            'recruit_roster': {'chapter': 1, 'month': '1-6', 'tick': 1482,
                'identity': dict(ID), 'names': names, 'complete': True,
                'wages': {n: r.fixed_wage(n) for n in names}},
            'month_sub': {'kind': 'egg', 'full_selected': True, 'gold_before': 112}}


def test_native_quote_112g_pays_100g_without_reserving_the_measured_8g_payroll():
    frame = read_png(F)
    assert frame.digest() == '33a245a22ad4d7b2401285a760c08651a82d5281d3b1af739f5c48f5d4339e70'
    screen = parse(frame); mem = memory(); before = deepcopy(mem['egg_uses'])
    assert screen.header['gold'] == 112
    assert p._egg_recovery_wage_floor(mem) == 8  # observation only, not a hold
    assert p._egg_recovery_step(screen, mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['quoted_cost'] == 100 and mem['month_sub']['stage'] == 'recovering'
    assert mem['egg_uses'] == before  # planned payment is not recovery
    assert mem['_records'][-1]['decision'] == 'egg_recover_confirm'


@pytest.mark.parametrize('gold,allowed', [(99, False), (100, True), (112, True)])
def test_real_quote_checks_only_the_actual_price_not_the_payroll(gold, allowed):
    screen = parse(read_png(F)); screen.header['gold'] = gold
    mem = memory()
    expected = p.pad('a') if allowed else p.pad('b')
    assert p._egg_recovery_step(screen, mem, mem['month_sub']) == [expected]
    assert bool(mem['month_sub'].get('quoted_cost')) is allowed


@pytest.mark.parametrize('change', ['partial', 'stale', 'month', 'chapter', 'run', 'missing_identity',
                                    'missing_wage', 'bool_wage', 'negative_wage', 'pending_employees'])
def test_unverified_payroll_does_not_block_a_necessary_recovery(change):
    mem = memory(); receipt = mem['recruit_roster']
    if change == 'partial': receipt['complete'] = False
    elif change == 'stale': mem['tick'] = receipt['tick'] + r.FRESH_TICKS
    elif change == 'month': receipt['month'] = '1-5'
    elif change == 'chapter': receipt['chapter'] = 2
    elif change == 'run': receipt['identity']['lease_id'] = 'other'
    elif change == 'missing_identity': mem.pop('_run_identity')
    elif change == 'missing_wage': receipt['wages'].pop('ゼウス')
    elif change == 'bool_wage': receipt['wages']['ゼウス'] = True
    elif change == 'negative_wage': receipt['wages']['ゼウス'] = -1
    else: mem['recruit_payroll_pending'] = ['シェーブル']
    assert p._egg_recovery_step(parse(read_png(F)), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub'].get('quoted_cost') == 100


def test_complete_large_payroll_is_not_reserved_above_the_fallback():
    mem = memory(); receipt = mem['recruit_roster']
    receipt['wages'] = {n: 15 for n in receipt['names']}
    assert p._egg_recovery_wage_floor(mem) == 60
    assert p._egg_recovery_step(parse(read_png(F)), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub'].get('quoted_cost') == 100


def test_existing_hero_repair_reserve_is_still_kept_for_its_own_purpose():
    mem = memory(); mem['shop'] = {'hero_repair_reserve': 50}
    assert p._egg_recovery_step(parse(read_png(F)), mem, mem['month_sub']) == [p.pad('b')]
    assert not mem['month_sub'].get('quoted_cost')
