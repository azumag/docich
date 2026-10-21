"""Issue #2367: no payroll reserve, weakest forced discharge, list-only month."""
import pytest

from docich import hanjuku_house as house, hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku.os_layer import roster as receipts
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.strategy import economy
from docich.hanjuku_reference import general_weakness, weakest_general
from test_hanjuku_chart_bot import Canvas, month_canvas
from test_hanjuku_house import Canvas as HouseCanvas
from test_hanjuku_house import roster as roster_frame


# ---------------------------------------------------------------- weakest
def test_weakest_general_uses_the_documented_order():
    # フェデリーニ: eggless, 8x3; ガルバンゾー: 30x3 with an egg.
    assert weakest_general(['ガルバンゾー', 'フェデリーニ'])[0] == 'フェデリーニ'
    # HP x 戦闘 dominates a mounted/egg distinction when the product differs.
    assert weakest_general(['ゼウス', 'ミント', 'ヴィーナス'])[0] == 'ミント'


def test_equal_hp_and_combat_prefers_the_non_mounted_general():
    key_a, key_b = general_weakness('チキータ'), general_weakness('グリッシーニ')
    assert key_a[:2] == key_b[:2] and key_a[3:5] == key_b[3:5]  # same 74x15
    assert key_a[2] < key_b[2]  # チキータ is not mounted, グリッシーニ is
    assert weakest_general(['グリッシーニ', 'チキータ'])[0] == 'チキータ'




def test_hero_display_name_is_normalized_for_the_weakest_ranking():
    # 一覧の表示名 `どうし` は静的データの `しゅじんこう` と同じ将軍。
    # 別名を解決しないと解雇走査が未解決のまま停止する (#2603)。
    assert general_weakness('どうし') == general_weakness('しゅじんこう')
    assert weakest_general(['どうし', 'ゼウス', 'ヴィーナス', 'ココット'])[0] == 'ココット'
    # 別名を含まない一覧の順位は変わらない。
    assert weakest_general(['ゼウス', 'ヴィーナス', 'ココット'])[0] == 'ココット'

def test_owner_adopted_order_uses_max_hp_and_ignores_egg_type_rank():
    # Same eggless status, same 54 = HP x combat, same non-mounted class:
    # the lower max HP wins even though the egg types differ.
    assert weakest_general(['クミン', 'チコリ', 'レモン'])[0] == 'チコリ'
    # クミン and レモン have identical HP/combat/horse; only the egg type
    # differs and it is not ranked, so the name order is the final rule.
    assert weakest_general(['レモン', 'クミン'])[0] == 'クミン'

def test_unknown_name_never_confirms_a_weakest_candidate():
    target, evidence = weakest_general(['ゼウス', '未読のしょうぐん'])
    assert target is None and evidence['unresolved'] == ['未読のしょうぐん']
    assert general_weakness('未読のしょうぐん') is None


# ---------------------------------------------------------------- discharge
def discharge_frame(names, selected=0, gold=None):
    c = HouseCanvas()
    c.text(16, 15, f'2ねん8のつき{gold or ""}')
    for i, name in enumerate(names):
        c.text(24, 47 + i * 16, name)
    c.text(16, 191, 'どのしょうぐんをかいこに')
    c.hand(2, 41 + selected * 16)
    return c.frame()


def test_forced_discharge_moves_to_the_weakest_not_the_default_cursor():
    names = ['ゼウス', 'ミント', 'ヴィーナス']
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    actions, state = decide(discharge_frame(names, 0), state)
    assert actions == [p.pad('down')]  # ミント is at index 1
    actions, state = decide(discharge_frame(names, 1), state)
    assert actions == [p.pad('a')]
    record = state['_records'][-1]
    assert record['decision'] == 'discharge_general'
    assert record['general'] == 'ミント'
    assert record['strategy_variant'] == 'weakest_general'
    assert record['observed_metric']['ranking']  # the static ranking is recorded
    assert record['observed_metric']['target'] == 'ミント'


