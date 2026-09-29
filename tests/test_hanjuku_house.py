"""Broken-egg repair contracts using measured layouts and synthetic pixels."""
from unittest.mock import patch

import pytest

from test_hanjuku_chart_bot import Canvas as BaseCanvas, MARK
from docich import hanjuku_house as house, hanjuku_policy as p
from docich.hanjuku_screen import Screen, parse
from docich.hanjuku_bot import decide


class Canvas(BaseCanvas):
    def text(self, x, y, text, color=(255, 255, 255)):
        for i, ch in enumerate(text):
            super().text(x + i * 8, y, 'ウ' if ch == 'ヴ' else ch, color)
            if ch == 'ヴ':
                self.tile(x + i * 8, y - 8, MARK['゛'], color)


def status(name='ゼウス', egg='こわれている', *, main=True, castle=True, gold=250, hp=85, max_hp=85):
    c = Canvas()
    x, y = (24, 39) if main else (16, 31)
    c.text(x, y, name)
    c.text(x + 64, y, 'しょうぐん')
    c.text(x, y + 16, 'HP')
    c.text(x + 40, y + 16, f'{hp}/ {max_hp} P')
    c.text(x, y + 80, 'たまご')
    c.text(x + 32, y + 80, egg)
    if main:
        c.text(56, 7, f'1わ1ねん6のつき{gold}G')
        c.text(24, 151, 'しろのなかにいます' if castle else 'いどうしています')
    return c.frame()


def roster(names=('どうし', 'ゼウス', 'ヴィーナス'), selected=0):
    c = Canvas()
    c.text(56, 7, '1わ1ねん6のつき250G')
    for i, name in enumerate(names):
        c.text(168, 39 + i * 16, name)
    c.text(24, 183, 'Aボタンでステータスひょうじ')
    c.text(24, 199, 'コマンドにもどりますじゃ!')
    c.hand(146, 33 + selected * 16)
    return c.frame()


def gifts(gold=250, selected=0, price=50):
    c = Canvas()
    c.text(72, 15, f'2ねん6のつき{gold}G')
    for i, (name, cost) in enumerate((('ピアス', price), ('みずぎ', 100), ('スカーフ', 200))):
        c.text(168, 167 + i * 16, name)
        c.text(232 - 8 * len(f'{cost}G'), 167 + i * 16, f'{cost}G')
    c.hand(146, 161 + selected * 16)
    return c.frame()


def memory(phase, general='ゼウス', **extra):
    return {'chapter': 1, 'month': '2-6', 'gold': 250, 'tick': 500,
            'house': {'phase': phase, 'chapter': 1, 'age': 0, 'total': 0,
                      'general': general, 'pending': [], 'seen': [], 'inspected': [], **extra}}


def feed(mem, frame):
    screen = parse(frame)
    if screen.header:
        mem['gold'] = screen.header['gold']
    return house.step(screen, mem, frame)


@pytest.mark.parametrize('name', ['どうし', 'ゼウス', 'ヴィーナス', 'エシャロット'])
@pytest.mark.parametrize('main', [True, False])
def test_broken_status_is_bound_to_each_named_general(name, main):
    got = house.general_status(parse(status(name, main=main)))
    assert got['general'] == name and got['broken'] is True
    assert got['uses'] is None


@pytest.mark.parametrize('egg,broken,uses', [('こわれている', True, None),
                                          ('エラベルエッグ0', False, 0),
                                          ('エラベルエッグ4', False, 4), ('なし', False, None)])
def test_zero_uses_and_eggless_are_not_broken(egg, broken, uses):
    got = house.general_status(parse(status(egg=egg)))
    assert (got['broken'], got['uses']) == (broken, uses)


def test_unknown_glyph_cannot_turn_a_partial_egg_row_into_evidence():
    from docich.hanjuku_font import TextLine, UNKNOWN
    screen = parse(status())
    egg = next(row for row in screen.lines if row.y == 119)
    screen.lines[screen.lines.index(egg)] = TextLine(119, tuple(
        (x, UNKNOWN if x == 72 else ch) for x, ch in egg.cells))
    assert house.general_status(screen) is None


