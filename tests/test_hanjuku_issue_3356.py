"""Regression tests for issue #3356.

半熟英雄: 兵士が残って戦闘力優位でもHP4分の1条項で退却する (g1133 ヴィーナス 20 vs 24)。

quarter-HP 退却は、両側の兵士数がパネルで読めている場合に限り、
合戦力スコア (HP + 10×兵士) × 戦闘力 が 1.3 倍以上なら抑制する。
対象は quarter-HP 条項のみで、hero / 召喚 / ボス / 資源確認の安全経路は変更しない。
"""
from copy import deepcopy

from docich.hanjuku.judgment import battle as rules


def g1133(**changes):
    """g1133: ヴィーナス HP 20/82 vs ココット HP 24、兵士 6 vs 6、戦闘力 12 vs 8。

    合戦力スコアは (20 + 60) x 12 = 960 vs (24 + 60) x 8 = 672 で 1.43x と明確優勢。
    従来は hp * 4 <= start_ally_hp (80 <= 82) の quarter-HP 条項だけで
    「たいきゃく」を選択していた。
    """
    cur = {
        'ally': 'ヴィーナス', 'enemy': 'ココット', 'side': 'attack',
        'ally_hp': 20, 'enemy_hp': 24, 'start_ally_hp': 82, 'start_enemy_hp': 24,
        'ally_soldiers': 6, 'enemy_soldiers': 6,
        'card_soldiers_current': True, 'planned_cards': [],
        'survival': {'exhausted': False, 'cards_uncertain': False},
    }
    cur.update(changes)
    return cur


def test_melee_score_matches_issue_g1133():
    cur = g1133()
    balance = rules._melee_balance(cur)
    assert balance['soldiers_current'] is True
    assert (balance['ally_melee_score'], balance['enemy_melee_score']) == (960, 672)


def test_quarter_hp_retreat_suppressed_by_combat_advantage_g1133():
    cur = g1133()
    assert rules._quarter_retreat_combat_blocked(cur) is True
    # hp * 4 <= start (80 <= 82) の quarter-HP 条項が成立するが、合戦力優位で抑制
    assert rules._default_retreat_needed(deepcopy(cur)) is False


def test_quarter_hp_retreat_not_suppressed_below_1_3():
    cur = g1133(ally_soldiers=0, enemy_soldiers=0)  # 240 vs 192 = 1.25x
    assert rules._quarter_retreat_combat_blocked(cur) is False
    assert rules._default_retreat_needed(deepcopy(cur)) is True


def test_quarter_hp_retreat_suppressed_at_exactly_1_3():
    # 比率がちょうど 1.3 (156 vs 120) でも「1.3倍以上」で抑制
    cur = g1133(ally_hp=13, enemy_hp=15, ally_soldiers=0, enemy_soldiers=0,
                start_ally_hp=82, start_enemy_hp=24)
    balance = rules._melee_balance(cur)
    assert (balance['ally_melee_score'], balance['enemy_melee_score']) == (156, 120)
    assert rules._quarter_retreat_combat_blocked(cur) is True
    assert rules._default_retreat_needed(deepcopy(cur)) is False


def test_unread_soldiers_do_not_suppress_quarter_hp_retreat():
    cur = g1133(card_soldiers_current=False)
    assert rules._quarter_retreat_combat_blocked(cur) is False
    assert rules._default_retreat_needed(deepcopy(cur)) is True


def test_hero_quarter_hp_retreat_is_not_suppressed():
    # ご主人様は combat_blocked から除外 -> quarter-HP 退却は抑制されない (安全経路不変)
    cur = g1133(ally='どうし', ally_hp=20, enemy_hp=24, start_ally_hp=82,
                start_enemy_hp=24, ally_soldiers=6, enemy_soldiers=6)
    assert rules._quarter_retreat_combat_blocked(cur) is False
    assert rules._default_retreat_needed(deepcopy(cur)) is True


def test_summon_quarter_hp_retreat_is_not_suppressed():
    # 召喚パスは quarter-HP 条項に到達せず変更しない
    cur = g1133(egg_battle=True, egg_retreat_needed=True)
    assert rules._default_retreat_needed(deepcopy(cur)) is True


def test_boss_quarter_hp_retreat_is_not_suppressed():
    # ボスは抑制しない (安全経路不変)
    cur = g1133(enemy='クイーン')
    assert rules._quarter_retreat_combat_blocked(cur) is False
    assert rules._default_retreat_needed(deepcopy(cur)) is True
