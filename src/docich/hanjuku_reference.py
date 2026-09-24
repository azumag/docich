"""Gameplay reference tables from gamecentergx / original charts (gcgx).

Static facts only: card damage tables, egg-drop rule, AI patterns, half-raw
level thresholds and event tables used by tests, commentary and future
independent judgment. No network, no model calls. Sources:

- https://gcgx.games/hanjuku/ (cards, bosses, castles, events, levels)
- games/hanjuku-sfc-speedrun/data/egg-drop-table.md (local transcription)
"""
from __future__ import annotations

# 切り札 basic stats (general damage / egg drop / price where measured).
# IDs used by enemy-egg summon detection (sum of card IDs >= 48).
CARDS: dict[str, dict] = {
    'イッテツーン': {'general_damage': 10, 'price': 1},
    'ダイチスイム': {'general_damage': 16, 'price': None},
    'ブラッキー': {'general_damage': 18, 'price': None, 'egg_drop': True},
    'フットバース': {'general_damage': 12, 'price': None},
    'グリンボー': {'general_damage': 6, 'price': 6},
    'ピッグローラー': {'general_damage': None, 'price': None},
    'カンケリン': {'general_damage': None, 'price': None},
    'ノリウツール': {'general_damage': 53, 'price': 18},
    'クースカン': {'general_damage': 45, 'price': 24, 'egg_drop': True},
    'ゼンマイン': {'general_damage': 50, 'price': 32},
    'ミックミー': {'general_damage': None, 'price': 40},
    'デッドガン': {'general_damage': None, 'price': None},
    'ブレイコウ': {'general_damage': None, 'price': None},
    'ブンシーン': {'general_damage': None, 'price': None},
    'ファイアーボイス': {'general_damage': None, 'price': None},
    'ファバード': {'general_damage': 100, 'price': 40, 'egg_drop': True},
    'エンジェリン': {'general_damage': None, 'price': 32},
    'マグネガキン': {'general_damage': 80, 'price': 34},
    'ハリケーン': {'general_damage': None, 'price': 38},
}

CARD_IDS: dict[str, int] = {
    'イッテツーン': 1, 'ダイチスイム': 2, 'ブラッキー': 3, 'フットバース': 4,
    'グリンボー': 5, 'ピッグローラー': 6, 'カンケリン': 7, 'ノリウツール': 8,
    'クースカン': 9, 'ゼンマイン': 10, 'ミックミー': 11, 'デッドガン': 12,
    'ブレイコウ': 13, 'ブンシーン': 14, 'ファイアーボイス': 15, 'ファバード': 16,
    'エンジェリン': 17, 'マグネガキン': 18, 'ハリケーン': 19,
}

# Egg-drop formula: 卵落 > (敵・味方将軍の最大HP合計 mod 16).
EGG_DROP_MOD = 16

# Enemy egg判定: summoned card IDs sum >= threshold, plus AI pattern 0-3.
ENEMY_EGG_CARD_ID_SUM = 48
AI_PATTERNS = (0, 1, 2, 3)

# 半熟レベル needed values (gcgx level table; index = level).
HALF_RAW_LEVEL_NEED = {
    1: 0, 2: 100, 3: 200, 4: 350, 5: 550, 6: 800, 7: 1100, 8: 1500, 9: 2000,
}

# Original six melee/strategy patterns mapped onto bot decisions.
# ①③: continue melee; ②④⑤: chart-directed cards; ⑥: own egg when behind.
MELEE_PATTERNS = {
    '①': {'action': 'pass', 'note': '白兵を続ける（劣勢でない）'},
    '②': {'action': 'chart_card', 'note': 'チャート指示の切り札'},
    '③': {'action': 'pass_clashed', 'note': 'ぶつかり合い後も白兵'},
    '④': {'action': 'chart_card', 'note': 'チャート指示の切り札'},
    '⑤': {'action': 'chart_card', 'note': 'チャート指示の切り札'},
    '⑥': {'action': 'use_egg', 'note': '劣勢/召喚時にたまご'},
}

# Monthly / cave / hot-spring event tables (gcgx event page; labels only).
EVENT_TABLES = {
    'monthly': ('凶作', '豊作', '大豊作', '美女来訪', 'おねだり', '時報'),
    'cave': ('洞窟商人', '将軍遭遇'),
    'spring': ('温泉効果',),
}

# Boss HP samples from the original charts (not exhaustive).
BOSS_HP = {
    'クイーン': None,          # ch1 panel-measured in live runs
    'にせヒーロー': 90,
    'プリンス': 100,
    'だいまおう': 1218,
    'ハードマン': 500,
    'ハードロボ': 1688,
}

#食いしばり (endure): lethal card damage clamps to HP-1 when
# damage <= current_hp + 16.
ENDURE_HEADROOM = 16


def endure_safe_kill_hp(card_damage: int) -> int:
    """Max enemy HP where ``card_damage`` still kills through 食いしばり."""
    # Need damage > hp + 16, i.e. hp <= damage - 17? Chart: ファバード100 →
    # kill at HP83 or less (84+ survives). 100 - 17 = 83. Yes.
    return card_damage - (ENDURE_HEADROOM + 1)


def enemy_egg_likely(card_ids: list[int]) -> bool:
    """True when a summon's card ID sum meets the gcgx enemy-egg rule."""
    return sum(card_ids) >= ENEMY_EGG_CARD_ID_SUM


def egg_drop_threshold(max_hp_sum: int) -> int:
    """Minimum 卵落 value required to drop an egg given combined max HPs."""
    return max_hp_sum % EGG_DROP_MOD + 1
