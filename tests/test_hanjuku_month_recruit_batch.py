"""Owner 2026-11-02: with 1000G or more, a general shortage is filled in one
monthly visit. Synthetic month-menu frames only; no live input is claimed."""
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import month_canvas, _short_recruit_memory


def planned(gold):
    mem = _short_recruit_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': gold})
    shop['egg'] = 'not_needed'  # egg recovery is its own gate and is settled here
    return mem, shop


def paid_round(mem, shop, before, after):
    """One recruit conversation that cost the 50G fee and returned to the menu."""
    shop['recruit'] = 'opened'
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': before, 'presses': 0, 'key': '1-7',
                        'left_menu': True, 'generals_before': ['どうし', 'ゼウス']}
    assert policy._finish_month_sub(parse(month_canvas(after, on='しょうぐんぼしゅう')), mem, shop)


def test_one_visit_recruits_the_whole_shortage_when_cash_is_1000g_or_more():
    mem, shop = planned(1200)
    assert shop['recruit_batch']['left'] == 3 and shop['recruit_reserve'] == 150
    gold = 1200
    for remaining in (2, 1, 0):
        paid_round(mem, shop, gold, gold - 50)
        gold -= 50
        assert shop['recruit_batch']['left'] == remaining
        if remaining:
            # Roster evidence was invalidated by the payment, yet the next
            # round is scheduled from the shortage proven before the first.
            assert shop['recruit'] == 'pending' and shop['recruit_reserve'] == 50 * remaining
            assert mem.get('recruit_roster') is None
        else:
            assert shop['recruit'] == 'done' and shop['recruit_reserve'] == 0
    events = [r['decision'] for r in mem['_records']]
    assert events.count('recruit_batch_continue') == 2 and events.count('recruit_batch_end') == 1


def test_next_round_opens_the_recruit_menu_again_in_the_same_visit():
    mem, shop = planned(1200)
    paid_round(mem, shop, 1200, 1150)
    assert shop['recruit'] == 'pending'
    screen = parse(month_canvas(1150, on='しょうぐんぼしゅう'))
    assert policy._month_extra(screen, mem, shop, recruit_only=True) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit' and shop['recruit'] == 'opened'


def test_below_1000g_recruits_one_general_as_before():
    mem, shop = planned(900)
    assert 'recruit_batch' not in shop and shop['recruit_reserve'] == 50
    paid_round(mem, shop, 900, 850)
    assert shop['recruit'] == 'done' and shop.get('recruit_batch') is None


def test_an_unverified_payment_ends_the_batch_without_another_round():
    mem, shop = planned(1200)
    shop['recruit'] = 'opened'
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 1200, 'presses': 0, 'key': '1-7',
                        'left_menu': True, 'generals_before': ['どうし', 'ゼウス']}
    # The balance did not drop by the fee: nothing was paid, so do not go on.
    assert policy._finish_month_sub(parse(month_canvas(1200, on='しょうぐんぼしゅう')), mem, shop)
    assert shop['recruit_batch']['left'] == 0 and shop['recruit'] != 'pending'
    assert any(r['decision'] == 'recruit_batch_stopped' for r in mem['_records'])


def test_batch_stops_when_the_remaining_cash_cannot_cover_another_fee_and_reserves():
    mem, shop = planned(1200)
    shop['hero_repair_reserve'] = 1110  # reservations now exceed what is left
    paid_round(mem, shop, 1200, 1150)
    assert shop['recruit_batch']['left'] == 0 and shop['recruit'] == 'done'
    assert any(r['decision'] == 'recruit_batch_end' for r in mem['_records'])


def test_single_missing_general_does_not_create_a_batch():
    mem = _short_recruit_memory()
    mem['recruit_roster'].update(names=['どうし', 'ゼウス', 'ヴィーナス', 'ヘルメス'],
                                 wages={'どうし': 0, 'ゼウス': 4, 'ヴィーナス': 4, 'ヘルメス': 4})
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 1200})
    assert shop.get('recruit_batch') is None and shop['recruit_reserve'] == 50