@pytest.mark.parametrize('gold,want', [(None, None), (79, None), (80, ('ピアス', 50)),
                                     (129, ('ピアス', 50)), (130, ('みずぎ', 100)),
                                     (229, ('みずぎ', 100)), (230, ('スカーフ', 200))])
def test_purchase_budget_always_keeps_wages(gold, want):
    assert house.affordable_gift(gold, p.WAGE_RESERVE) == want


def test_nonhero_is_included_in_roster_scan_and_healthy_hero_is_skipped():
    mem = memory('roster')
    assert feed(mem, roster()) == [p.pad('a')]
    assert feed(mem, status('どうし', 'エラベルエッグ4')) == [p.pad('b')]
    assert feed(mem, roster()) == [p.pad('down')]
    feed(mem, roster(selected=1))
    assert feed(mem, roster(selected=1)) == [p.pad('a')]
    assert feed(mem, status('ゼウス')) == [p.pad('b')]
    assert mem['house']['seen'] == ['どうし', 'ゼウス']
    assert mem['house']['pending'] == ['ゼウス']


def test_delayed_roster_cursor_is_not_mistaken_for_full_roster_wrap():
    mem = memory('roster_next', selected='どうし', seen=['どうし'])
    feed(mem, roster())
    feed(mem, roster())
    assert mem['house']['phase'] == 'roster_advance'
    feed(mem, roster(selected=1))
    assert mem['house']['phase'] == 'roster'


def test_only_exact_house_gifts_route_to_purchase():
    assert house.gift_prices(parse(gifts())) == dict(house.GIFTS)
    assert house.gift_prices(parse(gifts(price=55))) is None
    mem = memory('travel')
    feed(mem, gifts())
    assert mem['house']['phase'] == 'buy'
    assert mem['house']['purchase']['item'] == 'スカーフ'


def test_purchase_is_sent_once_and_needs_money_plus_named_status_to_verify():
    mem = memory('travel')
    feed(mem, gifts())
    assert feed(mem, gifts(selected=2)) == [p.pad('a')]
    assert feed(mem, gifts(selected=2)) == []
    assert not any(r['decision'] == 'house_repair_verified' for r in mem['_records'])
    mem['house']['phase'] = 'verify_status'
    feed(mem, status(egg='ワンダーエッグ4', gold=50))
    assert mem['house']['purchased'] is True
    assert mem['egg_uses']['ゼウス'] == 4
    result = next(r for r in mem['_records'] if r['decision'] == 'house_repair_verified')
    assert result['observed_metric']['gold_after'] == 50


@pytest.mark.parametrize('egg,gold', [('こわれている', 50), ('ワンダーエッグ4', 250)])
def test_no_success_on_deduction_alone_or_unpaid_recovery(egg, gold):
    mem = memory('verify_status', purchase={'item': 'スカーフ', 'cost': 200,
                                           'gold_before': 250, 'month': '2-6'})
    feed(mem, status(egg=egg, gold=gold))
    assert not mem['house']['purchased']
    assert mem['_records'][-1]['decision'] == 'house_repair_unconfirmed'


def test_wrong_general_status_does_not_confirm_repair():
    mem = memory('verify_status', purchase={'cost': 200, 'gold_before': 250, 'month': '2-6'})
    assert feed(mem, status('ヴィーナス', 'カラフルエッグ4', gold=50)) == []
    assert mem['house']['phase'] == 'verify_status'


def test_castle_repair_will_not_empty_even_one_of_many_owned_castles():
    mem = memory('castle_pick', source='ほんじょう')
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    c.text(144, 39, 'ゼウス')
    c.hand(122, 33)
    feed(mem, c.frame())
    assert mem['house']['phase'] == 'close'
    assert mem['_records'][-1]['decision'] == 'house_deferred'


