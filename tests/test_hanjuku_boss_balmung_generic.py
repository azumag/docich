"""バルムンク(ボス250)を全章のボス注文で先頭携行し、開幕に使う (v384, owner 2026-11-04)。"""
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as p
from docich import hanjuku_reference as reference
from docich.hanjuku.state.regions import plan as _plan_region


def inventory(rows, remaining=3):
    return {'rows': [{'y': 55 + 16 * i, 'card': c, 'stock': n} for i, (c, n) in enumerate(rows)],
            'selected_y': 55, 'remaining': remaining,
            'inventory_evidence': 'measured_four_row_card_select', 'observed_rows': len(rows)}


def memory(chapter):
    return {'chapter': chapter, 'tick': 5000, 'picked': [], 'month': '2-6',
            'captured': [], 'rare_scan_month': '2-6'}


def boss_order(chapter, mem):
    order = next(o for o in chart.orders(chapter) if o['target'] == chart.boss_castle(chapter))
    mem['active'] = order['step']
    return order


def test_reference_ranks_boss_damage_and_leaves_halving_cards_out():
    damages = [reference.boss_damage_card(c) for c in reference.BOSS_DAMAGE_CARDS]
    assert reference.BOSS_DAMAGE_CARDS[0] == 'バルムンク' and damages[0] == 250
    assert damages[1:4] == [100, 100, 100]
    assert 'クースカン' not in reference.BOSS_DAMAGE_CARDS
    assert 'ノリウツール' not in reference.BOSS_DAMAGE_CARDS
    assert reference.BOSS_SACRIFICE_CARDS == ('バグストーム',)


def test_a_monster_boss_order_carries_balmung_first_within_the_id_budget():
    mem = memory(5); order = boss_order(5, mem)
    inv = inventory([('イッテツーン', 3), ('ブラッキー', 2), ('バルムンク', 2), ('マグネガキン', 2)])
    assert p._chapter_two_balmung_inventory(mem, order, inv) == []
    kit = mem['rare_card_kit'][order['step']]
    assert kit[:2] == ['バルムンク', 'バルムンク']
    assert sum(p.CARD_IDS[c] for c in kit) < p.ENEMY_EGG_CARD_ID_SUM
    assert p._deploy_cards(order, mem) == kit
    record = mem['_records'][-1]
    assert record['strategy_variant'] == 'rare_balmung_boss'
    assert record['observed_metric']['boss_damage'] == 250


def test_one_copy_leaves_room_for_a_planned_card():
    mem = memory(1); order = boss_order(1, mem)
    inv = inventory([('イッテツーン', 3), ('クースカン', 1), ('バルムンク', 1), ('ミックミー', 1)])
    assert p._chapter_two_balmung_inventory(mem, order, inv) == []
    kit = mem['rare_card_kit'][order['step']]
    assert kit.count('バルムンク') == 1 and kit[0] == 'バルムンク'
    assert sum(p.CARD_IDS[c] for c in kit) < p.ENEMY_EGG_CARD_ID_SUM


def test_no_balmung_in_stock_or_a_started_attempt_changes_nothing():
    for rows, change in [([('イッテツーン', 3), ('ブラッキー', 2)], None),
                         ([('バルムンク', 0), ('ブラッキー', 2)], None),
                         ([('バルムンク', 2)], 'picked'), ([('バルムンク', 2)], 'spent'),
                         ([('バルムンク', 2)], 'launched')]:
        mem = memory(5); order = boss_order(5, mem)
        step = order['step']
        if change == 'picked': mem['picked'] = ['ファバード']
        elif change == 'spent': mem['kit_spent'] = {step: ['ファバード']}
        elif change == 'launched': mem['orders'] = {step: 'launched'}
        before = deepcopy(mem)
        assert p._chapter_two_balmung_inventory(mem, order, inventory(rows)) is None
        assert mem == before


def test_ordinary_sorties_never_receive_the_boss_balmung_kit():
    mem = memory(5)
    order = next(o for o in chart.orders(5) if o['target'] != chart.boss_castle(5))
    mem['active'] = order['step']
    assert p._chapter_two_balmung_inventory(mem, order, inventory([('バルムンク', 2)])) is None
    assert not mem.get('rare_card_kit')


def test_a_balmung_measured_this_month_counts_when_the_window_hides_it():
    mem = memory(5); order = boss_order(5, mem)
    p._note_balmung_stock(mem, '2-6', inventory([('バルムンク', 2), ('ブラッキー', 1), ('ミックミー', 1), ('ファバード', 1)]))
    assert mem['balmung_held'] == {'month': '2-6', 'stock': 2}
    hidden = inventory([('イッテツーン', 3), ('ダイチスイム', 2), ('ブラッキー', 2), ('フットバース', 2)])
    assert p._boss_balmung_kit(mem, order, hidden) == []
    assert mem['rare_card_kit'][order['step']][0] == 'バルムンク'
    # 別の月の記録は借りない。
    mem = memory(5); order = boss_order(5, mem)
    mem['balmung_held'] = {'month': '1-12', 'stock': 2}
    assert p._boss_balmung_kit(mem, order, hidden) is None
    # 一覧が不在を証明したら記録は消える。
    mem['balmung_held'] = {'month': '2-6', 'stock': 2}
    p._note_balmung_stock(mem, '2-6', inventory([('イッテツーン', 3), ('ブラッキー', 2)]))
    assert 'balmung_held' not in mem


