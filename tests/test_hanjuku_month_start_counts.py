"""Read national totals before monthly purchases; unread counts never recruit."""
from copy import deepcopy

import pytest

from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import roster
from docich.hanjuku.strategy import economy
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import parse
from docich import hanjuku_policy as p
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_level_scan import _country_page1, _country_page2, _main_on, _month_memory
from test_hanjuku_recruit_wage_headroom import _previous_country_memory


IDENTITY = {'game': 'hanjuku-hero', 'generation': 7,
            'runtime_id': 'g7-synthetic', 'lease_id': 'synthetic-lease'}


@pytest.mark.parametrize('block', ['egg', 'complete_roster', 'batch', 'finished_recruit'])
def test_month_start_inspects_counts_before_unopened_purchases(block):
    mem = _month_memory()
    mem.update(_run_identity=deepcopy(IDENTITY), survey_run_identity=deepcopy(IDENTITY),
               egg_uses={'どうし': 0}, egg_recheck=['どうし'])
    mem['shop'] = {'key': '1-7', 'recruit': 'check', 'egg': 'pending'}
    if block == 'complete_roster':
        mem['recruit_roster'] = {'chapter': 1, 'month': '1-7', 'tick': 499,
                                'identity': deepcopy(IDENTITY), 'complete': True,
                                'names': ['どうし', 'ゼウス']}
    elif block == 'batch':
        mem['shop']['recruit_batch'] = {'key': '1-7', 'left': 3}
    elif block == 'finished_recruit':
        mem['shop']['recruit'] = 'done'
    frame = month_canvas(250, on='メインメニュー', month=7)
    actions, state = decide(frame, {'policy': mem}, run_identity=IDENTITY)
    assert actions == [p.pad('a')]
    assert state['policy']['house']['level_only'] is True
    assert state['policy']['house']['month_scan'] is True
    assert not state['policy'].get('month_sub')
    assert not state['policy'].get('army_count_check')


def test_national_counts_replace_old_four_before_recruitment_planning():
    mem = _previous_country_memory(old_count=4)
    # Use the same month as the native synthetic frames below.
    mem.update(month='1-7', tick=500)
    mem['country_general_count']['scope'] = [1, '1-6']
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    main = _main_on('ステータス')
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    page = _country_page1(generals=6, soldiers=0)
    assert house.step(parse(page), mem, page) == [p.pad('a')]
    assert roster.country_count(mem, 'generals') == 6
    assert roster.country_count(mem, 'soldiers') == 0
    assert economy._recruit_headcount(mem) == (6, True, [])
    assert economy._soldier_count_current(mem, parse(page).header)
    assert mem['soldiers_seen'] == 0
    assert not mem.get('shop')  # no stale four-person spending plan was built
    page2 = _country_page2()
    assert house.step(parse(page2), mem, page2) == [p.pad('b')]
    house.step(parse(main), mem, main)
    assert house.step(parse(main), mem, main) == [p.pad('b')]
    assert house.step(parse(menu), mem, menu) == []
    assert 'house' not in mem
    assert house.step(parse(menu), mem, menu) is None
    p.month_step(parse(menu), mem)
    assert economy.low_army_first(mem['shop']) is True
    assert mem['shop']['low_army_refill']['count'] == 0
    assert mem['shop']['soldiers'] >= 50


@pytest.mark.parametrize('source', ['previous_country', 'current_partial', 'garrison'])
def test_failed_monthly_count_does_not_recruit_from_old_or_partial_totals(source):
    mem = _previous_country_memory(old_count=4)
    mem['recruit_month_scan_attempts'] = {'scope': [1, '1-8'], 'count': 2}
    if source == 'current_partial':
        mem['recruit_roster'] = {
            'chapter': 1, 'month': '1-8', 'tick': 109, 'identity': mem['_run_identity'],
            'names': ['どうし', 'ゼウス'], 'complete': False}
    elif source == 'garrison':
        mem['country_general_count'] = None
        mem['garrison'] = {'アルマムーン': ['どうし', 'ゼウス']}
    assert economy._recruit_headcount(mem) == (None, False, [])
    assert economy._recruit_shortage(mem) is None
    shop = {'key': '1-8', 'recruit': 'check', 'recruit_priority': True,
            'merchant_done': True, 'items': [], 'soldiers': 0,
            'soldiers_done': True, 'reserve': 0, 'hero_repair_reserve': 0}
    assert economy._stop_unneeded_recruit(mem, shop) is True
    assert shop['recruit'] == 'deferred_roster'
    spare, evidence = economy._recruit_spare_after_necessities(mem, shop, 1500)
    assert spare is None and evidence['country_count_unverified'] is True
    # The real current count, once read, releases the gate in the same month.
    mem['country_general_count'] = {'scope': [1, '1-8'], 'identity': mem['_run_identity'],
                                    'tick': 110, 'count': 6}
    assert economy._recruit_headcount(mem) == (6, True, [])
    assert economy._recruit_shortage(mem)['count_lower_bound'] == 6


