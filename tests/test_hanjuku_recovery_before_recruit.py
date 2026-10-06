"""A recovery check owns the month before any new recruitment payment."""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, _short_recruit_memory, month_canvas


def memory(gold=180, uses=None):
    mem = _short_recruit_memory()
    mem['gold'] = gold
    if uses is not None:
        mem['egg_uses'] = {'ゼウス': uses}
    return mem


@pytest.mark.parametrize('uses', [None, 0, 2, 4])
def test_recruitment_first_checks_recovery_even_when_army_or_egg_quantity_is_unknown(uses):
    mem = memory(uses=uses)
    actions = policy.month_step(parse(month_canvas(180, on='しょうぐんぼしゅう')), mem)
    assert actions == [policy.pad('down')]
    assert mem['shop']['egg'] == 'pending'
    assert 'month_sub' not in mem
    assert policy.month_step(parse(month_canvas(180, on='たまごのかいふく')), mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'


def test_paid_full_recovery_returns_to_the_real_menu_before_recruiting():
    mem = memory(300, uses=0)
    policy.month_step(parse(month_canvas(300, on='たまごのかいふく')), mem)
    mem['month_sub'].update(full_selected=True, quoted_cost=50, left_menu=True)
    assert policy.month_step(parse(month_canvas(250, on='しょうぐんぼしゅう')), mem) == [policy.pad('a')]
    assert mem['shop']['egg'] == 'done'
    assert mem['egg_uses'] == {}
    assert mem['month_sub']['kind'] == 'recruit'


def test_explicit_recovery_not_needed_unlocks_recruitment():
    mem = memory()
    policy.month_step(parse(month_canvas(180, on='たまごのかいふく')), mem)
    screen = parse(month_canvas(180, on='たまごのかいふく'))
    screen.text += 'おはらいのひつようなたまごはありませんぞ'
    assert policy.month_step(screen, mem) == [policy.pad('a')]
    assert mem['shop']['egg'] == 'not_needed'
    assert 'month_sub' not in mem
    assert policy.month_step(parse(month_canvas(180, on='しょうぐんぼしゅう')), mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit'


@pytest.mark.parametrize('gold', [45, 79])
def test_unaffordable_recovery_never_falls_through_to_recruitment_or_reopens_forever(gold):
    mem = memory(gold, uses=0)
    screen = parse(month_canvas(gold, on='しょうぐんぼしゅう'))
    for _ in range(8):
        policy.month_step(screen, mem)
        assert (mem.get('month_sub') or {}).get('kind') != 'recruit'
        assert mem['shop']['egg'] not in ('done', 'not_needed')
    assert mem['shop']['recruit'] == 'deferred_egg'
    assert sum(r['decision'] == 'recruit_deferred_egg' for r in mem['_records']) == 1


def test_unread_recovery_row_is_finite_and_does_not_license_recruitment():
    mem = memory(uses=0)
    screen = parse(month_canvas(180, on='しょうぐんぼしゅう'))
    screen.lines = [line for line in screen.lines if 'たまごのかいふく' not in line.known]
    for _ in range(8):
        policy.month_step(screen, mem)
    assert mem['shop']['egg'] == 'unverified'
    assert mem['shop']['recruit'] == 'deferred_egg'
    assert 'month_sub' not in mem


@pytest.mark.parametrize('status', ['unverified', 'opened'])
def test_lost_or_unverified_recovery_payment_is_not_retried_or_treated_as_done(status):
    mem = memory(uses=0)
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 180})
    shop['egg'] = status
    for _ in range(8):
        policy.month_step(parse(month_canvas(180, on='しょうぐんぼしゅう')), mem)
    assert shop['egg'] == 'unverified'
    assert shop['recruit'] == 'deferred_egg'
    assert 'month_sub' not in mem


