"""強い切り札の携行・開幕使用と、gcgx shogun.html 将軍表ハードコード値の照合。

owner 2026-10-03: 「強い切り札を偶然手に入れている時などは、強い将軍とたたかう
ときに積極的に利用するようにして下さい」「ここを参考に、この評をハードコードして
しまって、値を照らし合わせよ。まずは戦闘の値とHPでわかる。補助的に卵補正、たまごの
しゅるい、卵仕様の思考パターンなどが参考になる」「携行＋開幕使用」。
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart, hanjuku_policy as policy, hanjuku_reference as reference
from docich.hanjuku_egg_reference import GENERAL_EGGS, general_max_hp
from docich.hanjuku_screen import Battle, Screen
from test_hanjuku_sortie_evidence import foot_order_memory, measured_card_select, memory


def test_shogun_table_cross_checks_char_csv_and_egg_reference():
    """ハードコードした shogun.html の値を既存の正典表と照らし合わせる。"""
    assert len(reference.GENERAL_STATS) == 128        # SFC ID 0..127
    assert set(reference.GENERAL_STATS) == set(GENERAL_EGGS)
    for name, (hp, combat, bonus, egg, thought) in reference.GENERAL_STATS.items():
        assert general_max_hp(name) == hp, name       # char.csv 由来の最大HP
        has_egg, known_thought = GENERAL_EGGS[name]
        assert known_thought == thought, name         # エッグ思考パターン
        assert has_egg == (egg != '－'), name         # たまごのしゅるいの有無
        assert 1 <= combat <= 15, name
    # 補正の一部は季節付き ('春+1'/'夏-1') なので文字列で保持している。
    assert reference.GENERAL_STATS['ココット'][2] == '3'
    assert reference._bonus_value('春-1') == -1 and reference._bonus_value('夏+1') == 1


@pytest.mark.parametrize('enemy, ally, verdict, rule', [
    ('バタール', 'ココット', True, 'combat'),        # 戦闘 13 vs 8 (差5)
    ('しゅじんこう', 'オレガノ', True, 'hp'),          # 戦闘は負け (14 vs 15) だが HP 90 vs 65
    ('しゅじんこう', 'マンゴスチン', True, 'dominant'),  # 戦闘・HP とも僅かに勝る
    ('キャラウェイ', 'しゅじんこう', False, 'weaker'),   # 戦闘 4 vs 14, HP 40 vs 90
    ('コリアンダー', 'シナモン', True, 'bonus'),        # 主判定が混在 → 補正 +1 vs -2
    ('しゅじんこう', 'エッジ', True, 'egg'),            # 主判定が混在 → 卵あり vs なし
    ('タピオカ', 'トレビス', False, 'thought'),          # 主判定が混在 → 思考 2 < 3
    ('だいまおう', 'どうし', None, 'unknown'),          # 表 (ID0..127) に無い後期ボス
    (None, 'どうし', None, 'unknown'),                 # 敵名が未読のときは判定しない
])
def test_strong_general_primary_is_combat_and_hp_with_owner_auxiliary_fallback(enemy, ally, verdict, rule):
    got, evidence = reference.strong_general(enemy, ally)
    assert got is verdict
    assert evidence['rule'] == rule
    assert set(evidence) == {'rule', 'enemy', 'ally'}
    if rule != 'unknown':
        assert set(evidence['enemy']) == {'name', 'hp', 'combat', 'bonus', 'egg', 'thought'}


def test_policy_counts_every_boss_as_a_strong_general():
    # 後期ボスは shogun.html に載らないが、ボスだから強い将軍として扱う。
    verdict, evidence = policy._strong_enemy({}, {'enemy': 'だいまおう', 'ally': chart.HERO})
    assert verdict is True and evidence['rule'] == 'boss'
    verdict, evidence = policy._strong_enemy({}, {'enemy': 'キャラウェイ', 'ally': 'ヴィーナス'})
    assert verdict is False and evidence['rule'] == 'weaker'
    # どうし(主人公)は表では しゅじんこう として照合する。
    verdict, _ = policy._strong_enemy({}, {'enemy': 'キャラウェイ', 'ally': chart.HERO})
    assert verdict is False


def test_strong_cards_are_selectable_and_not_sacrificial():
    assert tuple(reference.STRONG_CARDS) == ('クースカン', 'ミックミー', 'マグネガキン', 'ノリウツール')
    assert set(reference.STRONG_CARDS) <= set(policy.CARD_NAMES)
    assert set(reference.STRONG_CARDS) <= set(policy.SURVIVAL_CARDS)
    assert 'ファバード' not in reference.STRONG_CARDS   # 自軍も半減させる犠牲札


def _sortie(step, picked=(), remaining=3, stock=('クースカン',)):
    mem = {'rare_scan_month': 'chapter-1:unknown', 'chapter': 1, 'active': step,
           'variant': 'chart', 'orders': {step: 'pending'}, 'picked': list(picked),
           '_records': []}
    inventory = {'rows': [{'card': name, 'stock': 1, 'y': 55 + 16*i}
                          for i, name in enumerate(stock)],
                 'selected_y': 55, 'remaining': remaining}
    policy._observe_card_stock(mem, inventory)
    order = policy._order(mem)
    return order, mem, inventory


def test_held_strong_card_joins_a_sortie_with_a_free_carry_slot():
    order, mem, inventory = _sortie('1-A2')          # 予定札: フットバース1枚 (ID 3)
    wanted = policy._deploy_cards(order, mem)
    assert wanted == ['フットバース']
    assert policy._strong_card_inventory(mem, order, inventory, wanted) is True
    assert mem['strong_card_kit']['1-A2'] == 'クースカン'
    carried = policy._deploy_cards(order, mem)
    assert carried == ['フットバース', 'クースカン']
    rec = next(r for r in mem['_records'] if r['decision'] == 'strong_card_kit')
    assert rec['card'] == 'クースカン' and rec['observed_metric']['id_sum'] == 16   # 3 + 13
    # 一度決めた携行は同じ出撃では変更しない。
    assert policy._strong_card_inventory(mem, order, inventory, wanted) is False


def test_no_strong_card_when_the_planned_kit_uses_every_carry_slot():
    order, mem, inventory = _sortie('1-C2')          # 予定札: ダイチスイムx2 + ブラッキー
    wanted = policy._deploy_cards(order, mem)
    assert len(wanted) == policy.CARRY_SLOTS
    assert policy._strong_card_inventory(mem, order, inventory, wanted) is False
    assert 'strong_card_kit' not in mem


def test_no_strong_card_when_the_rare_kit_leads_or_a_planned_card_is_picked():
    order, mem, inventory = _sortie('1-A2')
    mem['rare_card_kit'] = {'1-A2': ['キャトルミュー']}
    assert policy._strong_card_inventory(mem, order, inventory, ['キャトルミュー']) is False
    assert 'strong_card_kit' not in mem

    order, mem, inventory = _sortie('1-A2', picked=('フットバース',))
    assert policy._strong_card_inventory(mem, order, inventory, []) is False
    assert 'strong_card_kit' not in mem


def test_strong_card_is_kept_out_of_a_full_id_budget():
    # 予定札: クースカン13 + ノリウツール11 = 24。マグネガキン27 は 24+27=51 で
    # 48以上なので足さない (gcgx: 48以上だと敵がエッグを使う)。
    order, mem, inventory = _sortie('1-B1')
    mem['card_stock'].update({'マグネガキン': 2})
    assert policy._strong_card_inventory(mem, order, inventory,
                                         policy._deploy_cards(order, mem)) is False
    assert 'strong_card_kit' not in mem
    # 予算に収まる候補 (ミックミー17 → 41) なら足せる。
    order, mem, inventory = _sortie('1-B1')
    mem['card_stock'].update({'ミックミー': 4})
    assert policy._strong_card_inventory(mem, order, inventory,
                                         policy._deploy_cards(order, mem)) is True
    assert policy._deploy_cards(order, mem) == ['クースカン', 'ノリウツール', 'ミックミー']


@pytest.mark.parametrize('cards, strong, expected', [
    (['イッテツーン'], 'クースカン', ['イッテツーン', 'クースカン']),
    (['クースカン'], 'クースカン', ['クースカン']),                       # 携行済み
    (['イッテツーン', 'ブラッキー', 'フットバース'], 'クースカン',
     ['イッテツーン', 'ブラッキー', 'フットバース']),                     # 携行枠なし
    (['クースカン', 'ミックミー', 'イッテツーン'], 'マグネガキン',
     ['クースカン', 'ミックミー', 'イッテツーン']),                       # 13+17+1+27 >= 48
])
def test_with_strong_card_keeps_slots_and_the_47_id_budget(cards, strong, expected):
    assert policy._with_strong_card(cards, strong, {}, '1-A2') == expected


def test_with_strong_card_never_resurrects_a_spent_or_dropped_copy():
    assert policy._with_strong_card([], 'クースカン', {'kit_spent': {'1-A1': ['クースカン']}}, '1-A1') == []
    assert policy._with_strong_card([], 'クースカン', {'card_drop': {'1-A1': ['クースカン']}}, '1-A1') == []


def test_deploy_step_carries_the_strong_card_after_the_planned_one():
    """予定札を先に選び、残った携行枠で強い切り札を選ぶまでを実測フローで通す。"""
    mem = foot_order_memory()                        # 1-A2: 予定札はフットバース1枚

    def panel(selected):
        return measured_card_select(('クースカン', 'フットバース', 'イッテツーン', 'ブラッキー'),
                                    selected=selected, remaining='3')

    assert policy.deploy_step(panel(0), mem) == [policy.pad('down')]   # カーソルを予定札へ
    assert mem['strong_card_kit']['1-A2'] == 'クースカン'
    assert mem['picked'] == []
    assert policy.deploy_step(panel(1), mem) == [policy.pad('a')]
    assert mem['picked'] == ['フットバース']
    # 予定札が消えたので、追加携行した強い切り札が残り枠で選ばれる。
    assert policy.deploy_step(panel(1), mem) == [policy.pad('up')]
    assert policy.deploy_step(panel(0), mem) == [policy.pad('a')]
    assert mem['picked'] == ['フットバース', 'クースカン']
    assert policy._deploy_cards(policy._order(mem), mem) == ['フットバース', 'クースカン']


def test_sortie_without_planned_cards_still_carries_a_held_strong_card():
    mem = memory()                                    # 1-B1 ではなく 1-A1 (予定札なし)
    mem['active'] = '1-A1'
    screen = measured_card_select(('クースカン', 'フットバース', 'イッテツーン', 'ブラッキー'))
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    assert mem['strong_card_kit']['1-A1'] == 'クースカン'
    assert mem['picked'] == ['クースカン']
    assert policy._deploy_cards(policy._order(mem), mem) == ['クースカン']


def battle_memory(step, enemy, ally):
    mem = {'chapter': 1, 'month': '1-10', 'active': step, 'variant': 'chart',
           'orders': {step: 'pending'}, 'picked': [], '_records': [],
           'strong_card_kit': {step: 'クースカン'},
           'battle': {'enemy': enemy, 'ally': ally, 'enemy_hp': 70, 'ally_hp': 80,
                      'start_enemy_hp': 70, 'start_ally_hp': 80, 'step': step,
                      'cards_used': [], 'cards_selected': [], 'side': 'attack',
                      'castle': 'キカンドン', 'clashed': False, 'planned_cards': []}}
    return mem


def battle_screen(enemy, ally):
    screen = Screen(lines=[], hand=None, text='')
    screen.kind = 'battle'
    screen.battle = Battle(enemy=enemy, ally=ally, enemy_hp=70, ally_hp=80)
    return screen


def test_strong_general_opens_with_the_carried_strong_card():
    mem = battle_memory('1-A1', 'バタール', 'ココット')   # 戦闘13/HP99 vs 8/24
    assert policy.battle_step(battle_screen('バタール', 'ココット'), mem) == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    assert not mem['battle']['clashed']
    rec = mem['_records'][-1]
    assert rec['decision'] == 'battle_card' and rec['card'] == 'クースカン'
    assert rec['strong_enemy']['rule'] == 'combat'
    assert rec['strong_enemy']['enemy']['combat'] == 13 and rec['strong_enemy']['enemy']['hp'] == 99


def test_weak_general_keeps_the_strong_card_in_reserve():
    mem = battle_memory('1-A1', 'キャラウェイ', 'ヴィーナス')   # 4/40 vs 12/82
    policy.battle_step(battle_screen('キャラウェイ', 'ヴィーナス'), mem)
    assert mem['battle'].get('card_flow') is None
    assert mem['strong_card_kit']['1-A1'] == 'クースカン'      # 携行は維持する
    assert not [r for r in mem['_records'] if r['decision'] == 'battle_card']


def test_boss_enemy_opens_with_the_carried_strong_card():
    mem = battle_memory('1-A1', 'だいまおう', 'ココット')       # 表に無い後期ボス
    policy.battle_step(battle_screen('だいまおう', 'ココット'), mem)
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    assert mem['_records'][-1]['strong_enemy']['rule'] == 'boss'


def test_no_strong_opening_once_the_carried_copy_was_spent():
    mem = battle_memory('1-A1', 'バタール', 'ココット')
    mem['kit_spent'] = {'1-A1': ['クースカン']}
    policy.battle_step(battle_screen('バタール', 'ココット'), mem)
    assert mem['battle'].get('card_flow') is None


def test_no_strong_opening_without_an_actual_carry():
    mem = battle_memory('1-A1', 'バタール', 'ココット')
    mem.pop('strong_card_kit')
    policy.battle_step(battle_screen('バタール', 'ココット'), mem)
    assert mem['battle'].get('card_flow') is None
