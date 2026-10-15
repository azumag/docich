"""A canceled recovery is distinct from an unverified payment on hotload."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import month
from docich.hanjuku.strategy import economy
from docich.hanjuku.strategy.soldier_fallback import DEFERRAL_KEY
from test_hanjuku_army_count_check import screen
from test_hanjuku_soldiers_after_egg_skip import memory


def canceled_memory():
    mem = memory(gold=79)
    mem['shop']['egg'] = 'opened'
    mem['month_sub'] = {'kind': 'egg', 'key': '1-8', 'gold_before': 79,
                        'presses': 3, 'left_menu': True, 'aborted': True,
                        'stage': 'choosing', 'full_selected': True}
    return mem


def test_unpaid_cancel_returns_to_refill_without_claiming_recovered_eggs():
    mem = json.loads(json.dumps(canceled_memory()))
    assert p.month_step(screen(79), mem) == [p.pad('a')]
    assert mem['shop']['egg'] == 'skipped'
    assert mem['shop']['soldiers'] == 40 and not mem['shop']['soldiers_done']
    assert mem['egg_uses'] == {'ココット': 0}
    assert mem['soldiers_seen'] == 10 and not mem['shop'].get('soldier_receipt')
    receipt = next(r for r in mem['_records'] if r['decision'] == 'egg_recover')
    assert receipt['observed_metric']['unpaid_decline_verified'] is True
    assert receipt['observed_metric']['payment_verified'] is False


@pytest.mark.parametrize('change', ['debit', 'unknown_gold', 'month', 'not_aborted',
    'never_left', 'recovering', 'quoted_cost', 'confirm_identity', 'confirm_chapter',
    'confirm_actor', 'interrupted', 'no_identity', 'no_hand'])
def test_ambiguous_return_is_not_a_verified_unpaid_cancel(change):
    mem = canceled_memory()
    sub = mem['month_sub']; s = screen(79)
    if change == 'debit': s.header['gold'] = 78
    elif change == 'unknown_gold': s.header['gold'] = None
    elif change == 'month': sub['key'] = '1-7'
    elif change == 'not_aborted': sub['aborted'] = False
    elif change == 'never_left': sub['left_menu'] = False
    elif change == 'recovering': sub['stage'] = 'recovering'
    elif change.startswith('confirm_') or change == 'quoted_cost': sub[change] = 50
    elif change == 'interrupted': sub['payment_interrupted'] = True
    elif change == 'no_identity': mem.pop('_run_identity')
    else: s.hand = None
    assert not month._unpaid_egg_return(s, mem, mem['shop'], sub)


def test_already_confirmed_ritual_never_releases_money_on_unchanged_balance():
    mem = canceled_memory()
    mem['month_sub'].update(stage='recovering', quoted_cost=50,
                            confirm_identity=deepcopy(mem['_run_identity']))
    p.month_step(screen(79), mem)
    assert mem['shop']['egg'] == 'unverified'
    assert DEFERRAL_KEY not in mem['shop']
    assert not mem['shop'].get('soldier_receipt')


def test_a_fresh_month_can_recheck_eggs_but_cannot_reuse_an_old_deferral():
    mem = memory()
    p.month_step(screen(79), mem)
    previous = deepcopy(mem['shop'][DEFERRAL_KEY])
    mem['month'] = '1-9'
    s = screen(79); s.header['month'] = 9
    shop = economy._plan(mem, s.header)
    assert shop is not None and shop['key'] == '1-9'
    assert DEFERRAL_KEY not in shop
    assert previous['scope'][-1] == '1-8'
    assert mem['egg_uses'] == {'ココット': 0}


def test_fallback_does_not_replenish_an_exhausted_refill_request():
    mem = memory(0)
    scope = deepcopy(mem['soldiers_seen_scope'])
    mem['army_count_check'] = {'scope': scope, 'stage': 'measured'}
    assert p.month_step(screen(79), mem) == [p.pad('a')]
    for _ in range(3):
        assert p.month_step(screen(79), mem) == []
    assert p.month_step(screen(79), mem) == [p.pad('b')]
    for _ in range(6):
        p.month_step(screen(79), mem)
    assert mem['army_count_check']['stage'] == 'unverified'
    assert mem['shop']['soldiers_done'] is True
    assert not mem['shop'].get('soldier_receipt')
    assert sum(r['decision'] == 'soldier_refill_after_egg_skip' for r in mem['_records']) == 1
