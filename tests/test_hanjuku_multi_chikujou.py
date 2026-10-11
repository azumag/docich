"""Per-castle monthly building with measured prices, receipts and bounded input."""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


HOME = 'アルマムーン'
OTHER = 'ジョンリギ'
PROMPT = 'どのしろをぞうちくなさいますか?'


def line(x, y, text):
    return TextLine(y, tuple((x + 8 * i, char) for i, char in enumerate(text)))


def month(gold):
    return Screen([line(48, 79, 'ちくじょう')], (26, 73, 44, 86), 'ちくじょう',
                  header={'year': 1, 'month': 7, 'gold': gold}, kind='month_menu')


def castles(on=HOME, text=PROMPT, names=(HOME, OTHER)):
    rows = [line(48, 47 + i * 16, name) for i, name in enumerate(names)]
    index = names.index(on)
    return Screen(rows, (26, 41 + index * 16, 44, 54 + index * 16),
                  ''.join(names) + text, kind='text')


def quote(castle=HOME, gold=150, cost=5, on='うむッ!'):
    x = 32 if on == 'うむッ!' else 136
    return Screen([line(32, 191, 'うむッ!'), line(136, 191, 'いかんッ!')],
                  (x - 22, 185, x - 4, 198),
                  f'{castle}じょうですな{cost}Gかかりますがよろしいですかなうむッ!いかんッ!',
                  header={'gold': gold}, kind='yes_no')


def result(castle=HOME):
    return Screen([], None, f'{castle}じょうのレベルが2になりましたぞ', kind='text')


def memory(**shop_updates):
    shop = {'key': '1-7', 'chikujou': 'check', 'gold_start': 150,
            'soldiers_done': True, 'merchant_done': True, 'items': [],
            'reserve': 0, 'egg': 'done', 'recruit_reserve': 0,
            **shop_updates}
    return {'chapter': 1, '_records': [], 'captured': [OTHER],
            'garrison': {HOME: ['ゼウス'], OTHER: ['ココット']}, 'shop': shop}


def open_build(mem, gold=150):
    assert policy._month_chikujou(month(gold), mem, mem['shop']) == [policy.pad('a')]
    return mem['month_sub']


def upgrade(mem, castle, gold, cost):
    open_build(mem, gold)
    assert policy.month_sub_step(castles(on=castle), mem) == [policy.pad('a')]
    assert policy.month_sub_step(quote(castle, gold, cost), mem) == [policy.pad('a')]
    assert policy.month_sub_step(result(castle), mem) == [policy.pad('a')]
    assert policy.month_sub_step(castles(on=castle), mem) == [policy.pad('b')]
    assert policy._finish_month_sub(month(gold - cost), mem, mem['shop']) is True


def test_two_owned_castles_are_upgraded_in_one_month_with_separate_receipts():
    mem = memory()
    upgrade(mem, HOME, 150, 5)
    assert mem['shop']['chikujou'] == 'check'
    assert mem['shop']['chikujou_tried'] == [HOME]
    upgrade(mem, OTHER, 145, 9)
    assert mem['shop']['chikujou'] == 'done'
    assert mem['shop']['chikujou_upgrades'] == [
        {'castle': HOME, 'cost': 5, 'gold_before': 150, 'gold_after': 145},
        {'castle': OTHER, 'cost': 9, 'gold_before': 145, 'gold_after': 136}]
    assert policy._month_chikujou(month(136), mem, mem['shop']) is None
    assert len([r for r in mem['_records'] if r['decision'] == 'chikujou_confirm']) == 2


def test_old_month_quote_is_rejected_before_any_payment_confirmation():
    mem = memory()
    mem['month'] = '1-7'
    sub = mem['month_sub'] = {'kind': 'chikujou', 'key': '1-6', 'chosen': HOME,
                              'gold_before': 150, 'presses': 0, 'chikujou_version': 2}
    screen = quote()
    screen.header.update(year=1, month=7)
    policy.observe_events(screen, mem)
    assert policy.month_sub_step(screen, mem) == [policy.pad('b')]
    assert sub['stale_month'] is True
    assert not sub.get('confirm_sent')
    assert not mem['shop'].get('chikujou_upgrades')


def test_paid_castle_is_not_selected_again_even_when_it_still_has_the_hand():
    mem = memory()
    upgrade(mem, HOME, 150, 5)
    sub = open_build(mem, 145)
    assert policy.month_sub_step(castles(on=HOME), mem) == [policy.pad('down')]
    assert not sub.get('chosen')
    assert policy.month_sub_step(castles(on=OTHER), mem) == [policy.pad('a')]
    # A stale confirmation from the previous castle cannot authorize a debit.
    assert policy.month_sub_step(quote(HOME, 150, 5), mem) == []
    assert not sub.get('confirm_sent')
    assert policy.month_sub_step(quote(OTHER, 145, 9), mem) == [policy.pad('a')]