def test_unread_information_row_starts_bounded_inspection_without_a_purchase(monkeypatch):
    mem = _month_memory()
    monkeypatch.setattr(p, 'menu_to', lambda *_args: None)
    menu = month_canvas(250, on='しょうぐんぼしゅう', month=7)
    assert house.step(parse(menu), mem, menu) == []
    assert mem['house']['phase'] == 'month_open'
    assert mem['recruit_month_scan_attempts']['count'] == 1
    assert not mem.get('month_sub')


def test_a_paid_batch_cannot_bypass_an_unverified_monthly_general_count():
    mem = _previous_country_memory(old_count=4)
    mem['recruit_month_scan_attempts'] = {'scope': [1, '1-8'], 'count': 2}
    shop = {'key': '1-8', 'recruit_batch': {'left': 2, 'done': 1}}
    spare, evidence = economy._recruit_spare_after_necessities(mem, shop, 1500)
    assert spare is None and evidence['country_count_unverified'] is True


def test_fresh_count_replaces_an_unpaid_batch_planned_from_four_generals():
    mem = _previous_country_memory(old_count=4)
    shop = economy._plan(mem, {'year': 1, 'month': 8, 'gold': 1500})
    assert shop['recruit_batch']['left'] == 28
    shop['recruit_measured_budget'] = True
    menu = month_canvas(250, on='メインメニュー', month=8)
    assert house.step(parse(menu), mem, menu) == [p.pad('a')]
    main = _main_on('ステータス', month=8)
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    page = _country_page1(month=8, generals=6, soldiers=70)
    assert house.step(parse(page), mem, page) == [p.pad('a')]
    assert 'recruit_batch' not in shop
    assert 'recruit_measured_budget' not in shop
    assert shop['recruit_reserve'] == 0
    economy._prioritise_recruit(mem, shop, 1500)
    # Current 300G income / 150G payroll permit 10 hires, instead of the
    # old 4-person plan's 28. The actual fee and cash gates still apply.
    assert shop['recruit_batch']['left'] == 10
    event = next(r for r in reversed(mem['_records'])
                 if r['decision'] == 'recruit_batch_planned')
    assert event['observed_metric']['total_roster'] == 6


@pytest.mark.parametrize('paid', ['dialogue', 'batch'])
def test_fresh_count_preserves_a_paid_recruit_transaction(paid):
    mem = _previous_country_memory(old_count=4)
    shop = mem['shop'] = {'key': '1-8', 'recruit': 'opened',
                          'recruit_batch': {'left': 3, 'done': 0},
                          'recruit_budget_version': 2, 'recruit_reserve': 150}
    if paid == 'batch':
        shop['recruit'] = 'pending'
        shop['recruit_batch']['done'] = 1
    expected = deepcopy(shop)
    economy._reset_unstarted_recruit_plan(mem)
    assert shop == expected


@pytest.mark.parametrize('paid', ['recruit', 'soldiers'])
def test_inflight_payment_finishes_before_free_inspection(paid):
    mem = _month_memory()
    mem['shop'] = {'key': '1-7', 'recruit': 'opened' if paid == 'recruit' else 'check'}
    if paid == 'soldiers':
        mem['shop']['soldier_receipt'] = {'status': 'pending'}
    menu = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(menu), mem, menu) is None
    assert 'house' not in mem


@pytest.mark.parametrize('bad', ['scope', 'identity', 'future', 'boolean'])
def test_country_count_rejects_foreign_stale_or_invalid_observations(bad):
    mem = _month_memory()
    row = {'scope': [1, '1-7'], 'identity': None, 'tick': 500, 'count': 6}
    mem['country_general_count'] = row
    assert roster.country_count(mem, 'generals') == 6
    if bad == 'scope': row['scope'] = [1, '1-6', 'invalid']
    elif bad == 'identity': row['identity'] = deepcopy(IDENTITY)
    elif bad == 'future': row['tick'] = 501
    else: row['count'] = True
    assert roster.country_count(mem, 'generals') is None