def test_house_navigation_does_not_confirm_unread_or_far_cursor():
    mem = memory('house_view')
    screen = Screen([], None, '', kind='world_map')
    with patch.object(p, 'world_cursor', return_value=None):
        assert house.step(screen, mem, None) == []
    with patch.object(p, 'world_cursor', return_value=(80, 100)):
        actions = house.step(screen, mem, None)
    assert actions[0]['buttons'] == ['right']
    assert mem['house']['phase'] == 'house_view'


@pytest.mark.parametrize('kind', ['battle', 'battle_menu', 'monster_menu', 'month_menu', 'defense_started'])
def test_battle_and_month_inputs_keep_their_original_handler(kind):
    mem = memory('travel')
    assert house.step(Screen([], None, '', kind=kind), mem, None) is None
    assert mem['house']['phase'] == 'travel'


def test_interruption_during_general_selection_cannot_buy_or_send_another_general():
    mem = memory('castle_pick')
    house.step(Screen([], None, '', kind='defense_started'), mem, None)
    assert feed(mem, gifts()) == []
    assert mem['house']['phase'] == 'close'


def test_bound_aborts_instead_of_spinning_and_chapter_drops_route():
    mem = memory('house_view', age=house.STEP_LIMIT)
    house.step(Screen([], None, '', kind='unknown'), mem, None)
    assert mem['house']['phase'] == 'close'
    mem['chapter'] = 2
    assert house.step(Screen([], None, '', kind='map'), mem, None) is None
    assert 'house' not in mem


def test_new_game_and_unmeasured_chapter_cannot_start_house_route():
    mem = {'chapter': 3, 'tick': 999}
    assert house.step(Screen([], None, '', kind='map'), mem, None) is None
    mem = {'chapter': 1, 'tick': 1}
    assert house.step(Screen([], None, '', kind='map'), mem, None) is None


def test_real_bot_routes_house_shop_before_ordinary_merchant():
    mem = memory('travel')
    actions, updated = decide(gifts(), {'policy': mem})
    assert actions == []
    assert updated['policy']['house']['phase'] == 'buy'


def test_roster_border_is_ignored_but_unknown_name_is_not():
    c = Canvas()
    c.text(168, 39, 'ゼウス')
    c.text(24, 183, 'Aボタンでステータスひょうじ')
    c.hand(146, 33)
    c.tile(240, 39, 0x0123456789ABCDEF)
    assert house.roster(parse(c.frame())) == ['ゼウス']
    c.tile(192, 39, 0x0123456789ABCDEF)
    assert house.roster(parse(c.frame())) is None


def test_actual_gift_layout_accepts_joined_scarf_price_and_border():
    screen = parse(gifts(selected=2))
    assert p.menu_to(screen, 'スカーフ', exact=False) == 'here'
    assert house.gift_prices(screen) == dict(house.GIFTS)


def test_insufficient_funds_uses_unaffordable_option_in_forced_house_list():
    mem = memory('travel')
    feed(mem, gifts(gold=30))
    assert mem['house']['phase'] == 'decline'
    assert feed(mem, gifts(gold=30, selected=2)) == [p.pad('a')]
    assert mem['house']['phase'] == 'decline_done'
    assert 'purchase' not in mem['house']


def test_castle_path_selects_the_named_nonhero_without_substitution():
    mem = memory('castle_pick', source='ほんじょう')
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    c.text(144, 39, 'どうし')
    c.text(144, 55, 'ゼウス')
    c.hand(122, 49)
    assert feed(mem, c.frame()) == [p.pad('a')]
    assert mem['house']['phase'] == 'castle_kit'
    screen = parse(status('ゼウス', main=False))
    screen.kind = 'card_select'
    assert house.step(screen, mem, None) == [p.pad('b')]
    assert mem['house']['phase'] == 'castle_confirm'


def test_wrong_general_cannot_leave_castle_even_if_their_egg_is_broken():
    mem = memory('castle_kit')
    screen = parse(status('どうし', main=False))
    screen.kind = 'card_select'
    assert house.step(screen, mem, None) == []
    assert mem['house']['phase'] == 'close'


