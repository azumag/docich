"""Use the measured chapter-two event-card kit only from current stock."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse, Screen, Battle

F = Path(__file__).parent / 'fixtures' / 'hanjuku'
KIT = ['イッテツーン', 'バルムンク', 'バルムンク']


def order():
    return next(o for o in chart.orders(2) if o['step'] == '2-B1')


def memory():
    return {'chapter': 2, 'active': '2-B1', 'tick': 5000,
            'sortie_general': {'2-B1': p.NAME}, 'picked': [],
            'garrison': {'スペランザ': [p.NAME]}}


def inventory():
    screen = parse(read_png(F / 'g700-balmung-stock-panel.png'))
    return p._measured_card_select(screen)


def test_native_stock_freezes_the_tested_kit_without_selection_or_spending():
    mem = memory()
    assert p._chapter_two_balmung_inventory(mem, order(), inventory()) == []
    assert p._deploy_cards(order(), mem) == KIT
    assert mem['picked'] == [] and not mem.get('kit_spent')
    record = mem['_records'][-1]
    assert record['observed_metric']['id_sum'] == 32 < p.ENEMY_EGG_CARD_ID_SUM
    assert record['observed_metric']['stocks']['バルムンク'] == 2
    assert record['strategy_variant'] == 'rare_balmung_boss'
    assert 'キャトルミュー' not in record['deviation_reason']


@pytest.mark.parametrize('remaining', [0])
def test_full_selected_panel_never_changes_kit(remaining):
    mem = memory(); inv = inventory(); inv['remaining'] = remaining
    assert p._chapter_two_balmung_inventory(mem, order(), inv) is None
    assert not mem.get('rare_card_kit')


@pytest.mark.parametrize('card,stock', [('バルムンク', 0), ('バルムンク', 1), ('イッテツーン', 0)])
def test_missing_current_stock_cannot_borrow_a_positive_old_cache(card, stock):
    mem = memory(); mem['card_stock'] = {'バルムンク': 99, 'イッテツーン': 99}
    inv = inventory()
    for row in inv['rows']:
        if row['card'] == card: row['stock'] = stock
    assert p._chapter_two_balmung_inventory(mem, order(), inv) is None
    assert not mem.get('rare_card_kit')


@pytest.mark.parametrize('state', ['picked', 'spent', 'dropped', 'existing', 'launched', 'unconfirmed'])
def test_existing_or_committed_attempts_are_not_rekitted(state):
    mem = memory()
    if state == 'picked': mem['picked'] = ['イッテツーン']
    elif state == 'spent': mem['kit_spent'] = {'2-B1': ['バルムンク']}
    elif state == 'dropped': mem['card_drop'] = {'2-B1': ['イッテツーン']}
    elif state == 'existing': mem['rare_card_kit'] = {'2-B1': ['キャトルミュー']}
    else: mem['orders'] = {'2-B1': 'launched' if state == 'launched' else 'launched_unconfirmed'}
    before = deepcopy(mem)
    assert p._chapter_two_balmung_inventory(mem, order(), inventory()) is None
    assert mem == before


@pytest.mark.parametrize('change', ['chapter', 'step', 'target'])
def test_other_chapters_orders_and_targets_keep_their_chart_kit(change):
    mem = memory(); o = dict(order())
    if change == 'chapter': mem['chapter'] = 1
    elif change == 'step': o['step'] = '2-S1'
    else: o['target'] = 'ウラノポリス'
    assert p._chapter_two_balmung_inventory(mem, o, inventory()) is None
    assert not mem.get('rare_card_kit')


def test_native_three_card_receipt_confirms_the_installed_exact_kit():
    mem = memory(); p._chapter_two_balmung_inventory(mem, order(), inventory())
    screen = parse(read_png(F / 'g700-balmung-kit-confirm.png'))
    assert p._sortie_inventory(screen) == KIT
    assert p.deploy_step(screen, mem) == [p.pad('a')]
    assert mem['order_context']['2-B1']['observed_metric']['cards'] == sorted(KIT)


def confirmed():
    mem = memory(); p._chapter_two_balmung_inventory(mem, order(), inventory())
    p.deploy_step(parse(read_png(F / 'g700-balmung-kit-confirm.png')), mem)
    return mem


def test_boss_tactics_require_actual_carriage_and_keep_each_copy_distinct():
    mem = memory(); p._chapter_two_balmung_inventory(mem, order(), inventory())
    assert not any(t['card'] == 'バルムンク' for t in p._tactics(mem, '2-B1'))
    mem = confirmed()
    tactics = [t for t in p._tactics(mem, '2-B1') if t['card'] == 'バルムンク']
    assert len(tactics) == 2 and len({t['tactic_id'] for t in tactics}) == 2
    assert all(t['enemy'] == 'にせヒーロー' and t['boss_only'] for t in tactics)
    mem['kit_spent'] = {'2-B1': ['バルムンク']}
    remaining = [t for t in p._tactics(mem, '2-B1') if t['card'] == 'バルムンク']
    assert [t['tactic_id'] for t in remaining] == [tactics[1]['tactic_id']]
    mem['kit_spent']['2-B1'].append('バルムンク')
    assert not any(t['card'] == 'バルムンク' for t in p._tactics(mem, '2-B1'))


def test_road_enemy_does_not_receive_the_boss_opening_tactic():
    mem = confirmed()
    mem['attack'] = {'general': p.NAME, 'castle': None, 'side': 'attack', 'step': '2-B1'}
    screen = Screen([], None, '', kind='battle', battle=Battle('クミン', 27, p.NAME, 90))
    p.battle_step(screen, mem); p.battle_step(screen, mem)
    assert not any(r['decision'] == 'battle_card' and r.get('card') == 'バルムンク'
                   for r in mem['_records'])


def test_native_before_after_and_next_chapter_cue_prove_isolated_boss_defeat():
    before = parse(read_png(F / 'g700-fake-hero-before-balmung.png'))
    after = parse(read_png(F / 'g700-fake-hero-after-balmung.png'))
    assert before.battle.enemy == after.battle.enemy == 'にせヒーロー'
    assert before.battle.enemy_hp == 90 and after.battle.enemy_hp == 0
    assert before.battle.ally_hp == 90 and after.battle.ally_hp == 88
    cue = parse(read_png(F / 'g700-fake-hero-defeated-cue.png'))
    assert 'にせヒーローをたおし' in cue.text


def test_zero_boss_panel_never_spends_the_spare_event_card():
    mem = confirmed()
    mem['attack'] = {'general': p.NAME, 'castle': 'ボス', 'side': 'attack',
                     'step': '2-B1', 'entry_evidence': 'measured_boss_entry'}
    live = parse(read_png(F / 'g700-fake-hero-before-balmung.png'))
    p.battle_step(live, mem); p.battle_step(live, mem)
    zero = parse(read_png(F / 'g700-fake-hero-after-balmung.png'))
    start = len(mem['_records'])
    assert p.battle_step(zero, mem) == []
    assert not any(r['decision'] == 'battle_card' for r in mem['_records'][start:])


@pytest.mark.parametrize('remaining', [1, 2])
def test_partial_original_kit_cancels_without_rewriting_reserved_cards(remaining):
    mem = memory(); mem['picked'] = ['イッテツーン']; inv = inventory(); inv['remaining'] = remaining
    assert p._chapter_two_balmung_inventory(mem, order(), inv) == [p.pad('b'), p.pad('b')]
    assert mem['active'] == '2-B1' and mem['picked'] == []
    assert mem['boss_stock_restart'] == {'step': '2-B1', 'readings': 0}
    assert mem['orders']['2-B1'] == 'pending'
    assert not mem.get('rare_card_kit') and not mem.get('kit_spent')
    assert mem['_records'][-1]['decision'] == 'boss_stock_kit_restart'


def test_partial_reservation_is_not_assumed_to_match_cached_selected_name():
    mem = memory(); mem['picked'] = ['ノリウツール']; inv = inventory(); inv['remaining'] = 2
    assert p._chapter_two_balmung_inventory(mem, order(), inv) == [p.pad('b'), p.pad('b')]
    assert not mem.get('rare_card_kit')


def test_restart_backs_out_before_reselecting_and_is_bounded():
    mem = memory(); mem['boss_stock_restart'] = {'step': '2-B1', 'readings': 0}
    screen = parse(read_png(F / 'g700-balmung-kit-confirm.png'))
    for _ in range(11):
        assert p.deploy_step(screen, mem) == [p.pad('b')]
        assert not mem.get('order_context')
    assert p.deploy_step(screen, mem) == [p.pad('b'), p.pad('b')]
    assert mem['active'] is None and not mem.get('boss_stock_restart')


def test_restart_flag_is_cleared_on_full_backout_and_other_order(monkeypatch):
    mem = memory(); mem['boss_stock_restart'] = {'step': 'other', 'readings': 1}
    p.deploy_step(parse(read_png(F / 'g700-balmung-stock-panel.png')), mem)
    assert not mem.get('boss_stock_restart')
    assert p._deploy_cards(order(), mem) == KIT
    mem = memory(); mem['boss_stock_restart'] = {'step': '2-B1', 'readings': 2}
    monkeypatch.setattr(p, '_check_source_castle', lambda *args: [p.pad('b')])
    p.deploy_step(Screen([], None, '', kind='castle_menu'), mem)
    assert not mem.get('boss_stock_restart') and mem['active'] == '2-B1'


def test_chapter_two_boss_keeps_comparable_companions_and_rejects_weak_one():
    mem = memory()
    assert p._order_hero_alternatives(order(), mem, ['どうし', 'シェーブル', 'ゼウス']) == ['ゼウス']
    assert p._order_hero_alternatives(order(), mem, ['どうし', 'シェーブル']) == []
    assert p._hero_alternatives(mem, ['どうし', 'シェーブル']) == ['シェーブル']
    road = {**order(), 'step': '2-S1', 'target': 'スペランザ'}
    assert p._order_hero_alternatives(road, mem, ['どうし', 'シェーブル']) == ['シェーブル']


def test_broken_hero_is_not_licensed_by_a_weak_boss_companion():
    mem = memory(); mem['garrison'] = {'スペランザ': ['どうし', 'シェーブル']}
    mem['house_eggs'] = {'どうし': {'broken': True}}
    mem['captured'] = [n for n in chart.castles(2) if n not in ('アルマムーン', 'ボス')]
    assert not p._hero_source_alternative(order(), mem)
    assert not p._ready(order(), mem)