def test_after_one_dismissal_the_next_weakest_is_chosen_again():
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    generals = []
    for names, selected in [(['ゼウス', 'ミント', 'ヴィーナス'], 0),
                            (['ゼウス', 'ミント', 'ヴィーナス'], 1),
                            # ミント is gone and the balance is still negative:
                            # ヴィーナス (984) is weaker than ゼウス (1105).
                            (['ゼウス', 'ヴィーナス'], 0),
                            (['ゼウス', 'ヴィーナス'], 1)]:
        actions, state = decide(discharge_frame(names, selected), state)
        generals += [r['general'] for r in state['_records']
                     if r['decision'] == 'discharge_general']
    assert generals == ['ミント', 'ヴィーナス']


def test_unproven_full_page_never_confirms_a_dismissal():
    names = ['フェデリーニ', 'ゼウス', 'ヴィーナス', 'ココット',
             'クミン', 'ミント', 'シナモン', 'バジル']
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    dismissed = []
    for _ in range(30):
        actions, state = decide(discharge_frame(names, 0), state)
        dismissed += [r for r in state['_records']
                      if r['decision'] == 'discharge_general']
    assert dismissed == []
    assert state['_records'] == [] or any(
        r['strategy_variant'] == 'discharge_scan_unproven' for r in state['_records'])


def test_a_candidate_on_a_previous_page_is_reached_with_up():
    first = ['フェデリーニ', 'ゼウス', 'ヴィーナス', 'ココット',
             'クミン', 'ミント', 'シナモン', 'バジル']
    last = ['ヘーゼル', 'ラズベリー']
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    for i in range(8):
        actions, state = decide(discharge_frame(first, i), state)
        assert actions == [p.pad('down')]
    actions, state = decide(discharge_frame(last, 0), state)
    assert actions == [p.pad('up')]  # フェデリーニ is on the previous page
    actions, state = decide(discharge_frame(first, 0), state)
    assert actions == [p.pad('a')]
    record = state['_records'][-1]
    assert record['decision'] == 'discharge_general'
    assert record['general'] == 'フェデリーニ'



def test_hero_in_the_discharge_list_does_not_stall_the_scan():
    names = ['どうし', 'ミント', 'ゼウス']
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    assert decide(discharge_frame(names, 0), state)[0] == [p.pad('down')]
    actions, state = decide(discharge_frame(names, 1), state)
    assert actions == [p.pad('a')]
    record = state['_records'][-1]
    assert record['general'] == 'ミント'

def test_paid_up_discharge_list_leaves_without_another_dismissal():
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    actions, state = decide(discharge_frame(['ゼウス', 'ミント'], 0, gold='32G'), state)
    assert actions == [p.pad('b')]
    assert not [r for r in state['_records'] if r['decision'] == 'discharge_general']


def test_unread_candidate_never_confirms_the_default_cursor():
    c = HouseCanvas()
    c.text(16, 15, '2ねん8のつき')
    c.text(24, 47, 'ミント')
    c.tile(24, 63, 0x0123456789ABCDEF)  # second name unreadable
    c.text(16, 191, 'どのしょうぐんをかいこに')
    c.hand(2, 41)
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    actions, state = decide(c.frame(), state)
    assert actions == []
    assert not [r for r in state['_records'] if r['decision'] == 'discharge_general']
    assert any(r['strategy_variant'] == 'discharge_unverified' for r in state['_records'])


# ---------------------------------------------------------------- payroll
def test_large_measured_payroll_does_not_hold_cash_from_soldiers():
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'x', 'generation': 1, 'lease_id': 'y'}
    names = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット']
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100, '_run_identity': identity,
           'recruit_roster': {'chapter': 1, 'month': '1-7', 'tick': 90,
                              'identity': identity, 'names': names, 'complete': True,
                              'wages': {n: 15 for n in names}}}
    shop = {'key': '1-7', 'items': [], 'merchant_done': True, 'reserve': 40,
            'recruit_reserve': 0, 'egg': 'pending'}
    assert economy._egg_recovery_wage_floor(mem) == 60  # observation only
    assert economy._soldier_spendable(mem, shop, {'year': 1, 'month': 7, 'gold': 70}) == 30