def test_return_picker_accepts_only_an_own_flag_under_its_ring():
    mem = memory('return_view')
    screen = Screen([], None, '', kind='world_map')
    home = p.chart.home_castle(1)
    x, y = p.chart.castles(1)[home]
    ox, oy = p.WORLD_MAP_OFFSET[1]
    with patch.object(p, 'world_cursor', return_value=(x / 8 + ox, y / 8 + oy)), \
            patch.object(p, 'world_flags', return_value={home: 'own'}):
        assert house.step(screen, mem, None) == [p.pad('a')]
        assert mem['house']['phase'] == 'return_confirm'
        assert house.step(screen, mem, None) == [p.pad('a')]
        assert mem['house']['phase'] == 'return_done'
    assert mem['_records'][-1]['decision'] == 'house_return_requested'


def test_return_does_not_confirm_enemy_castle():
    mem = memory('return_view')
    screen = Screen([], None, '', kind='world_map')
    with patch.object(p, 'world_cursor', return_value=(127, 150)), \
            patch.object(p, 'world_flags', return_value={'ほんじょう': 'enemy'}):
        assert house.step(screen, mem, None) == [p.pad('right')]


def test_first_postbattle_map_cannot_start_a_chart_sortie_during_house_trip():
    mem = memory('travel')
    mem['battle'] = {'away': 1}
    assert house.step(Screen([], None, '', kind='map'), mem, None) == []


def test_emergency_recall_cancels_repair_instead_of_resuming_old_route():
    mem = memory('travel')
    mem['recall'] = {'stage': 'hero_focus', 'hero': True}
    assert house.step(Screen([], None, '', kind='map'), mem, None) is None
    assert 'house' not in mem and mem['recall']['hero']


def test_nonhero_uses_selected_roster_jump_then_requires_named_unit_status():
    mem = memory('find_field')
    assert house.step(Screen([], None, '', kind='map'), mem, None) == [p.pad('x')]
    mem['house'].update(phase='field_pick', scrolls=0)
    assert feed(mem, roster(selected=1)) == [p.pad('select')]
    assert mem['house']['phase'] == 'field_open'
    mem['house']['phase'] = 'field_read'
    assert feed(mem, status('ヴィーナス', main=False)) == []
    assert mem['house']['phase'] == 'close'


def test_castle_view_tolerates_measured_ring_jitter_before_roof_and_name_checks():
    mem = memory('castle_view', source='ほんじょう')
    x, y = house._castle_view(mem, 'ほんじょう')
    with patch.object(p, 'world_cursor', return_value=(x, y + 0.9)), patch.object(p, 'world_flags', return_value={}):
        assert house.step(Screen([], None, '', kind='world_map'), mem, None) == [p.pad('a')]
    assert mem['house']['phase'] == 'castle_open'


def test_old_house_state_cannot_consume_a_new_game_name_screen():
    mem = memory('travel')
    assert house.step(Screen([], None, '', kind='name_entry'), mem, None) is None
    assert 'house' not in mem


@pytest.mark.parametrize('hp', [0, 18, 84])
def test_injured_broken_egg_general_returns_instead_of_unarmed_repair_trip(hp):
    mem = memory('field_read', general='どうし')
    assert feed(mem, status('どうし', main=False, castle=False, hp=hp)) == [p.pad('b')]
    assert mem['house']['returning'] is True
    assert mem['house']['phase'] == 'unit_move'
    assert any(r['decision'] == 'house_recall_needed' for r in mem['_records'])


def test_injured_castle_general_is_not_dispatched_to_repair():
    mem = memory('castle_kit', general='どうし')
    screen = parse(status('どうし', main=False, hp=18))
    screen.kind = 'card_select'
    assert house.step(screen, mem, None) == []
    assert mem['house']['phase'] == 'close'


def test_unknown_hp_does_not_authorize_a_repair_trip():
    mem = memory('field_read', general='どうし')
    screen = parse(status('どうし', main=False, castle=False))
    screen.lines = [r for r in screen.lines if r.y != 47]
    assert house.step(screen, mem, None) == [p.pad('b')]
    assert mem['house']['returning'] is True
