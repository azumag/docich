"""owner 2026-11-26: ボス戦で携行切り札を使わなかったら issue 用の証拠を残す。"""
from docich.hanjuku.flows import battle as battle_flow


def _battle(**changes):
    cur = {'ally': 'カシス', 'enemy': 'ハデス', 'castle': 'ボス', 'side': 'attack',
           'ally_hp': 40, 'enemy_hp': 100, 'planned_cards': ['ミックミー']}
    cur.update(changes)
    return cur


def test_a_boss_battle_with_an_unused_carried_card_is_flagged():
    assert battle_flow._boss_card_left_unused({}, _battle()) == 'ミックミー'


def test_a_used_card_is_not_a_miss():
    cur = _battle(cards_used=['ミックミー'])
    assert battle_flow._boss_card_left_unused({}, cur) is None


def test_a_non_boss_battle_is_not_flagged():
    cur = _battle(enemy='エシャロット', castle='ウラノポリス')
    assert battle_flow._boss_card_left_unused({}, cur) is None


def test_the_miss_is_noted_once():
    cur = _battle(boss_card_missed_noted=True)
    assert battle_flow._boss_card_left_unused({}, cur) is None


def test_no_carried_card_is_not_a_miss():
    cur = _battle(planned_cards=[])
    assert battle_flow._boss_card_left_unused({}, cur) is None

def test_an_unread_boss_name_is_still_a_boss_by_castle():
    cur = _battle(enemy=None)
    assert battle_flow._boss_card_left_unused({}, cur) == 'ミックミー'


def test_battle_end_emits_self_contained_card_miss_evidence():
    """docich#5749: the #5747 shape leaves outcome/HP/gate/flow evidence."""
    mem = {'chapter': 1, 'tick': 42,
           'battle': {'ally': 'ゼウス', 'enemy': 'クイーン', 'castle': 'けっかい',
                      'side': 'attack', 'step': None, 'ally_hp': 0, 'enemy_hp': 30,
                      'start_ally_hp': 74, 'start_enemy_hp': 70,
                      'planned_cards': ['クースカン'], 'cards_used': [],
                      'card_evidence_version': 1,
                      'cards_selected': [], 'cards_unclassified': [], 'cards_missing': [],
                      'critical_survival_needed_observed': True,
                      'survival_needed_observed': True,
                      'enemy_summon_observed': True,
                      'card_flow_opens': [
                          {'card': 'クースカン', 'stage': 'list', 'source': 'chart_tactic',
                           'tick': 40, 'ally_hp': 11, 'enemy_hp': 50}],
                      'card_flow_history': [
                          {'seq': 1, 'tick': 40, 'event': 'open', 'card': 'クースカン',
                           'stage': 'list', 'source': 'chart_tactic', 'ally_hp': 11,
                           'enemy_hp': 50},
                          {'seq': 2, 'tick': 41, 'event': 'gate_evaluation',
                           'card': 'クースカン', 'stage': 'list', 'allowed': True,
                           'role': 'critical_card_spend', 'ally_hp': 11, 'enemy_hp': 50}],
                      'card_gate_history': {
                          'クースカン': {'card': 'クースカン', 'allowed': True,
                                         'role': 'critical_card_spend', 'source': 'evaluation',
                                         'ally_hp': 11, 'enemy_hp': 50, 'tick': 41, 'seq': 2}}}}
    battle_flow.battle_end(mem, 'map')
    battle_flow.battle_end(mem, 'map')
    row = next(record for record in mem['_records']
               if record['decision'] == 'battle_boss_card_missed')
    metric = row['observed_metric']
    assert metric['outcome'] == 'loss'
    assert metric['ally_hp_at_open'] == 11
    assert metric['enemy_hp_at_open'] == 50
    assert metric['ally_hp_at_end'] == 0
    assert metric['enemy_hp_at_end'] == 30
    assert metric['critical_survival']['needed'] is True
    assert metric['critical_survival']['behind'] is True
    assert metric['enemy_summon_observed'] is True
    assert metric['carried_cards'][0]['card'] == 'クースカン'
    assert 'planned_cards' in metric['carried_cards'][0]['provenance']
    assert metric['damage_gate']['allowed'] is True
    assert metric['damage_gate']['role'] == 'critical_card_spend'
    assert [entry['event'] for entry in metric['card_flow_history']] == [
        'open', 'gate_evaluation']