def test_month_controller_reopens_building_after_a_verified_castle_receipt():
    mem = memory()
    assert policy.month_step(month(150), mem) == [policy.pad('a')]
    policy.month_sub_step(castles(), mem)
    policy.month_sub_step(quote(), mem)
    policy.month_sub_step(result(), mem)
    assert policy.month_sub_step(castles(), mem) == [policy.pad('b')]
    assert policy.month_step(month(145), mem) == [policy.pad('a')]
    assert mem['month_sub']['gold_before'] == 145
    assert mem['month_sub']['rows_tried'] == [HOME]
    assert not mem['month_sub'].get('confirm_sent')


@pytest.mark.parametrize('stage', ['selection', 'confirmation', 'result'])
def test_persistent_frames_never_repeat_their_a_press_and_exit_in_finite_time(stage):
    mem = memory()
    sub = open_build(mem)
    frame = castles()
    assert policy.month_sub_step(frame, mem) == [policy.pad('a')]
    if stage != 'selection':
        frame = quote()
        assert policy.month_sub_step(frame, mem) == [policy.pad('a')]
    if stage == 'result':
        frame = result()
        assert policy.month_sub_step(frame, mem) == [policy.pad('a')]
    for _ in range(policy.CHIKUJOU_WAIT_LIMIT - 1):
        assert policy.month_sub_step(frame, mem) == []
    assert policy.month_sub_step(frame, mem) == [policy.pad('b')]
    assert sub['aborted']
    assert policy.month_sub_step(frame, mem) == [policy.pad('b')]


@pytest.mark.parametrize('gold', [None, 150, 144])
def test_unread_stale_or_mismatched_balance_stops_the_batch_without_a_false_receipt(gold):
    mem = memory()
    open_build(mem)
    policy.month_sub_step(castles(), mem)
    policy.month_sub_step(quote(), mem)
    policy.month_sub_step(result(), mem)
    for _ in range(policy.MONTH_SUB_MENU_WAIT - 1):
        assert policy._finish_month_sub(month(gold), mem, mem['shop']) is False
        assert mem['shop']['chikujou'] == 'opened'
    assert policy._finish_month_sub(month(gold), mem, mem['shop']) is True
    assert mem['shop']['chikujou'] == 'unverified'
    assert not mem['shop'].get('chikujou_upgrades')
    assert policy._month_chikujou(month(145), mem, mem['shop']) is None


def test_lagging_header_can_be_reconciled_before_the_next_castle_opens():
    mem = memory()
    open_build(mem)
    policy.month_sub_step(castles(), mem)
    policy.month_sub_step(quote(), mem)
    policy.month_sub_step(result(), mem)
    assert policy._finish_month_sub(month(150), mem, mem['shop']) is False
    assert policy._finish_month_sub(month(145), mem, mem['shop']) is True
    assert mem['shop']['chikujou'] == 'check'


@pytest.mark.parametrize('held', [
    {'egg': 'unverified', 'reserve': 50, 'egg_cost': 50},
    {'hero_repair_reserve': 50},
    {'recruit_reserve': 50},
    {'soldiers_done': False, 'soldiers': 50},
])
def test_actual_quote_cannot_consume_unfinished_necessities(held):
    mem = memory(**held)
    sub = open_build(mem, 120)
    policy.month_sub_step(castles(), mem)
    assert policy.month_sub_step(quote(gold=120, cost=45, on='いかんッ!'), mem) == [policy.pad('a')]
    assert sub['declined'] and not sub.get('confirm_sent')
    assert 'quoted_cost' not in sub
    # A changing header must not turn an already declined choice back to yes.
    assert policy.month_sub_step(quote(gold=200, cost=45), mem) == [policy.pad('b')]


def test_completed_egg_recovery_releases_the_old_estimate_for_building():
    mem = memory(egg='done', reserve=150, egg_cost=150)
    assert policy._month_chikujou(month(100), mem, mem['shop']) == [policy.pad('a')]
    other = memory(egg='unverified', reserve=150, egg_cost=150)
    assert policy._month_chikujou(month(100), other, other['shop']) is None
    assert other['shop']['chikujou'] == 'skipped'


def test_the_actual_remaining_balance_can_end_a_batch_after_one_success():
    mem = memory()
    upgrade(mem, HOME, 70, 5)
    assert mem['shop']['chikujou'] == 'done'
    assert mem['shop']['chikujou_upgrades'][0]['gold_after'] == 65
    assert policy._month_chikujou(month(65), mem, mem['shop']) is None


