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
    cur = _battle(enemy='エシャロット')
    assert battle_flow._boss_card_left_unused({}, cur) is None


def test_the_miss_is_noted_once():
    cur = _battle(boss_card_missed_noted=True)
    assert battle_flow._boss_card_left_unused({}, cur) is None


def test_no_carried_card_is_not_a_miss():
    cur = _battle(planned_cards=[])
    assert battle_flow._boss_card_left_unused({}, cur) is None