@pytest.mark.parametrize('gold', [60, 74, 75])
def test_recruit_reserve_never_shrinks_the_necessary_soldier_refill(gold):
    from test_hanjuku_soldiers_after_egg_skip import memory as soldier_memory
    from test_hanjuku_army_count_check import screen
    mem = soldier_memory(25, gold)
    shop = mem['shop']
    shop.update(reserve=0, recruit_reserve=50, soldiers=25, soldiers_target=25,
                soldiers_done=False, soldiers_before_recruit=25, egg='not_needed')
    economy._limit_soldier_refill(mem, shop, screen(gold).header)
    # The 50G optional recruit fee is not deducted from the 25 needed troops.
    assert shop['soldiers'] == 25
    assert shop['soldiers_before_recruit'] == 25
    # The optional fee gate therefore sees only gold - necessary soldiers.
    assert gold - shop['soldiers_before_recruit'] == gold - 25


@pytest.mark.parametrize('change', ['income_unread', 'payroll_unread', 'known_deficit'])
def test_necessary_recruitment_opens_without_an_income_or_payroll_proof(change):
    from test_hanjuku_chart_bot import month_canvas, _fewer_generals_than_castles_memory
    mem = _fewer_generals_than_castles_memory()
    if change == 'income_unread':
        mem['castle_income'].clear()
    elif change == 'payroll_unread':
        mem['recruit_roster']['wages'] = {}
    else:
        for row in mem['castle_income'].values():
            row['income'] = 1
        mem['recruit_roster']['wages'] = {n: 15 for n in mem['recruit_roster']['names']}
    plan = p._plan(mem, {'year': 1, 'month': 7, 'gold': 300})
    plan.update(egg='not_needed', merchant_done=True, items=[],
                soldiers=0, soldiers_done=True)
    actions = p.month_step(parse(month_canvas(300, on='しょうぐんぼしゅう', month=7)), mem)
    assert actions == [p.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit'
    assert mem['shop']['recruit'] == 'opened'


# ---------------------------------------------------------------- month list
def month_memory():
    return {'chapter': 1, 'month': '1-7', 'tick': 500, '_records': []}



def test_monthly_scan_reads_names_only_and_never_a_status_screen(monkeypatch):
    from test_hanjuku_recruit_roster import _survey_main
    mem = month_memory()
    state = {'phase': 'open_roster', 'chapter': 1, 'roster_month': '1-7',
             'month_scan': True, 'age': 0, 'total': 0, 'seen': [], 'pending': []}
    mem['house'] = state
    receipts.begin(mem, state)
    monkeypatch.setattr(house, 'general_status', lambda screen: pytest.fail('status screen read'))

    main = _survey_main(7)
    assert house.step(parse(main), mem, main) == [p.pad('a')]
    page = roster_frame(['どうし', 'ゼウス'], 0)
    assert house.step(parse(page), mem, page) == [p.pad('b')]
    assert house.step(parse(main), mem, main) == [p.pad('b')]
    month = month_canvas(250, on='メインメニュー', month=7)
    assert house.step(parse(month), mem, month) == []
    assert mem['recruit_roster']['complete'] is True
    assert mem['recruit_roster']['names'] == ['どうし', 'ゼウス']
    assert not mem.get('house_eggs')


def test_exactly_eight_generals_without_a_native_end_stay_incomplete():
    from test_hanjuku_recruit_roster import _survey_main
    mem = month_memory()
    state = {'phase': 'open_roster', 'chapter': 1, 'roster_month': '1-7',
             'month_scan': True, 'age': 0, 'total': 0, 'seen': [], 'pending': []}
    mem['house'] = state
    receipts.begin(mem, state)
    main = _survey_main(7)
    house.step(parse(main), mem, main)
    names = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'クミン', 'ミント', 'シナモン', 'バジル']
    page = roster_frame(names, 7)
    assert house.step(parse(page), mem, page) == [p.pad('down')]
    assert house.step(parse(page), mem, page) == [p.pad('down')]
    assert house.step(parse(page), mem, page) == [p.pad('b')]
    assert not mem['house'].get('roster_end_observed')
    assert house.step(parse(main), mem, main) == [p.pad('b')]
    month = month_canvas(250, on='メインメニュー', month=7)
    house.step(parse(month), mem, month)
    assert mem['recruit_roster']['complete'] is False
    assert mem['recruit_roster']['names'] == names
    assert any(r['decision'] == 'house_month_roster_end_unproven' for r in mem['_records'])