def test_balmung_tactics_open_only_in_a_measured_boss_entry_per_copy():
    mem = memory(5); order = boss_order(5, mem); step = order['step']
    inv = inventory([('バルムンク', 2), ('ブラッキー', 2)])
    p._chapter_two_balmung_inventory(mem, order, inv)
    _plan_region(mem)['order_context'] = {step: {'observed_metric': {'cards': list(mem['rare_card_kit'][step])}}}
    tactics = [t for t in p._tactics(mem, step) if t['card'] == 'バルムンク']
    assert len(tactics) == 2 and len({t['tactic_id'] for t in tactics}) == 2
    assert all(t['boss_only'] and t['enemy'] == 'だいまおう' for t in tactics)
    mem['kit_spent'] = {step: ['バルムンク']}
    assert len([t for t in p._tactics(mem, step) if t['card'] == 'バルムンク']) == 1


def _wave_memory(general):
    mem = memory(5)
    order = {'step': '5-W9', 'general': general, 'source': chart.castles(5) and next(iter(chart.castles(5))),
             'cards': ('イッテツーン',), 'target': chart.boss_castle(5), 'after': None, 'note': 'wave'}
    mem['active'] = order['step']
    _plan_region(mem)['sortie_general'] = {order['step']: general}
    return order, mem


def test_a_monster_boss_sortie_carries_boss_damage_cards_not_halving_cards():
    order, mem = _wave_memory('ココット')
    inv = inventory([('クースカン', 3), ('ハリケーン', 1), ('ノリウツール', 2)])
    p._observe_card_stock(mem, inv)
    assert p._strong_card_names(mem, order)[:2] == ('バルムンク', 'ハリケーン')
    assert p._strong_card_inventory(mem, order, inv, ['イッテツーン']) is True
    assert mem['strong_card_kit'][order['step']] == 'ハリケーン'


def test_halving_cards_are_not_added_to_a_monster_boss_sortie():
    order, mem = _wave_memory('ココット')
    inv = inventory([('クースカン', 3), ('ミックミー', 2), ('ノリウツール', 2)])
    p._observe_card_stock(mem, inv)
    assert p._strong_card_inventory(mem, order, inv, ['イッテツーン']) is False
    assert 'strong_card_kit' not in mem


def test_only_an_eggless_weak_general_spends_bug_storm_on_the_boss():
    stats = reference.GENERAL_STATS
    ok = lambda n: reference.sacrifice_general(n)
    assert ok('キャラウェイ') and ok('バジル')                       # HP40/戦闘4・3、卵なし
    assert not ok('カシュー')          # 戦闘1・HP39 だが卵(イビル)を持つ
    assert not ok('タピオカ')          # 卵なし・HP50 だが戦闘7
    assert not ok('ヘパイストス') and not ok('しゅじんこう')
    assert not ok('存在しない将軍') and not ok(None)               # 表に無ければ不可
    assert all(stats[n][3] == '－' and stats[n][0] <= 50 and stats[n][1] <= 5
               for n in stats if ok(n))
    weak = 'キャラウェイ'
    strong = next(n for n, (hp, *_rest) in stats.items() if hp >= 70)
    for general, expected in ((weak, True), ('カシュー', False), ('タピオカ', False), (strong, False)):
        order, mem = _wave_memory(general)
        assert ('バグストーム' in p._strong_card_names(mem, order)) is expected, general


def test_boss_generals_and_ordinary_sorties_keep_the_general_strong_cards():
    mem = memory(1); order = boss_order(1, mem)
    assert p._strong_card_names(mem, order) == reference.STRONG_CARDS
    mem = memory(5)
    order = next(o for o in chart.orders(5) if o['target'] != chart.boss_castle(5))
    assert p._strong_card_names(mem, order) == reference.STRONG_CARDS


def test_g1075_season_site_panel_with_balmung_now_takes_the_balmung_kit():
    from test_hanjuku_sortie_evidence import actor_panel, measured_card_select
    normal = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
    mem = {'rare_scan_month': 'chapter-4:unknown', 'chapter': 4, 'active': '4-B1',
           'captured': normal, 'lost': [], 'variant': 'chart', 'orders': {'4-B1': 'pending'},
           'picked': [], 'sortie_general': {'4-B1': 'アルテミス'}}
    screen = actor_panel(measured_card_select(
        ('クースカン', 'バルムンク', 'ミックミー', 'バグストーム'), selected=3,
        stocks=('5', '9', '4', '50'), remaining='3'), 'アルテミス')
    p.deploy_step(screen, mem)
    kit = mem['rare_card_kit']['4-B1']
    assert kit[:2] == ['バルムンク', 'バルムンク']
    assert sum(p.CARD_IDS[c] for c in kit) < p.ENEMY_EGG_CARD_ID_SUM
    # 四季王は敵名が季節で変わるため、戦術は敵名を問わずボス突入の実測でだけ開く。
    _plan_region(mem)['order_context'] = {'4-B1': {'observed_metric': {'cards': list(kit)}}}
    tactics = [t for t in p._tactics(mem, '4-B1') if t['card'] == 'バルムンク']
    assert len(tactics) == 2 and all(t['enemy'] is None and t['boss_only'] for t in tactics)
