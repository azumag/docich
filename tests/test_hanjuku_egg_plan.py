"""将軍×切り札データによる卵ディニアル／卵落と、青ゲージ消費の接続。

owner 2026-10-03: 「全ての将軍と切り札のデータをちゃんと内部でデータとして持って、
その組み合わせで卵を使わせないか、落とさせるようにたたかう」「適宜青ゲージ
（ぶつかり合い時の青いバー→A連打で前進）も消費する」「切り札の『将軍戦ダメージ』は
gcgx kirihuda.html と wikiwiki 切り札表（両者完全一致）に統一して全32札を埋める」。

青バーそのものの画素検出は follow-up（実機計測待ち）。ここでは予測側:
「卵の脅威が消えた局面だけ全力A連打」を検証する。
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy, hanjuku_reference as reference
from docich.hanjuku_egg_reference import GENERAL_EGGS, enemy_egg_triggers, general_max_hp
from docich.hanjuku_screen import Battle, Screen


# gcgx kirihuda.html と wikiwiki 切り札表が一致する全32札（転記元の確認用アンカー）。
CANONICAL_ANCHORS = {
    'ブラッキー': (2, 22, 50, 20, 0, 3),
    'クースカン': (13, 0, 56, 20, 1, 0),
    'マグネガキン': (27, 48, 48, 80, 4, 8),
    'グルミー': (19, 68, 96, 100, 4, 15),
    'ころぼぐんだん': (23, 1, 1, 1, 1, 12),
    'ファバード': (31, 240, 240, 100, 20, 0),
    'イッテツーン': (0, 10, 32, 16, 0, 8),
    'エンジェリン': (22, 0, 0, 0, 0, 0),
}


def test_the_card_table_holds_the_whole_canonical_32_card_table():
    assert set(reference.CARDS) == set(reference.ALL_CARD_IDS) == set(reference.EGG_DROP_VALUES)
    assert len(reference.CARDS) == 32
    fields = {'id', 'general_damage', 'monster_damage', 'boss_damage', 'soldier_damage',
              'egg_drop', 'price', 'effect'}
    for name, card in reference.CARDS.items():
        assert set(card) == fields, name
        assert card['id'] == reference.ALL_CARD_IDS[name], name
        assert card['egg_drop'] == reference.EGG_DROP_VALUES[name], name
        assert all(type(card[k]) is int for k in
                   ('id', 'general_damage', 'monster_damage', 'boss_damage', 'soldier_damage',
                    'egg_drop')), name
        assert card['price'] is None or type(card['price']) is int, name
    for name, values in CANONICAL_ANCHORS.items():
        card = reference.CARDS[name]
        assert (card['id'], card['general_damage'], card['monster_damage'],
                card['boss_damage'], card['soldier_damage'], card['egg_drop']) == values, name
    # 旧CARDSの未使用10件は正典値へ置き換わっている。
    assert reference.CARDS['ダイチスイム']['general_damage'] == 6
    assert reference.CARDS['グリンボー']['general_damage'] == 32
    assert reference.CARDS['ゼンマイン']['general_damage'] == 32
    assert reference.CARDS['ノリウツール']['general_damage'] == 0
    assert reference.CARDS['ハリケーン']['general_damage'] == 10
    # 敵卵の開幕判定はID合計のまま（#1180 の契約を維持）。
    assert [reference.CARD_IDS[n] for n in ('ブラッキー', 'クースカン', 'ファバード')] == [2, 13, 31]


def test_egg_droppers_combine_the_general_max_hp_sum_with_the_card_value():
    # しゅじんこう 90 + クミン 27 = 117 → 117 mod 16 = 5 → 卵落 6 以上で落とせる。
    assert (general_max_hp('しゅじんこう') + general_max_hp('クミン')) == 117
    assert reference.egg_drop_threshold(117) == 6
    assert reference.egg_droppers(['イッテツーン', 'クースカン', 'マグネガキン'], 117) == [
        'イッテツーン', 'マグネガキン']         # 卵落8の強い順、IDの小さい札が先
    assert reference.egg_droppers(['フットバース', 'ブラッキー'], 117) == []   # 5/3 では不足
    # 合計140 (140 mod 16 = 12) では卵落12では境界で落ちないが、15なら落ちる。
    assert reference.egg_drop_threshold(140) == 13
    assert reference.egg_droppers(['ころぼぐんだん'], 140) == []
    assert reference.egg_droppers(['ころぼぐんだん', 'グルミー'], 140) == ['グルミー']
    # 最大HPが読めない札は候補にしない (fail-closed)。
    assert reference.egg_droppers(['きらきら'], 117) == []


def test_every_selectable_dropper_is_eligible_for_the_survival_path():
    droppers = [name for name in policy.CARD_NAMES if reference.egg_drop_value(name)]
    assert droppers and set(droppers) <= set(policy.SURVIVAL_CARDS)
    assert set(policy.SURVIVAL_RANK) == set(policy.SURVIVAL_CARDS)


def mem(step, ally='しゅじんこう', chapter=1, cards=()):
    state = {'chapter': chapter, 'month': f'{chapter}-10', 'active': step, 'variant': 'chart',
             'orders': {step: 'pending'}, 'picked': [], '_records': [],
             'attack': {'general': ally, 'castle': 'キカンドン', 'side': 'attack', 'step': step}}
    if cards:
        state['card_override'] = {step: list(cards)}
    return state


def screen(enemy, enemy_hp, ally, ally_hp):
    return Screen(lines=[], hand=None, text='', kind='battle',
                  battle=Battle(enemy, enemy_hp, ally, ally_hp))


def opening(state, enemy, enemy_hp, ally, ally_hp):
    """安定した2回の読みで戦闘記録を作る（本番と同じ経路）。"""
    view = screen(enemy, enemy_hp, ally, ally_hp)
    assert policy.battle_step(view, state) == []        # 1回目の読みは安定待ち
    policy.battle_step(view, state)
    assert state['battle']['enemy'] == enemy and state['battle']['step'] == state['active']
    return view


def test_the_egg_plan_is_recorded_when_the_enemy_can_use_its_egg():
    # キッシュ: 卵持ち・思考タイプ2 (壁の判定あり) → 卵を使える能力がある。
    assert enemy_egg_triggers('キッシュ').has_egg is True
    state = mem('1-C2', ally='ココット')
    opening(state, 'キッシュ', 26, 'ココット', 24)
    plan = next(r for r in state['_records'] if r['decision'] == 'battle_egg_plan')
    observed = plan['observed_metric']
    assert observed['threat'] is True and observed['has_egg'] is True
    assert observed['carried'] == ['ダイチスイム', 'ダイチスイム', 'ブラッキー']
    assert observed['id_sum'] == 4 and observed['deny_opening_egg'] is True   # 4 < 48
    assert observed['max_hp_sum'] == 50 and observed['threshold'] == 3
    assert observed['droppers'] == ['ブラッキー']       # 24 + 26 = 50 → 余り2 → 卵落3
    assert observed['dropper'] == 'ブラッキー'
    assert plan['reason']


def test_no_egg_plan_record_against_a_general_without_an_egg():
    # キャラウェイは卵なし → 計画は立たない（戦闘開始と通常の白兵判断だけが残る）。
    state = mem('1-A1')
    opening(state, 'キャラウェイ', 40, 'しゅじんこう', 80)
    decisions = [r['decision'] for r in state['_records']]
    assert decisions[0] == 'battle_start'
    assert 'battle_egg_plan' not in decisions


def test_a_spare_dropper_opens_the_battle_to_force_the_egg_drop():
    # 1-C2 はダイチスイム/ブラッキーを携行するが、キッシュへの戦術はチャートに無い。
    state = mem('1-C2', ally='ココット')
    view = opening(state, 'キッシュ', 26, 'ココット', 24)
    assert policy.battle_step(view, state) == [policy.pad('b')]
    assert state['battle']['card_flow']['card'] == 'ブラッキー'
    assert state['battle']['card_flow']['tactic_id'].startswith('eggdrop:')
    record = next(r for r in state['_records'] if r['decision'] == 'battle_card')
    assert record['egg_drop'] == {'value': 3, 'threshold': 3, 'max_hp_sum': 50, 'drops': True}
    assert record['egg_plan']['threat'] is True
    assert record['egg_plan']['deny_opening_egg'] is True
    assert record['reason'].startswith('携行札の卵落')
    # 監視対象は選択前のHPで置かれ、まだ卵は落ちていない扱い。
    assert state['battle']['egg_drop_watch']['card'] == 'ブラッキー'
    assert not state['battle'].get('enemy_egg_dropped')


def test_the_chart_still_owns_the_card_it_names_even_when_that_card_drops_eggs():
    # 1-V2 のフットバースは開幕戦術としてチャートに書かれている。この組み合わせでは
    # フットバースが卵落札なので (ヴィーナス82 + ガルバンゾー30 = 112 → 余り0)、
    # 卵ディニアル側が先に取ってはいけない。
    state = mem('1-V2', ally='ヴィーナス')
    view = opening(state, 'ガルバンゾー', 30, 'ヴィーナス', 82)
    plan = policy._egg_plan(state, state['battle'])
    assert plan['droppers'] == ['フットバース']
    assert plan['dropper'] is None            # チャートが指す札なので開幕の別札に回さない
    policy.battle_step(view, state)
    assert state['battle']['card_flow']['card'] == 'フットバース'
    assert not state['battle']['card_flow']['tactic_id'].startswith('eggdrop:')


def test_no_proactive_drop_against_a_general_without_an_egg():
    # キャラウェイは卵を持たない → 携行札は温存し、開幕札は消費しない。
    state = mem('1-C2')
    view = opening(state, 'キャラウェイ', 40, 'しゅじんこう', 80)
    policy.battle_step(view, state)
    assert state['battle'].get('card_flow') is None
    assert not [r for r in state['_records'] if r['decision'] == 'battle_card']


def test_the_chart_locked_boss_kits_record_that_no_dropper_is_carried():
    # 1-B1 は携行札がチャート固定 (クースカン+ノリウツール、どちらも卵落0)。
    # 敵は卵を使えるが、落とせる札を持ち出せないという事実をそのまま残す。
    state = mem('1-B1')
    opening(state, 'クイーン', 70, 'しゅじんこう', 80)
    observed = next(r for r in state['_records']
                    if r['decision'] == 'battle_egg_plan')['observed_metric']
    assert observed['threat'] is True
    assert observed['carried'] == ['クースカン', 'ノリウツール']
    assert observed['droppers'] == [] and observed['dropper'] is None
    assert observed['deny_opening_egg'] is True
    assert state['battle'].get('card_flow') is None


def _melee(enemy, egg_dropped):
    return {'enemy': enemy, 'ally': 'しゅじんこう', 'step': None, 'side': 'attack',
            'enemy_hp': 62, 'ally_hp': 80, 'start_enemy_hp': 70, 'start_ally_hp': 80,
            'clashed': True, 'planned_cards': [], 'enemy_egg_dropped': egg_dropped}


def test_melee_spends_the_blue_gauge_once_the_egg_is_gone():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    actions = policy._melee_step(state, _melee('クミン', True))
    assert sum(a.get('type') == 'pad' for a in actions) == policy.POWER_TAPS
    record = state['_records'][-1]
    assert record['decision'] == 'battle_melee'
    assert record['melee_control_mode'] == 'power_mash'
    assert record['enemy_egg_dropped'] is True
    assert record['a_frames_sent'] == policy.POWER_TAPS * 3
    assert '青ゲージ' in record['reason']


def test_melee_holds_against_an_enemy_that_still_has_its_egg():
    # クミンは激突判定あり・卵はまだ落ちていない → 従来どおり入力保留。
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    actions = policy._melee_step(state, _melee('クミン', False))
    assert actions == []
    record = state['_records'][-1]
    assert record['melee_control_mode'] == 'egg_safe_hold'
    assert record['enemy_egg_dropped'] is False
    assert record['egg_risk_flags']['clash_position'] is True


def test_a_dropped_egg_ends_the_unarmed_clash_check():
    still_held = policy._unarmed_clash_risk(_melee('クミン', False))
    assert still_held is True
    assert policy._unarmed_clash_risk(_melee('クミン', True)) is False


def test_selected_dropper_with_a_measured_hp_drop_marks_the_egg_gone():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart', 'battle': {
        'enemy': 'クミン', 'ally': 'しゅじんこう', 'enemy_hp': 62, 'ally_hp': 80,
        'start_enemy_hp': 70, 'start_ally_hp': 80, 'ref_ally_hp': 90, 'step': '1-A1',
        'side': 'attack', 'castle': 'キカンドン', 'clashed': False, 'planned_cards': [], 'card_evidence_version': 1,
        'cards_used': [], 'cards_selected': ['イッテツーン'], 'cards_unclassified': [],
        'egg_drop_watch': {'card': 'イッテツーン', 'hp': 70, 'value': 8, 'threshold': 6,
                           'max_hp_sum': 117}}}
    view = screen('クミン', 62, 'しゅじんこう', 80)
    policy.battle_step(view, state)
    assert state['battle']['enemy_egg_dropped'] is True
    assert 'egg_drop_watch' not in state['battle']
    record = next(r for r in state['_records'] if r['decision'] == 'battle_egg_dropped')
    assert record['card'] == 'イッテツーン'
    assert record['egg_drop'] == {'value': 8, 'threshold': 6, 'max_hp_sum': 117}
    assert record['observed_metric'] == {'enemy_hp': 62, 'hp_at_card': 70, 'selected': True}


def test_no_claim_when_the_selected_dropper_never_moved_the_hp():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart', 'battle': {
        'enemy': 'クミン', 'ally': 'しゅじんこう', 'enemy_hp': 70, 'ally_hp': 80,
        'start_enemy_hp': 70, 'start_ally_hp': 80, 'ref_ally_hp': 90, 'step': '1-A1',
        'side': 'attack', 'castle': 'キカンドン', 'clashed': False, 'planned_cards': [], 'card_evidence_version': 1,
        'cards_used': [], 'cards_selected': ['イッテツーン'], 'cards_unclassified': [],
        'egg_drop_watch': {'card': 'イッテツーン', 'hp': 70, 'value': 8, 'threshold': 6,
                           'max_hp_sum': 117}}}
    policy.battle_step(screen('クミン', 70, 'しゅじんこう', 80), state)
    assert not state['battle'].get('enemy_egg_dropped')
    assert state['battle']['egg_drop_watch']['card'] == 'イッテツーン'


def test_no_claim_before_the_card_reaches_the_list_selection():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart', 'battle': {
        'enemy': 'クミン', 'ally': 'しゅじんこう', 'enemy_hp': 62, 'ally_hp': 80,
        'start_enemy_hp': 70, 'start_ally_hp': 80, 'ref_ally_hp': 90, 'step': '1-A1',
        'side': 'attack', 'castle': 'キカンドン', 'clashed': False, 'planned_cards': [], 'card_evidence_version': 1,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'egg_drop_watch': {'card': 'イッテツーン', 'hp': 70, 'value': 8, 'threshold': 6,
                           'max_hp_sum': 117}}}
    policy.battle_step(screen('クミン', 62, 'しゅじんこう', 80), state)
    assert not state['battle'].get('enemy_egg_dropped')


def test_the_survival_rescue_picks_the_dropper_and_watches_it():
    # 救済 (HP低下) の選択も、卵落札なら同じ根拠 (最大HP合計→選択後HP低下) で監視する。
    cur = {'enemy': 'クミン', 'ally': 'しゅじんこう', 'ref_ally_hp': 90,
           'enemy_hp': 30, 'ally_hp': 11, 'step': '1-A1'}
    assert policy._rescue_card(['マグネガキン', 'イッテツーン'], cur) == 'マグネガキン'
    policy._watch_egg_drop(cur, 'マグネガキン', cur['enemy_hp'],
                           policy._egg_drop_evidence(cur, 'マグネガキン'))
    assert cur['egg_drop_watch'] == {'card': 'マグネガキン', 'hp': 30, 'value': 8,
                                     'threshold': 6, 'max_hp_sum': 117}


# --- Sortie dropper carry tests (follow-up #1553) ---

def mem_for_sortie(step, chapter=1, general='どうし', cards=(), stock=None):
    """Create a mem dict for sortie testing."""
    return {
        'chapter': chapter,
        'month': f'{chapter}-10',
        'active': step,
        'variant': 'chart',
        'orders': {step: 'pending'},
        'picked': [],
        '_records': [],
        'attack': {'general': general, 'castle': 'けっかい', 'side': 'attack', 'step': step},
        'garrison': {},
        'card_stock': stock or {},
        'card_override': {},
        'rare_card_kit': {},
        'strong_card_kit': {},
        'kit_spent': {},
        'card_drop': {},
    }


def test_sortie_dropper_added_for_boss_order_with_hero():
    # Adjusted order for boss castle with hero: enemy is chapter boss (クイーン HP=70)
    # Hero max HP=90, sum=160, threshold=1. Many droppers available.
    # イッテツーン (egg_drop=8, ID=0) should be added if slot and budget allow.
    step = 'A:test123:1'
    order = {'target': 'けっかい', 'general': 'どうし', 'step': step, 'purpose': 'attack',
             'after': None, 'cards': ['ダイチスイム', 'ブラッキー']}
    mem = mem_for_sortie(step, chapter=1, general='どうし', stock={'イッテツーン': 2, 'マグネガキン': 1})
    cards = policy._deploy_cards(order, mem)
    # Should add イッテツーン as dropper
    assert 'イッテツーン' in cards, f'Expected イッテツーン in {cards}'
    # ID sum should be < 48 (ダイチスイム=1, ブラッキー=2, イッテツーン=0 = 3)
    id_sum = sum(policy.CARD_IDS.get(c, policy.ENEMY_EGG_CARD_ID_SUM) for c in cards)
    assert id_sum < policy.ENEMY_EGG_CARD_ID_SUM, f'ID sum {id_sum} >= 48'
    # Record should be created
    rec = next((r for r in mem['_records'] if r['decision'] == 'sortie_dropper_added'), None)
    assert rec is not None, 'sortie_dropper_added record not created'
    assert rec['observed_metric']['enemy'] == 'クイーン'
    assert rec['observed_metric']['ally'] == 'どうし'
    assert rec['observed_metric']['max_hp_sum'] == 160
    assert rec['observed_metric']['threshold'] == 1
    assert rec['observed_metric']['dropper'] == 'イッテツーン'
    assert rec['observed_metric']['dropper_egg_drop'] == 8
    assert rec['observed_metric']['id_sum'] == 3


def test_sortie_dropper_not_added_when_enemy_unknown():
    # Non-boss order: enemy unknown at sortie time -> fail-closed, no dropper added
    step = 'A:test123:2'
    order = {'target': 'キカンドン', 'general': 'ココット', 'step': step, 'purpose': 'attack',
             'after': None, 'cards': ['ダイチスイム', 'ブラッキー']}
    mem = mem_for_sortie(step, chapter=1, general='ココット', stock={'イッテツーン': 2})
    cards = policy._deploy_cards(order, mem)
    # No dropper added
    assert cards == ['ダイチスイム', 'ブラッキー'], f'Unexpected cards: {cards}'
    # No sortie_dropper_added record
    rec = next((r for r in mem['_records'] if r['decision'] == 'sortie_dropper_added'), None)
    assert rec is None, 'sortie_dropper_added should not be created for unknown enemy'


def test_sortie_dropper_respects_id_budget():
    # If adding dropper would exceed ID budget (47), it should not be added
    step = 'A:test123:3'
    # ファバード ID=31, グルミー ID=19 -> sum=50 > 47 if we add another high-ID card
    order = {'target': 'けっかい', 'general': 'どうし', 'step': step, 'purpose': 'attack',
             'after': None, 'cards': ['ファバード', 'グルミー']}  # IDs 31+19=50, already over!
    # But _cap_card_ids should cap it first. Let's use a case where current sum + dropper >= 48
    order = {'target': 'けっかい', 'general': 'どうし', 'step': step, 'purpose': 'attack',
             'after': None, 'cards': ['ファバード', 'クースカン']}  # IDs 31+13=44
    mem = mem_for_sortie(step, chapter=1, general='どうし', stock={'マグネガキン': 1})  # ID=27, 44+27=71 >= 48
    cards = policy._deploy_cards(order, mem)
    # Dropper should not be added due to budget
    assert 'マグネガキン' not in cards, f'Dropper added despite budget: {cards}'
    id_sum = sum(policy.CARD_IDS.get(c, policy.ENEMY_EGG_CARD_ID_SUM) for c in cards)
    assert id_sum < policy.ENEMY_EGG_CARD_ID_SUM


def test_sortie_dropper_respects_carry_slots():
    # CARRY_SLOTS=3, if already 3 cards, no dropper added
    step = 'A:test123:4'
    order = {'target': 'けっかい', 'general': 'どうし', 'step': step, 'purpose': 'attack',
             'after': None, 'cards': ['ダイチスイム', 'ブラッキー', 'クースカン']}  # 3 cards already
    mem = mem_for_sortie(step, chapter=1, general='どうし', stock={'イッテツーン': 2})
    cards = policy._deploy_cards(order, mem)
    assert len(cards) == 3, f'Should not exceed 3 slots: {cards}'
    assert 'イッテツーン' not in cards


def test_sortie_dropper_not_added_for_strict_boss_order():
    # Strict boss order (1-B1) has fixed kit, dropper logic bypassed
    step = '1-B1'
    order = {'target': 'けっかい', 'general': 'どうし', 'step': step, 'purpose': 'attack',
             'after': None, 'cards': ['クースカン', 'ノリウツール']}
    mem = mem_for_sortie(step, chapter=1, general='どうし', stock={'イッテツーン': 2})
    cards = policy._deploy_cards(order, mem)
    assert cards == ['クースカン', 'ノリウツール'], f'Strict boss kit modified: {cards}'
    rec = next((r for r in mem['_records'] if r['decision'] == 'sortie_dropper_added'), None)
    assert rec is None


def test_battle_egg_plan_max_hp_sum_computable_at_sortie_time():
    # The max_hp_sum for a known enemy at sortie time should be computable
    # using the ally's max HP (hero remembered full strength or general's max HP)
    # and enemy's max HP from GENERAL_HP.
    from docich.hanjuku_egg_reference import general_max_hp
    from docich.hanjuku_reference import egg_drop_threshold
    
    # For hero (ref_ally_hp=90) vs クイーン (HP=70): sum=160, threshold=1
    ally_max = 90  # hero remembered full strength
    enemy_max = general_max_hp('クイーン')
    assert enemy_max == 70
    max_hp_sum = ally_max + enemy_max
    assert max_hp_sum == 160
    assert egg_drop_threshold(max_hp_sum) == 1
    
    # For ココット (HP=24) vs クミン (HP=27): sum=51, threshold=4
    ally_max = general_max_hp('ココット')
    enemy_max = general_max_hp('クミン')
    assert ally_max == 24
    assert enemy_max == 27
    max_hp_sum = ally_max + enemy_max
    assert max_hp_sum == 51
    assert egg_drop_threshold(max_hp_sum) == 4
    
    # This demonstrates max_hp_sum is computable at sortie time when both
    # ally and enemy max HPs are known (which is true for boss orders with hero).


def test_blue_gauge_detection_gated_until_calibrated():
    # Blue gauge detection returns None until _BLUE_GAUGE_CALIBRATED=True
    from docich.hanjuku_screen import _detect_blue_gauge, Frame, _BLUE_GAUGE_CALIBRATED
    assert _BLUE_GAUGE_CALIBRATED is False
    
    # Even with a clear blue gauge pattern, detection returns None
    width, height = 256, 224
    rgb = bytearray(width * height * 3)
    for i in range(len(rgb)):
        rgb[i] = 50
    for x in range(48, 209):
        y = 150
        idx = (y * width + x) * 3
        rgb[idx] = 0
        rgb[idx+1] = 120
        rgb[idx+2] = 200
    frame = Frame(rgb=bytes(rgb), width=width, height=height)
    result = _detect_blue_gauge(frame)
    assert result is None, 'Detection should be gated until calibrated'


def test_blue_gauge_cross_check_discrepancy_logged():
    # When blue gauge is detected (mocked) and disagrees with prediction,
    # discrepancy is logged but prediction is followed (fail-closed)
    # Case 1: Prediction says egg dropped (True), gauge says not dropped (fill >= 0.2)
    from docich.hanjuku_screen import BlueGauge
    
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    cur = {'enemy': 'クミン', 'ally': 'しゅじんこう', 'step': '1-A1',
           'enemy_hp': 62, 'ally_hp': 80, 'start_enemy_hp': 70, 'start_ally_hp': 80,
           'clashed': True, 'planned_cards': [], 'enemy_egg_dropped': True,
           'side': 'attack', 'castle': 'キカンドン',
           'blue_gauge': {'fill': 0.8, 'confidence': 0.9, 'x0': 48, 'x1': 200}}
    
    # Mock _charted_melee to return False
    import docich.hanjuku_policy as policy_module
    original_charted = policy_module._charted_melee
    policy_module._charted_melee = lambda mem, cur: False
    
    try:
        policy._melee_step(state, cur)
    finally:
        policy_module._charted_melee = original_charted
    
    # Should log discrepancy
    rec = next((r for r in state['_records'] if r['decision'] == 'blue_gauge_discrepancy'), None)
    assert rec is not None, 'blue_gauge_discrepancy not logged'
    assert rec['observed_metric']['blue_gauge_fill'] == 0.8
    assert rec['observed_metric']['blue_gauge_confidence'] == 0.9
    assert rec['observed_metric']['predicted_egg_dropped'] is True
    assert rec['observed_metric']['gauge_says_dropped'] is False  # fill=0.8 >= 0.2
    
    # blue_gauge_agrees should be False in battle_melee record
    melee_rec = next((r for r in state['_records'] if r['decision'] == 'battle_melee'), None)
    assert melee_rec is not None
    assert melee_rec['blue_gauge_agrees'] is False


def test_blue_gauge_cross_check_agrees_when_both_say_dropped():
    # Case 2: Prediction says egg dropped (True), gauge says dropped (fill < 0.2)
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    cur = {'enemy': 'クミン', 'ally': 'しゅじんこう', 'step': '1-A1',
           'enemy_hp': 62, 'ally_hp': 80, 'start_enemy_hp': 70, 'start_ally_hp': 80,
           'clashed': True, 'planned_cards': [], 'enemy_egg_dropped': True,
           'side': 'attack', 'castle': 'キカンドン',
           'blue_gauge': {'fill': 0.1, 'confidence': 0.9, 'x0': 48, 'x1': 100}}
    
    import docich.hanjuku_policy as policy_module
    original_charted = policy_module._charted_melee
    policy_module._charted_melee = lambda mem, cur: False
    
    try:
        policy._melee_step(state, cur)
    finally:
        policy_module._charted_melee = original_charted
    
    # Should NOT log discrepancy (they agree)
    rec = next((r for r in state['_records'] if r['decision'] == 'blue_gauge_discrepancy'), None)
    assert rec is None, 'blue_gauge_discrepancy should not be logged when they agree'
    
    melee_rec = next((r for r in state['_records'] if r['decision'] == 'battle_melee'), None)
    assert melee_rec is not None
    assert melee_rec['blue_gauge_agrees'] is True


def test_blue_gauge_cross_check_confidence_threshold():
    # Confidence < 0.6 should not trigger cross-check
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    cur = {'enemy': 'クミン', 'ally': 'しゅじんこう', 'step': '1-A1',
           'enemy_hp': 62, 'ally_hp': 80, 'start_enemy_hp': 70, 'start_ally_hp': 80,
           'clashed': True, 'planned_cards': [], 'enemy_egg_dropped': True,
           'side': 'attack', 'castle': 'キカンドン',
           'blue_gauge': {'fill': 0.8, 'confidence': 0.5, 'x0': 48, 'x1': 200}}
    
    import docich.hanjuku_policy as policy_module
    original_charted = policy_module._charted_melee
    policy_module._charted_melee = lambda mem, cur: False
    
    try:
        policy._melee_step(state, cur)
    finally:
        policy_module._charted_melee = original_charted
    
    # Should NOT log discrepancy (confidence too low)
    rec = next((r for r in state['_records'] if r['decision'] == 'blue_gauge_discrepancy'), None)
    assert rec is None, 'blue_gauge_discrepancy should not be logged when confidence < 0.6'
    
    melee_rec = next((r for r in state['_records'] if r['decision'] == 'battle_melee'), None)
    assert melee_rec is not None
    assert melee_rec['blue_gauge_agrees'] is None  # Not evaluated