def test_native_continuation_page_is_not_treated_as_the_end():
    from docich.hanjuku_pixels import read_png
    from pathlib import Path as P
    frame = read_png(P('tests/fixtures/hanjuku-roster-end/g749-11428.png'))
    screen = parse(frame)
    mem = month_memory()
    state = {'phase': 'roster', 'chapter': 1, 'roster_month': '1-7',
             'month_scan': True, 'age': 0, 'total': 0, 'seen': [], 'pending': []}
    mem['house'] = state
    receipts.begin(mem, state)
    actions = None
    for _ in range(3):
        actions = house.step(screen, mem, frame)
    assert actions == [p.pad('b')]
    assert not mem['house'].get('roster_end_observed')
    assert 'ビシソワーズ' not in state['seen']
    house.step(parse(month_canvas(250, on='メインメニュー', month=7)), mem,
               None)
    assert mem['recruit_roster']['complete'] is False


def test_monthly_scan_follows_a_full_page_scroll_and_counts_each_name_once():
    from test_hanjuku_recruit_roster import _survey_main
    mem = month_memory()
    state = {'phase': 'open_roster', 'chapter': 1, 'roster_month': '1-7',
             'month_scan': True, 'age': 0, 'total': 0, 'seen': [], 'pending': []}
    mem['house'] = state
    receipts.begin(mem, state)
    main = _survey_main(7)
    house.step(parse(main), mem, main)
    first = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'クミン', 'ミント', 'シナモン', 'バジル']
    second = ['ヘーゼル', 'ラズベリー']
    for i in range(8):
        page = roster_frame(first, i)
        house.step(parse(page), mem, page)
    page = roster_frame(second, 0)
    assert house.step(parse(page), mem, page) == [p.pad('b')]
    assert house.step(parse(main), mem, main) == [p.pad('b')]
    month = month_canvas(250, on='メインメニュー', month=7)
    house.step(parse(month), mem, month)
    receipt = mem['recruit_roster']
    assert receipt['complete'] is True
    assert receipt['names'] == first + second
    assert len(receipt['names']) == len(set(receipt['names'])) == 10


def test_seventh_required_dismissal_continues_then_paid_up_exits():
    # Seven actual removals; each new complete one-row list proves that the
    # preceding target disappeared. The old six-press cap stopped at Hestia.
    names = ['ココット', 'ガルバンゾー', 'チコリ', 'ミルフィーユ',
             'セルリアク', 'タピオカ', 'ヘスティア']
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    for index, name in enumerate(names):
        actions, state = decide(discharge_frame([name]), state)
        assert actions == [p.pad('a')]
        assert state['policy']['discharge']['presses'] == index + 1
        verified = [r for r in state['_records'] if r['decision'] == 'discharge_verified']
        assert [r['general'] for r in verified] == ([] if index == 0 else [names[index - 1]])
    actions, state = decide(discharge_frame(['ゼウス'], gold='0G'), state)
    assert actions == [p.pad('b')]
    verified = [r for r in state['_records'] if r['decision'] == 'discharge_verified']
    assert [r['general'] for r in verified] == ['ヘスティア']
    assert verified[0]['observed_metric']['basis'] == 'balance_nonnegative'
    assert not any(r['decision'] == 'discharge_general' for r in state['_records'])


def test_full_roster_discharge_budget_is_finite():
    from docich.hanjuku.flows.shop import DISCHARGE_LIMIT
    assert DISCHARGE_LIMIT == 32
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': [],
                       'discharge': {'key': '2-8', 'presses': DISCHARGE_LIMIT, 'exits': 0,
                                     'pending_target': 'ミント'}}}
    actions, state = decide(discharge_frame(['ゼウス']), state)
    assert actions == []
    assert any(r.get('strategy_variant') == 'discharge_limit' for r in state['_records'])
    verified = [r for r in state['_records'] if r['decision'] == 'discharge_verified']
    assert [r['general'] for r in verified] == ['ミント']
    assert verified[0]['observed_metric']['basis'] == 'complete_list_removal'