def test_payment_delta_on_a_stale_background_is_not_a_recovery_completion_receipt():
    mem = memory(uses=0)
    policy.month_step(parse(month_canvas(180, on='たまごのかいふく')), mem)
    mem['month_sub'].update(full_selected=True, quoted_cost=50, left_menu=False)
    for _ in range(policy.MONTH_SUB_MENU_WAIT + 1):
        policy.month_step(parse(month_canvas(130, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['egg'] == 'unverified'
    assert mem['shop']['recruit'] == 'deferred_egg'
    assert mem['egg_uses'] == {'ゼウス': 0}


def test_unknown_balance_does_not_confirm_recovery_or_open_recruitment():
    mem = memory(uses=0)
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 180})
    screen = parse(month_canvas(180, on='しょうぐんぼしゅう'))
    screen.header = None
    assert policy._month_extra(screen, mem, shop, recruit_only=True) is None
    assert shop['egg'] == 'skipped'
    assert shop['recruit'] == 'deferred_egg'
    assert 'month_sub' not in mem


def test_declined_real_quote_stays_unverified_and_does_not_spend_on_recruitment():
    mem = memory(uses=0)
    policy.month_step(parse(month_canvas(180, on='たまごのかいふく')), mem)
    c = Canvas()
    c.text(72, 15, '1ねん7のつき180G')
    c.text(24, 183, '4こで200Gになりまんな')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    mem['month_sub']['full_selected'] = True
    assert policy.month_sub_step(parse(c.frame()), mem) == [policy.pad('b')]
    policy.month_step(parse(month_canvas(180, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['egg'] == 'unverified'
    assert mem['shop']['recruit'] == 'deferred_egg'


def test_new_month_retries_a_previously_deferred_recovery_before_recruitment():
    mem = memory(79, uses=0)
    policy.month_step(parse(month_canvas(79, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'deferred_egg'
    mem['month'] = '1-8'
    mem['tick'] += 1
    mem['recruit_roster'].update(month='1-8', tick=mem['tick'])
    mem['castle_income']['アルマムーン'].update(month='1-8', tick=mem['tick'])
    assert policy.month_step(parse(month_canvas(180, on='たまごのかいふく', month=8)), mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'


@pytest.mark.parametrize('count,allowed', [(2, True), (3, False), (4, False)])
def test_one_general_per_owned_castle_adds_the_spare_cash_gate(count, allowed):
    from docich import hanjuku_roster as roster
    names = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット'][:count]
    mem = memory()
    mem['captured'] = ['キカンドン', 'ジョンリギ']
    mem['recruit_roster'].update(names=names, wages={n: roster.fixed_wage(n) for n in names})
    shop = {'merchant_done': True, 'soldiers': 20, 'soldiers_done': False, 'egg': 'not_needed'}
    result, evidence = policy._recruit_spare_after_necessities(mem, shop, 99)
    assert result is allowed
    assert evidence['owned_castles'] == 3 and evidence['generals'] == count
    if count >= 3:
        assert evidence['spare'] == 49
        assert policy._recruit_spare_after_necessities(mem, shop, 100)[0] is True


@pytest.mark.parametrize('gold', [None, False])
def test_spare_cash_is_not_invented_from_an_unknown_or_bool_balance(gold):
    mem = memory()
    shop = {'merchant_done': True, 'soldiers': 0, 'soldiers_done': True, 'egg': 'not_needed'}
    result, evidence = policy._recruit_spare_after_necessities(mem, shop, gold)
    assert result is None and evidence['spare'] is None


def test_unknown_roster_or_castle_names_never_supply_a_confirmed_staffing_count():
    mem = memory()
    shop = {'merchant_done': True, 'soldiers_done': True, 'egg': 'not_needed'}
    mem['recruit_roster']['complete'] = False
    assert policy._recruit_spare_after_necessities(mem, shop, 500)[0] is None
    mem['recruit_roster']['complete'] = True
    mem['captured'] = ['未確認の城']
    assert policy._recruit_spare_after_necessities(mem, shop, 500)[0] is None


def test_staffed_castles_do_not_borrow_the_goninja_1000g_floor():
    mem = memory()
    shop = {'merchant_done': True, 'soldiers': 0, 'soldiers_done': True, 'egg': 'done'}
    assert policy._recruit_spare_after_necessities(mem, shop, 80)[0] is True


@pytest.mark.parametrize('header', [{'gold': 50}, {'gold': 50, 'year': True, 'month': 7},
                                    {'gold': 50, 'year': 1, 'month': None}])
def test_partial_month_header_cannot_complete_a_recovery_or_crash(header):
    from docich.hanjuku_screen import Screen
    mem = memory(100, uses=0)
    mem['month_sub'] = {'kind': 'egg', 'gold_before': 100, 'quoted_cost': 50,
                        'full_selected': True, 'left_menu': True, 'key': '1-7'}
    shop = {'key': '1-7', 'egg': 'opened'}
    assert policy._finish_month_sub(Screen([], None, '', header=header), mem, shop)
    assert shop['egg'] == 'unverified' and mem['egg_uses']['ゼウス'] == 0


def test_spare_gate_preserves_pending_cards_house_repair_and_wages():
    mem = memory()
    shop = {'items': [['ブラッキー', 1]], 'soldiers': 20, 'soldiers_done': False,
            'hero_repair_reserve': 50, 'egg': 'done'}
    held = policy.WAGE_RESERVE + 20 + 50 + policy.KNOWN_PRICES['ブラッキー']
    assert policy._recruit_spare_after_necessities(mem, shop, held + 49)[0] is False
    assert policy._recruit_spare_after_necessities(mem, shop, held + 50)[0] is True


@pytest.mark.parametrize('status', ['check', 'pending'])
def test_staffed_castles_cannot_create_spare_by_cutting_the_soldier_budget(status):
    mem = memory(101)
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 101})
    assert shop['soldiers_before_recruit'] == 71
    shop.update(egg='not_needed', recruit=status)
    policy._month_extra(parse(month_canvas(101, on='しょうぐんぼしゅう')),
                        mem, shop, recruit_only=True)
    assert shop['recruit'] == 'deferred_cash'
    assert shop['soldiers'] == 71 and shop['recruit_reserve'] == 0
    assert 'month_sub' not in mem


def test_a_legacy_recruit_budget_without_its_original_soldier_target_is_not_spare():
    mem = memory(101)
    mem['shop'] = {'key': '1-7', 'items': [], 'merchant_done': True,
                   'soldiers': 21, 'soldiers_done': False, 'recruit_priority': True,
                   'recruit': 'check', 'recruit_budget_version': 2, 'egg': 'not_needed'}
    policy.month_step(parse(month_canvas(101, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['soldiers_before_recruit'] is None
    assert mem['shop']['recruit'] == 'deferred_cash'
    assert (mem.get('month_sub') or {}).get('kind') != 'recruit'


@pytest.mark.parametrize('tracked', [False, True])
def test_unpaid_recruitment_introduction_cannot_bypass_unverified_recovery(tracked):
    from test_hanjuku_chart_bot import recruit_overlay_memory, recruit_overlay_screen
    mem = recruit_overlay_memory()
    mem['shop'].update(egg='unverified', recruit='opened' if tracked else 'unverified')
    mem['egg_uses'] = {'どうし': 0}
    if tracked:
        mem['month_sub'] = {'kind': 'recruit', 'gold_before': 158, 'presses': 0,
                            'key': '1-7', 'left_menu': False}
    screen = recruit_overlay_screen(gold=158)
    step = policy.month_sub_step if tracked else policy.month_step
    assert step(screen, mem) == [policy.pad('b')]
    assert mem['shop']['egg'] == 'unverified'
    assert mem['shop']['recruit'] == 'deferred_egg'


def test_already_paid_recruitment_is_finished_without_guessing_a_refund():
    from test_hanjuku_chart_bot import recruit_overlay_memory, recruit_overlay_screen
    mem = recruit_overlay_memory()
    mem['shop'].update(egg='unverified', recruit='opened')
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 158, 'presses': 0,
                        'key': '1-7', 'left_menu': False}
    assert policy.month_sub_step(recruit_overlay_screen(gold=108), mem) == [policy.pad('a')]
    assert mem['shop']['egg'] == 'unverified'


def test_resuming_a_recruitment_intro_also_obeys_the_extra_cash_gate():
    from test_hanjuku_chart_bot import recruit_overlay_memory, recruit_overlay_screen
    mem = recruit_overlay_memory()
    mem['shop'].update(egg='not_needed', recruit='unverified', soldiers=100, soldiers_done=False)
    assert policy.month_step(recruit_overlay_screen(gold=101), mem) == [policy.pad('b')]
    assert mem['shop']['recruit'] == 'deferred_cash'


def test_cutting_the_soldier_budget_to_zero_is_not_a_completed_refill():
    mem = memory(80)
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 80})
    assert shop['soldiers'] == 0 and shop['soldiers_done'] is True
    assert shop['soldiers_before_recruit'] == 50 and shop['soldiers_before_recruit_done'] is False
    shop['egg'] = 'not_needed'
    policy.month_step(parse(month_canvas(80, on='しょうぐんぼしゅう')), mem)
    assert shop['recruit'] == 'deferred_cash'
    assert shop['soldiers'] == 50 and shop['soldiers_done'] is False
    assert (mem.get('month_sub') or {}).get('kind') != 'recruit'


def test_soldier_restoration_clamps_to_the_real_post_recovery_balance():
    mem = memory(200)
    mem['egg_uses'] = {'ゼウス': 0, 'ココット': 0}
    policy.month_step(parse(month_canvas(200, on='たまごのかいふく')), mem)
    mem['month_sub'].update(full_selected=True, quoted_cost=100, left_menu=True)
    policy.month_step(parse(month_canvas(100, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'deferred_cash'
    assert mem['shop']['soldiers'] == 70
    assert 100 - mem['shop']['soldiers'] == policy.WAGE_RESERVE


def test_cash_deferral_restores_the_same_affordable_budget_when_resuming_intro():
    from test_hanjuku_chart_bot import recruit_overlay_screen
    mem = memory(101)
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 101})
    shop.update(egg='not_needed', recruit='unverified', recruit_measured_budget=True)
    assert policy.month_step(recruit_overlay_screen(gold=101), mem) == [policy.pad('b')]
    assert shop['soldiers'] == 71 and shop['recruit_reserve'] == 0
    assert shop['recruit_priority'] is False


def test_unpaid_tracked_owner_also_uses_the_pretransfer_cash_gate():
    from test_hanjuku_chart_bot import recruit_overlay_screen
    mem = memory(101)
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 101})
    shop.update(egg='not_needed', recruit='opened')
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 101, 'key': '1-7', 'presses': 0}
    assert policy.month_sub_step(recruit_overlay_screen(gold=101), mem) == [policy.pad('b')]
    assert shop['recruit'] == 'deferred_cash' and shop['soldiers'] == 71
    assert shop['recruit_reserve'] == 0
