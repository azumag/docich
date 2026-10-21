"""将軍×切り札データによる卵ディニアル／卵落と、青ゲージ消費の接続。

owner 2026-10-03: 「全ての将軍と切り札のデータをちゃんと内部でデータとして持って、
その組み合わせで卵を使わせないか、落とさせるようにたたかう」「適宜青ゲージ
（ぶつかり合い時の青いバー→A連打で前進）も消費する」「切り札の『将軍戦ダメージ』は
gcgx kirihuda.html と wikiwiki 切り札表（両者完全一致）に統一して全32札を埋める」。

青バーと卵落の直接観測は未校正。予測による札選びと、選択・HP低下だけでは
敵を召喚不能と確定しないことを検証する。
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
    # Bを開く予定だけでは選択後の監視を始めない。
    assert 'egg_drop_watch' not in state['battle']
    assert not state['battle'].get('enemy_egg_dropped')


def test_a_charted_dropper_stays_with_the_chart_and_is_not_an_opening_tactic():
    # 82+30=112, 余り0。フットバースは成立してもチャートが所有する。
    state = mem('1-V2', ally='ヴィーナス')
    opening(state, 'ガルバンゾー', 30, 'ヴィーナス', 82)
    plan = state['battle']['egg_plan']
    assert plan['max_hp_sum'] == 112 and plan['remainder'] == 0
    assert 'フットバース' in plan['charted']
    assert 'フットバース' in plan['droppers']
    assert next(row for row in plan['cards'] if row['name'] == 'フットバース')['drops'] is True
    assert plan['dropper'] is None
    assert policy._egg_drop_tactics(state, state['battle'], 'ガルバンゾー', plan) == []
    flow = state['battle'].get('card_flow')
    assert flow is None or not str(flow.get('tactic_id', '')).startswith('eggdrop:')


def test_the_display_plan_and_the_tactic_use_the_same_max_hp_arithmetic():
    # キッシュ26 + ココット24 = 50 → 余り2。卵落3のブラッキーだけが成立する。
    state = mem('1-C2', ally='ココット')
    opening(state, 'キッシュ', 26, 'ココット', 24)
    plan = state['battle']['egg_plan']
    assert (plan['enemy_max_hp'], plan['ally_max_hp'], plan['max_hp_sum']) == (26, 24, 50)
    assert plan['remainder'] == 2 and plan['threshold'] == 3
    rows = {row['name']: row for row in plan['cards']}
    assert rows['ブラッキー']['id'] == 2 and rows['ブラッキー']['egg_drop'] == 3
    assert rows['ブラッキー']['drops'] is True
    assert rows['ダイチスイム']['id'] == 1 and rows['ダイチスイム']['egg_drop'] == 1
    assert rows['ダイチスイム']['drops'] is False
    assert plan['dropper'] == 'ブラッキー'
    assert plan['egg_drop'] == {'value': 3, 'threshold': 3, 'max_hp_sum': 50, 'drops': True}
    # 現在HPを変えても最大HP由来の判定は変わらない。
    for current in (1, 13, 999):
        state['battle']['enemy_hp'] = current
        again = policy._egg_plan(state, state['battle'])
        assert again['max_hp_sum'] == 50 and again['dropper'] == 'ブラッキー'


def test_egg_drop_boundaries_use_a_strict_greater_than():
    # 合計80 → 余り0、合計95 → 余り15。
    assert reference.egg_drop_remainder(80) == 0
    assert reference.egg_drop_threshold(80) == 1
    assert reference.egg_drop_remainder(95) == 15
    assert reference.egg_drop_threshold(95) == 16
    # 卵落 = 余り は不成立、余り+1 で成立 (>= にしない)。
    assert reference.card_drop_prediction('デッドガン', 80)['drops'] is False
    assert reference.card_drop_prediction('ダイチスイム', 80)['drops'] is True
    assert reference.card_drop_prediction('グルミー', 95)['drops'] is False
    assert max(reference.EGG_DROP_VALUES.values()) == 15
    # 余り2 でも同じ境界 (ピッグローラー2 は不成立、ブラッキー3 は成立)。
    assert reference.card_drop_prediction('ピッグローラー', 50)['drops'] is False
    assert reference.card_drop_prediction('ブラッキー', 50)['drops'] is True


def test_unknown_data_never_becomes_a_zero_filled_prediction():
    state = mem('1-A2')
    state['card_stock'] = {'きらきら': 1, 'ダイチスイム': 2, 'ブラッキー': 0}
    opening(state, '未知将軍', 10, 'しゅじんこう', 80)
    plan = state['battle']['egg_plan']
    assert plan['verdict'] == 'unknown'
    assert plan['enemy_max_hp'] is None and plan['max_hp_sum'] is None
    assert plan['remainder'] is None and plan['dropper'] is None
    rows = {row['name']: row for row in plan['cards']}
    assert rows['きらきら']['id'] is None and rows['きらきら']['egg_drop'] is None
    assert rows['きらきら']['drops'] is None
    assert rows['きらきら']['carried'] == 0
    assert rows['きらきら']['usable'] is False
    assert rows['きらきら']['unavailable'] == 'not_carried'
    # 在庫0は「所持なし」と断定せず、携行0として残す (行自体は落とす)。
    assert 'ブラッキー' not in rows


def test_boss_battles_are_excluded_and_eggless_or_used_eggs_do_not_fire():
    boss = mem('1-B1')
    opening(boss, 'クイーン', 70, 'しゅじんこう', 80)
    plan = boss['battle']['egg_plan']
    assert plan['excluded'] == 'boss' and plan['verdict'] == 'excluded'
    assert plan['dropper'] is None
    # 卵なし。
    plain = mem('1-C2', ally='ココット')
    opening(plain, 'キャラウェイ', 40, 'ココット', 24)
    assert plain['battle']['egg_plan']['verdict'] == 'no_egg'
    assert plain['battle']['egg_plan']['dropper'] is None
    # 使用済み: 召喚を観測した戦闘ではもう落とす対象がない。
    used = mem('1-C2', ally='ココット')
    opening(used, 'キッシュ', 26, 'ココット', 24)
    used['battle']['egg_battle'] = True
    assert policy._egg_plan(used, used['battle'])['verdict'] == 'used'
    assert policy._egg_plan(used, used['battle'])['dropper'] is None
    assert policy._egg_drop_tactics(used, used['battle'], 'キッシュ') == []
    # 落下推定 (未確認) でも再使用しない。
    dropped = mem('1-C2', ally='ココット')
    opening(dropped, 'キッシュ', 26, 'ココット', 24)
    dropped['battle']['enemy_egg_drop_expected'] = True
    assert policy._egg_plan(dropped, dropped['battle'])['verdict'] == 'dropped_unconfirmed'
    assert policy._egg_plan(dropped, dropped['battle'])['dropper'] is None
    assert policy._egg_drop_tactics(dropped, dropped['battle'], 'キッシュ') == []


def test_used_and_uncarried_cards_are_not_offered_even_when_they_would_drop():
    state = mem('1-C2', ally='ココット')
    state['card_stock'] = {'ダイチスイム': 2, 'ブラッキー': 1, 'イッテツーン': 1}
    opening(state, 'キッシュ', 26, 'ココット', 24)
    state['battle']['cards_used'] = ['ブラッキー']
    plan = policy._egg_plan(state, state['battle'])
    rows = {row['name']: row for row in plan['cards']}
    assert rows['ブラッキー']['available'] == 0
    assert rows['ブラッキー']['usable'] is False
    assert rows['ブラッキー']['unavailable'] == 'used'
    assert rows['イッテツーン']['usable'] is False
    assert rows['イッテツーン']['unavailable'] == 'not_carried'
    assert 'ブラッキー' not in plan['droppers'] and 'イッテツーン' not in plan['droppers']
    assert plan['dropper'] is None
    assert plan['verdict'] == 'not_droppable'


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


def test_melee_does_not_treat_an_old_inferred_egg_drop_as_a_receipt():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    actions = policy._melee_step(state, _melee('クミン', True))
    assert actions == []
    record = state['_records'][-1]
    assert record['decision'] == 'battle_melee'
    assert record['melee_control_mode'] == 'egg_safe_hold'
    assert record['enemy_egg_dropped'] is None
    assert record['a_frames_sent'] == 0


def test_melee_holds_against_an_enemy_that_still_has_its_egg():
    # クミンは激突判定あり・卵はまだ落ちていない → 従来どおり入力保留。
    state = {'_records': [], 'chapter': 1, 'variant': 'chart'}
    actions = policy._melee_step(state, _melee('クミン', False))
    assert actions == []
    record = state['_records'][-1]
    assert record['melee_control_mode'] == 'egg_safe_hold'
    assert record['enemy_egg_dropped'] is None
    assert record['egg_risk_flags']['clash_position'] is True


def test_an_inferred_egg_drop_does_not_skip_the_unarmed_clash_check():
    still_held = policy._unarmed_clash_risk(_melee('クミン', False))
    assert still_held is True
    assert policy._unarmed_clash_risk(_melee('クミン', True)) is True


def test_selected_dropper_with_a_measured_hp_drop_remains_unconfirmed():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart', 'battle': {
        'enemy': 'クミン', 'ally': 'しゅじんこう', 'enemy_hp': 62, 'ally_hp': 80,
        'start_enemy_hp': 70, 'start_ally_hp': 80, 'ref_ally_hp': 90, 'step': '1-A1',
        'side': 'attack', 'castle': 'キカンドン', 'clashed': False, 'planned_cards': [], 'card_evidence_version': 1,
        'cards_used': [], 'cards_selected': ['イッテツーン'], 'cards_unclassified': [],
        'egg_drop_watch': {'card': 'イッテツーン', 'hp': 70, 'value': 8, 'threshold': 6,
                           'max_hp_sum': 117, 'evidence_version': 2}}}
    view = screen('クミン', 62, 'しゅじんこう', 80)
    policy.battle_step(view, state)
    assert not state['battle'].get('enemy_egg_dropped')
    assert state['battle']['enemy_egg_drop_expected'] is True
    assert 'egg_drop_watch' not in state['battle']
    record = next(r for r in state['_records'] if r['decision'] == 'battle_egg_drop_unconfirmed')
    assert record['card'] == 'イッテツーン'
    assert record['egg_drop'] == {'value': 8, 'threshold': 6, 'max_hp_sum': 117}
    assert record['observed_metric'] == {'enemy_hp': 62, 'hp_at_card': 70, 'selected': True,
                                        'egg_drop_confirmation': 'unclassified'}


def test_no_claim_when_the_selected_dropper_never_moved_the_hp():
    state = {'_records': [], 'chapter': 1, 'variant': 'chart', 'battle': {
        'enemy': 'クミン', 'ally': 'しゅじんこう', 'enemy_hp': 70, 'ally_hp': 80,
        'start_enemy_hp': 70, 'start_ally_hp': 80, 'ref_ally_hp': 90, 'step': '1-A1',
        'side': 'attack', 'castle': 'キカンドン', 'clashed': False, 'planned_cards': [], 'card_evidence_version': 1,
        'cards_used': [], 'cards_selected': ['イッテツーン'], 'cards_unclassified': [],
        'egg_drop_watch': {'card': 'イッテツーン', 'hp': 70, 'value': 8, 'threshold': 6,
                           'max_hp_sum': 117, 'evidence_version': 2}}}
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
                           'max_hp_sum': 117, 'evidence_version': 2}}}
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
                                     'threshold': 6, 'max_hp_sum': 117, 'evidence_version': 2}


def test_battle_end_and_a_new_opponent_drop_the_old_plan_and_cards():
    # 画面切替・戦闘終了・相手変更で旧将軍・旧携行品・旧計算を残さない。
    state = mem('1-C2', ally='ココット')
    opening(state, 'キッシュ', 26, 'ココット', 24)
    assert state['battle']['egg_plan']['enemy'] == 'キッシュ'
    assert state['battle']['egg_plan']['verdict'] == 'droppable'
    # パネルを2回連続で失った観測で戦闘記録ごと消える。
    policy.battle_end(state, 'map')
    assert 'battle' in state
    policy.battle_end(state, 'map')
    assert 'battle' not in state and 'egg_plan' not in state
    # 別の相手の新しい戦闘は、前の将軍・携行・計算を持ち越さない。
    other = mem('1-C3', ally='ココット')
    opening(other, 'キャラウェイ', 40, 'ココット', 24)
    plan = other['battle']['egg_plan']
    assert plan['enemy'] == 'キャラウェイ' and plan['verdict'] == 'no_egg'
    assert plan['dropper'] is None and plan['cards'] == []


def test_non_threatening_egg_owner_has_documented_verdict():
    state = mem('1-C2', ally='ココット')
    opening(state, 'ミモザ', reference.general_profile('ミモザ')['max_hp'], 'ココット', 24)
    plan = state['battle']['egg_plan']
    assert plan['has_egg'] is True and plan['threat'] is False
    assert plan['verdict'] == 'not_threat' and plan['dropper'] is None
