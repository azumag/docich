"""出撃時の卵落札携行と、守備将軍の実測記録 (#1553)。

出撃前の画面に対戦相手の表示は存在しない (きりふだセレクト/しゅつげきしますか
は自軍の将軍・在庫・卵だけを表示する)。だから判明の根拠は「その城での前回戦闘
パネル」(flows.battle の ``castle_defenders``) だけ: 攻撃敗北後の再出撃などで、
記録した守備将軍の最大HPから ``卵落 > (敵味方最大HP合計) mod 16`` を出撃時に
算出し、卵落札を1枠に加える。未判明の出撃は現状どおり (fail-closed)、ID合計は
47以下を守り、戦闘側 (``battle_egg_plan``) は実測した敵で同じ算出を行う。
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy, hanjuku_reference as reference
from docich.hanjuku_egg_reference import general_max_hp
from test_hanjuku_egg_plan import mem as egg_mem, opening


def sortie(step, picked=(), remaining=3, stock=('グルミー',), count=1):
    mem = {'rare_scan_month': 'chapter-1:unknown', 'chapter': 1, 'active': step,
           'variant': 'chart', 'orders': {step: 'pending'}, 'picked': list(picked),
           '_records': []}
    inventory = {'rows': [{'card': name, 'stock': count, 'y': 55 + 16 * i}
                          for i, name in enumerate(stock)],
                 'selected_y': 55, 'remaining': remaining}
    policy._observe_card_stock(mem, inventory)
    order = policy._order(mem)
    return order, mem, inventory


def defender(mem, castle='ゴーメン', general='ガルバンゾー', chapter=1):
    mem['castle_defenders'] = {castle: {'general': general, 'chapter': chapter,
                                        'evidence': 'battle_panel', 'step': '1-A2'}}
    return mem


def records(mem, kind):
    return [r for r in mem['_records'] if r['decision'] == kind]


# ガルバンゾー30 + しゅじんこう90 = 120 → 120 mod 16 = 8 → 卵落9以上で落とせる。
PLAN = {'max_hp_sum': 120, 'threshold': 9}


def test_the_sortie_plan_shares_the_battle_side_arithmetic():
    assert (general_max_hp('ガルバンゾー'), general_max_hp('しゅじんこう')) == (30, 90)
    assert reference.egg_drop_plan(30, 90) == PLAN
    # 卵落9の閾値: 8のイッテツーンは届かず、15のグルミーは届く (強い順)。
    assert reference.egg_drop_value('イッテツーン') == 8
    assert reference.egg_droppers(['イッテツーン', 'グルミー', 'ころぼぐんだん'], 120) == [
        'グルミー', 'ころぼぐんだん']
    assert reference.egg_drop_threshold(120) == PLAN['threshold']


def test_a_measured_defender_reserves_one_dropper_slot_within_the_budget():
    order, mem, inventory = sortie('1-A2')          # 予定札: フットバース1枚 (ID 3)
    defender(mem)
    wanted = policy._deploy_cards(order, mem)
    assert wanted == ['フットバース']
    assert policy._dropper_card_inventory(mem, order, inventory, wanted) is True
    kit = mem['dropper_card_kit']['1-A2']
    assert kit == {'card': 'グルミー', 'defender': 'ガルバンゾー',
                   'defender_evidence': 'castle_defenders', **PLAN}
    carried = policy._deploy_cards(order, mem)
    assert carried == ['フットバース', 'グルミー']
    assert sum(reference.ALL_CARD_IDS[c] for c in carried) == 22    # 3 + 19 < 48
    rec = records(mem, 'dropper_card_kit')[0]
    assert rec['card'] == 'グルミー' and rec['observed_metric']['id_sum'] == 22
    assert rec['observed_metric']['threshold'] == PLAN['threshold']
    # 決定は一度だけ: 同じ出撃では選び直さない。
    assert policy._dropper_card_inventory(mem, order, inventory, wanted) is False


def test_dropper_candidates_rank_by_egg_drop_and_need_stock():
    order, mem, inventory = sortie('1-A2', stock=('ころぼぐんだん', 'グルミー'))
    defender(mem)
    assert policy._dropper_card_inventory(mem, order, inventory, ['フットバース']) is True
    assert mem['dropper_card_kit']['1-A2']['card'] == 'グルミー'      # 15 > 12
    order, mem, inventory = sortie('1-A2', stock=('イッテツーン',))
    defender(mem)
    assert policy._dropper_card_inventory(mem, order, inventory, ['フットバース']) is False
    assert 'dropper_card_kit' not in mem                # 卵落8 < 閾値9 は候補外


@pytest.mark.parametrize('prepare, reason', [
    (lambda order, mem, inventory: None, '未判明の出撃は現状どおり (fail-closed)'),
    (lambda order, mem, inventory: mem.update(chapter=2), '章が違う記録は使わない'),
    (lambda order, mem, inventory: defender(mem, chapter=2), '記録の章が今の章と違う'),
    (lambda order, mem, inventory: defender(mem, general='クイーン'), 'ボス将軍は卵落算術の対象外'),
    (lambda order, mem, inventory: mem.update(
        rare_card_kit={'1-A2': ['キャトルミュー']}), 'レア札キットが携行札の主導権を持つ'),
    (lambda order, mem, inventory: mem.update(picked=['フットバース']), '選択開始後は決め直さない'),
])
def test_fail_closed_conditions_keep_the_carry_unchanged(prepare, reason):
    order, mem, inventory = sortie('1-A2')
    if '章が違う記録' not in reason and 'fail-closed' not in reason:
        defender(mem)
    prepare(order, mem, inventory)
    wanted = policy._deploy_cards(order, mem)
    before = list(wanted)
    assert policy._dropper_card_inventory(mem, order, inventory, wanted) is False, reason
    assert policy._deploy_cards(order, mem) == before
    assert 'dropper_card_kit' not in mem


def test_no_dropper_when_every_carry_slot_is_planned_or_the_budget_blocks_it():
    order, mem, inventory = sortie('1-C2')          # 予定札: ダイチスイムx2 + ブラッキー
    defender(mem, castle='スペンソニア')
    assert len(policy._deploy_cards(order, mem)) == policy.CARRY_SLOTS
    assert policy._dropper_card_inventory(mem, order, inventory,
                                          policy._deploy_cards(order, mem)) is False
    # 1-B1 (ボス城) は守備将軍が読めていても対象外。
    order, mem, inventory = sortie('1-B1')
    defender(mem, castle='けっかい')
    assert policy._sortie_defender(mem, order) is None


def test_the_carried_dropper_survives_spent_and_dropped_copies():
    order, mem, inventory = sortie('1-A2')
    defender(mem)
    policy._dropper_card_inventory(mem, order, inventory, ['フットバース'])
    mem['kit_spent'] = {'1-A2': ['グルミー']}
    assert policy._deploy_cards(order, mem) == ['フットバース']
    mem.pop('kit_spent')
    mem['card_drop'] = {'1-A2': ['グルミー']}
    assert policy._deploy_cards(order, mem) == ['フットバース']


def test_a_defense_loss_records_the_capturing_enemy_as_the_defender():
    cur = {'side': 'defense', 'castle': 'アルマムーン', 'enemy': 'ガスパチョ',
           'enemy_hp': 30, 'ally_hp': 0, 'ally': 'どうし', 'step': None,
           'cards_used': [], 'card_evidence_version': 1}
    mem = {'chapter': 1, 'battle': dict(cur), '_records': []}
    policy.battle_end(mem, 'map')            # 1回目の観測は戦闘終了候補として保持
    policy.battle_end(mem, 'map')
    assert mem['castle_defenders']['アルマムーン'] == {
        'general': 'ガスパチョ', 'chapter': 1, 'evidence': 'defense_loss_capture', 'step': None}
    # 攻撃勝利で占領した城の記録は失効する。
    mem = {'chapter': 1, 'battle': {**cur, 'side': 'attack', 'castle': 'ゴーメン',
                                    'ally_hp': 40, 'enemy_hp': 0},
           '_records': []}
    mem['castle_defenders'] = {'ゴーメン': {'general': 'ガルバンゾー', 'chapter': 1}}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert 'ゴーメン' not in mem['castle_defenders']


def test_the_battle_panel_records_the_defender_for_the_next_sortie():
    mem = {'chapter': 1, '_records': []}
    cur = {'side': 'attack', 'castle': 'ゴーメン', 'enemy': 'ガルバンゾー', 'step': '1-A2'}
    policy._observe_defender(mem, cur)
    assert mem['castle_defenders'] == {'ゴーメン': {'general': 'ガルバンゾー', 'chapter': 1,
                                                   'evidence': 'battle_panel', 'step': '1-A2'}}
    assert records(mem, 'castle_defender_seen')[0]['castle'] == 'ゴーメン'
    # 防衛側の戦闘 (相手は城にいない) と、章に無い城は記録しない。
    mem = {'chapter': 1, '_records': []}
    policy._observe_defender(mem, {**cur, 'side': 'defense'})
    assert 'castle_defenders' not in mem
    policy._observe_defender(mem, {**cur, 'castle': 'ボス'})
    assert 'castle_defenders' not in mem
    # ボス将軍 (クイーン) も記録しない。
    policy._observe_defender(mem, {**cur, 'enemy': 'クイーン'})
    assert 'castle_defenders' not in mem


def test_battle_egg_plan_agrees_with_the_sortie_time_computation():
    """出撃時にも max_hp_sum が算出でき、戦闘時の battle_egg_plan と一致する。"""
    state = egg_mem('1-A2', cards=('フットバース', 'グルミー'))
    state['dropper_card_kit'] = {'1-A2': {'card': 'グルミー', 'defender': 'ガルバンゾー',
                                         'defender_evidence': 'castle_defenders', **PLAN}}
    opening(state, 'ガルバンゾー', 30, 'しゅじんこう', 90)
    plan = records(state, 'battle_egg_plan')[0]['observed_metric']
    assert plan['max_hp_sum'] == PLAN['max_hp_sum'] == 120
    assert plan['threshold'] == PLAN['threshold'] == 9
    assert plan['sortie_dropper'] == {'card': 'グルミー', 'defender': 'ガルバンゾー',
                                     'max_hp_sum': 120, 'threshold': 9, 'agreement': True}
    assert plan['droppers'] == ['グルミー']          # 携行した卵落札が開幕の候補になる
    assert plan['dropper'] == 'グルミー'


def test_a_defender_mismatch_leaves_the_sortie_plan_unused():
    state = egg_mem('1-C2', ally='ココット')
    state['dropper_card_kit'] = {'1-C2': {'card': 'グルミー', 'defender': 'ガルバンゾー',
                                         'defender_evidence': 'castle_defenders', **PLAN}}
    opening(state, 'キッシュ', 26, 'ココット', 24)
    plan = records(state, 'battle_egg_plan')[0]['observed_metric']
    assert plan['sortie_dropper'] is None            # この戦闘では出撃時計画を使わない
    mismatch = records(state, 'sortie_dropper_mismatch')
    assert len(mismatch) == 1
    assert mismatch[0]['expected_metric'] == {'defender': 'ガルバンゾー', 'card': 'グルミー'}
    assert mismatch[0]['observed_metric'] == {'battle_enemy': 'キッシュ'}
    # 戦闘側は実測した敵で算出する (ココット24 + キッシュ26 = 50 → 閾値3)。
    assert (plan['max_hp_sum'], plan['threshold']) == (50, 3)