@pytest.mark.parametrize('refusal', ['これいじょうのぞうちくはできませんぞ!!',
                                     'とほほほしょうぐんがおりませなんだ!'])
def test_unavailable_castle_advances_to_another_owned_castle(refusal):
    mem = memory()
    sub = open_build(mem)
    assert policy.month_sub_step(castles(), mem) == [policy.pad('a')]
    refused = castles(on=OTHER, text=refusal + PROMPT)
    assert policy.month_sub_step(refused, mem) == [policy.pad('a')]
    assert sub['rows_tried'] == [HOME] and sub['chosen'] == OTHER
    assert policy.month_sub_step(refused, mem) == []
    assert sub['rows_tried'] == [HOME]
    assert policy.month_sub_step(quote(OTHER, 150, 9), mem) == [policy.pad('a')]


def test_unowned_or_known_empty_castles_are_not_building_candidates():
    mem = memory()
    mem['captured'] = ['ゴーメン']
    mem['garrison'].update({'ゴーメン': [], 'キカンドン': ['ヴィーナス']})
    assert policy._chikujou_candidates(mem) == [HOME]
    # Old captured-home state must not override positive evidence of its loss.
    mem['home_lost'] = True
    mem['captured'].append(HOME)
    assert policy._month_chikujou(month(200), mem, mem['shop']) is None


def test_unknown_screens_never_receive_a_and_leave_after_a_bounded_wait():
    mem = memory()
    sub = open_build(mem)
    unknown = Screen([], None, '�', kind='text')
    for _ in range(policy.CHIKUJOU_WAIT_LIMIT - 1):
        assert policy.month_sub_step(unknown, mem) == []
    assert policy.month_sub_step(unknown, mem) == [policy.pad('b')]
    assert sub['aborted'] and not sub.get('confirm_sent')


def test_hotloaded_old_quote_is_never_confirmed_again_or_used_as_upgrade_proof():
    mem = memory(chikujou='opened')
    sub = mem['month_sub'] = {'kind': 'chikujou', 'chosen': HOME, 'quoted_cost': 5,
                              'gold_before': 150, 'key': '1-7', 'left_menu': True}
    assert policy.month_sub_step(quote(), mem) == []
    assert not any(r['decision'] == 'chikujou_confirm' for r in mem['_records'])
    assert policy._finish_month_sub(month(145), mem, mem['shop']) is True
    assert mem['shop']['chikujou'] == 'unverified'
    assert not sub.get('upgraded') and not mem['shop'].get('chikujou_upgrades')


def test_hotloaded_old_month_done_is_not_reopened_without_its_castle_receipt():
    mem = memory(chikujou='done')
    assert policy._month_chikujou(month(200), mem, mem['shop']) is None
    assert 'month_sub' not in mem


def test_previous_month_sub_does_not_spend_or_pollute_the_new_months_building_plan():
    mem = memory()
    mem['month_sub'] = {'kind': 'chikujou', 'chosen': HOME, 'quoted_cost': 5,
                        'confirm_sent': True, 'upgraded': True, 'gold_before': 150,
                        'key': '1-6', 'left_menu': True}
    assert policy._finish_month_sub(month(145), mem, mem['shop']) is True
    assert mem['shop']['chikujou'] == 'check'
    assert not mem['shop'].get('chikujou_upgrades') and not mem['shop'].get('chikujou_tried')
    assert mem['_records'][-1]['deviation_reason'] == 'month_changed'


def test_payment_without_matching_castle_result_cannot_start_another_upgrade():
    mem = memory()
    open_build(mem)
    policy.month_sub_step(castles(), mem)
    policy.month_sub_step(quote(), mem)
    assert policy.month_sub_step(result(OTHER), mem) == []
    assert policy._finish_month_sub(month(145), mem, mem['shop']) is True
    assert mem['shop']['chikujou'] == 'unverified'
    assert not mem['shop'].get('chikujou_upgrades')


def test_castle_navigation_has_its_own_finite_budget_above_the_old_eight_frames():
    mem = memory()
    sub = open_build(mem)
    sub['rows_tried'] = [HOME]
    for _ in range(policy.MONTH_SUB_LIMIT + 1):
        assert policy.month_sub_step(castles(on=HOME), mem) == [policy.pad('down')]
    sub['presses'] = policy.CHIKUJOU_SUB_LIMIT
    assert policy.month_sub_step(castles(on=HOME), mem) == [policy.pad('b')]
    assert sub['aborted']
