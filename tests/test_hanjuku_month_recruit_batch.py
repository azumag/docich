"""Owner 2026-11-02: with 1000G or more, a general shortage is filled in one
monthly visit. Synthetic month-menu frames only; no live input is claimed."""
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import month_canvas, _short_recruit_memory


def planned(gold):
    mem = _short_recruit_memory()
    # Monthly totals were checked before this batch was planned. Paid rounds
    # invalidate the named roster, not the country's current-month counts.
    for key, count in (('country_general_count', len(mem['recruit_roster']['names'])),
                       ('country_soldier_count', 70)):
        mem[key] = {'scope': [1, '1-7'], 'identity': None,
                    'tick': mem['tick'], 'count': count}
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



# ---- owner 2026-11-02: 募集1人ごとにステータス確認するのは冗長 ----
# g1097 19:01:09: right after the first general of a batch joined, the monthly
# roster scan (~40 s) ran, although the next round needed no new roster.
def scan_after_payment(recruit_status):
    from docich import hanjuku_house as house
    mem, shop = planned(1200)
    mem['shop'] = shop
    paid_round(mem, shop, 1200, 1150)               # roster evidence invalidated, as in play
    if recruit_status is not None:
        shop['recruit'] = recruit_status
    screen = parse(month_canvas(1150, on='しょうぐんぼしゅう'))
    return house.step(screen, mem, None), mem


def test_no_roster_scan_between_the_rounds_of_an_active_batch():
    actions, mem = scan_after_payment(None)          # next round is 'pending', batch left > 0
    assert actions is None and not mem.get('house')
    assert not mem.get('recruit_month_scan_attempts')


def test_a_stalled_batch_does_not_block_the_roster_scan_for_the_month():
    # A round skipped for cash leaves batch['left'] > 0; the month must still get
    # its roster count rather than none at all.
    actions, mem = scan_after_payment('skipped')
    assert mem.get('house') and actions is not None
    assert mem['recruit_month_scan_attempts']['count'] == 1


def test_after_the_last_round_the_existing_recruit_transaction_gate_still_applies():
    mem, shop = planned(1200)
    mem['shop'] = shop
    for before, after in ((1200, 1150), (1150, 1100), (1100, 1050)):
        paid_round(mem, shop, before, after)
    assert shop['recruit'] == 'done' and shop['recruit_batch']['left'] == 0
    from docich import hanjuku_house as house
    assert house.step(parse(month_canvas(1050, on='しょうぐんぼしゅう')), mem, None) is None
