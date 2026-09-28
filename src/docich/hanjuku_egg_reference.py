"""Static enemy egg facts; no network, ROM access or live state inference.

Roster: games/hanjuku-sfc-speedrun/data/char.csv at
5e982942ec24fb559f58b29250560fd784e5ae5c, SFC IDs 0..127 only.
Egg presence and already-separated human-opponent thought type were checked
against https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggGeneral.html
on 2026-09-27. The malformed/remake-only rows above ID 127 are not imported.
Triggers and player-castle override:
https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggHero.html#GS_EggAI
See also the local README section 敵将軍の卵使用判定.
"""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class EggTriggers:
    # None is unknown, never permission to mash. These are capabilities, not
    # observations of an egg being available/used in this particular fight.
    has_egg: bool | None = None
    opening_card_sum: bool | None = None
    clash_position: bool | None = None
    wall_critical: bool | None = None
    wall_mod4: bool | None = None


# A=opening, B=wall critical, C=clash, D=wall mod4.
# The char.csv 思考 column is the human-opponent type, NOT the raw nibble.
THOUGHT_TRIGGERS = {
    0: EggTriggers(True, False, False, False, False),
    1: EggTriggers(True, True, True, True, False),
    2: EggTriggers(True, True, False, True, True),
    3: EggTriggers(True, True, True, True, True),
}

# name: (has egg, human-opponent thought type). Keep IDs for source auditing.
GENERAL_EGGS: dict[str, tuple[bool, int]] = {
    'しゅじんこう': (True, 2),  # 0
    'キャラウェイ': (False, 2),  # 1
    'クミン': (True, 3),  # 2
    'コリアンダー': (False, 2),  # 3
    'バジル': (False, 2),  # 4
    'ミント': (False, 2),  # 5
    'パプリカ': (False, 2),  # 6
    'シナモン': (False, 2),  # 7
    'ヘーゼル': (True, 3),  # 8
    'ガルバンゾー': (True, 3),  # 9
    'ラズベリー': (False, 3),  # 10
    'ピスタチオ': (False, 2),  # 11
    'マカデミア': (False, 2),  # 12
    'カシュー': (True, 3),  # 13
    'クランベリー': (False, 2),  # 14
    'ガスパチョ': (True, 3),  # 15
    'ビシソワーズ': (True, 3),  # 16
    'タピオカ': (False, 2),  # 17
    'キッシュ': (True, 2),  # 18
    'ロックフォール': (False, 3),  # 19
    'シェーブル': (True, 3),  # 20
    'アマンディーヌ': (False, 2),  # 21
    'チコリ': (True, 3),  # 22
    'ビーツ': (False, 3),  # 23
    'セルリアク': (False, 3),  # 24
    'アンディーブ': (False, 1),  # 25
    'リーキ': (True, 1),  # 26
    'トレビス': (False, 3),  # 27
    'アルファルファ': (True, 2),  # 28
    'タルタル': (False, 1),  # 29
    'ヘルメス': (False, 1),  # 30
    'リースリング': (False, 3),  # 31
    'デュオニソス': (True, 1),  # 32
    'アルテミス': (True, 3),  # 33
    'キャンディー': (True, 3),  # 34
    'シャルドネ': (False, 3),  # 35
    'ソーピニヨン': (True, 1),  # 36
    'ピオーネ': (True, 3),  # 37
    'ヘラ': (False, 1),  # 38
    'セミヨン': (True, 3),  # 39
    'ポワソン': (False, 1),  # 40
    'デーメーテール': (False, 1),  # 41
    'ユイートル': (False, 1),  # 42
    'ヘパイストス': (True, 3),  # 43
    'エシャロット': (True, 3),  # 44
    'ミュスカデ': (False, 1),  # 45
    'アテナ': (True, 1),  # 46
    'エピィ': (True, 1),  # 47
    'ポセイドン': (False, 1),  # 48
    'ペコリーノ': (False, 1),  # 49
    'アポロン': (True, 1),  # 50
    'ジェラート': (True, 3),  # 51
    'ミルフィーユ': (False, 2),  # 52
    'ヘスティア': (True, 2),  # 53
    'キール': (False, 2),  # 54
    'ライム': (False, 2),  # 55
    'レモン': (True, 2),  # 56
    'フェットチーネ': (True, 2),  # 57
    'バーミセリ': (False, 2),  # 58
    'カペリーニ': (False, 2),  # 59
    'ブカティーニ': (True, 2),  # 60
    'ナストリーニ': (False, 2),  # 61
    'ラビオリ': (False, 2),  # 62
    'フェデリーニ': (False, 2),  # 63
    'カシス': (True, 2),  # 64
    'グレナデン': (False, 2),  # 65
    'チキータ': (True, 2),  # 66
    'ランプータン': (True, 2),  # 67
    'ドリアン': (False, 2),  # 68
    'マスカット': (True, 2),  # 69
    'マンゴスチン': (False, 3),  # 70
    'バタール': (False, 2),  # 71
    'バゲット': (True, 2),  # 72
    'ブリオッシュ': (True, 3),  # 73
    'バトウラ': (False, 2),  # 74
    'ブレッツェル': (True, 2),  # 75
    'マフィン': (True, 2),  # 76
    'ベーグル': (False, 2),  # 77
    'ミモザ': (True, 0),  # 78
    'ショコラ': (True, 0),  # 79
    'プラリネ': (False, 0),  # 80
    'ブラマンジェ': (False, 0),  # 81
    'シフォン': (True, 0),  # 82
    'シュゼット': (False, 2),  # 83
    'ブラウニー': (True, 2),  # 84
    'キャロット': (True, 3),  # 85
    'マサラ': (False, 2),  # 86
    'バラクーダ': (False, 2),  # 87
    'コンポート': (True, 3),  # 88
    'ギー': (False, 2),  # 89
    'グリッシーニ': (True, 2),  # 90
    'シュガー': (True, 2),  # 91
    'オレガノ': (True, 1),  # 92
    'ジキタリス': (False, 3),  # 93
    'ローズマリー': (False, 1),  # 94
    'マーマレード': (False, 1),  # 95
    'サフラン': (True, 1),  # 96
    'アニス': (False, 1),  # 97
    'エストラゴン': (True, 1),  # 98
    'エッジ': (False, 2),  # 99
    'リディア': (True, 2),  # 100
    'ガーラント': (False, 2),  # 101
    'カイン': (True, 2),  # 102
    'グレイ': (True, 2),  # 103
    'レオンハルト': (True, 2),  # 104
    'フリオニール': (True, 2),  # 105
    'サムソー': (False, 2),  # 106
    'ハバティー': (True, 2),  # 107
    'マリボー': (True, 0),  # 108
    'ラクレット': (True, 2),  # 109
    'エダム': (True, 2),  # 110
    'リゴット': (False, 2),  # 111
    'ブリー': (True, 0),  # 112
    'マルガリータ': (True, 2),  # 113
    'ジン': (False, 2),  # 114
    'マラスキーノ': (True, 2),  # 115
    'アクアビット': (True, 2),  # 116
    'ラ ターシュ': (True, 2),  # 117
    'バランタイン': (True, 2),  # 118
    'カミュ': (True, 2),  # 119
    'クイーン': (True, 3),  # 120
    'プリンス': (True, 3),  # 121
    'にせヒーロー': (True, 3),  # 122
    'せいめいたい': (True, 3),  # 123
    'だいじん': (False, 2),  # 124
    'ココット': (True, 3),  # 125
    'ヴィーナス': (True, 2),  # 126
    'ゼウス': (True, 2),  # 127
}


# Fixed max HP per general (SFC IDs 0..127), from char.csv at
# 5e982942ec24fb559f58b29250560fd784e5ae5c. The 卵落 rule needs max HP,
# not a wounded battle reading; unknown names stay None (fail-closed).
GENERAL_HP: dict[str, int] = {
    'しゅじんこう': 90,  # 0
    'キャラウェイ': 40,  # 1
    'クミン': 27,  # 2
    'コリアンダー': 38,  # 3
    'バジル': 40,  # 4
    'ミント': 32,  # 5
    'パプリカ': 34,  # 6
    'シナモン': 39,  # 7
    'ヘーゼル': 33,  # 8
    'ガルバンゾー': 30,  # 9
    'ラズベリー': 45,  # 10
    'ピスタチオ': 49,  # 11
    'マカデミア': 34,  # 12
    'カシュー': 39,  # 13
    'クランベリー': 51,  # 14
    'ガスパチョ': 37,  # 15
    'ビシソワーズ': 27,  # 16
    'タピオカ': 50,  # 17
    'キッシュ': 26,  # 18
    'ロックフォール': 57,  # 19
    'シェーブル': 27,  # 20
    'アマンディーヌ': 59,  # 21
    'チコリ': 18,  # 22
    'ビーツ': 29,  # 23
    'セルリアク': 32,  # 24
    'アンディーブ': 36,  # 25
    'リーキ': 30,  # 26
    'トレビス': 66,  # 27
    'アルファルファ': 38,  # 28
    'タルタル': 40,  # 29
    'ヘルメス': 63,  # 30
    'リースリング': 37,  # 31
    'デュオニソス': 54,  # 32
    'アルテミス': 49,  # 33
    'キャンディー': 26,  # 34
    'シャルドネ': 56,  # 35
    'ソーピニヨン': 48,  # 36
    'ピオーネ': 46,  # 37
    'ヘラ': 59,  # 38
    'セミヨン': 36,  # 39
    'ポワソン': 80,  # 40
    'デーメーテール': 49,  # 41
    'ユイートル': 57,  # 42
    'ヘパイストス': 49,  # 43
    'エシャロット': 46,  # 44
    'ミュスカデ': 57,  # 45
    'アテナ': 66,  # 46
    'エピィ': 57,  # 47
    'ポセイドン': 85,  # 48
    'ペコリーノ': 88,  # 49
    'アポロン': 74,  # 50
    'ジェラート': 49,  # 51
    'ミルフィーユ': 39,  # 52
    'ヘスティア': 25,  # 53
    'キール': 65,  # 54
    'ライム': 69,  # 55
    'レモン': 27,  # 56
    'フェットチーネ': 67,  # 57
    'バーミセリ': 89,  # 58
    'カペリーニ': 63,  # 59
    'ブカティーニ': 42,  # 60
    'ナストリーニ': 66,  # 61
    'ラビオリ': 87,  # 62
    'フェデリーニ': 8,  # 63
    'カシス': 73,  # 64
    'グレナデン': 81,  # 65
    'チキータ': 74,  # 66
    'ランプータン': 27,  # 67
    'ドリアン': 51,  # 68
    'マスカット': 27,  # 69
    'マンゴスチン': 85,  # 70
    'バタール': 99,  # 71
    'バゲット': 37,  # 72
    'ブリオッシュ': 60,  # 73
    'バトウラ': 55,  # 74
    'ブレッツェル': 71,  # 75
    'マフィン': 15,  # 76
    'ベーグル': 91,  # 77
    'ミモザ': 66,  # 78
    'ショコラ': 56,  # 79
    'プラリネ': 87,  # 80
    'ブラマンジェ': 81,  # 81
    'シフォン': 62,  # 82
    'シュゼット': 90,  # 83
    'ブラウニー': 5,  # 84
    'キャロット': 60,  # 85
    'マサラ': 77,  # 86
    'バラクーダ': 76,  # 87
    'コンポート': 55,  # 88
    'ギー': 71,  # 89
    'グリッシーニ': 74,  # 90
    'シュガー': 49,  # 91
    'オレガノ': 65,  # 92
    'ジキタリス': 26,  # 93
    'ローズマリー': 98,  # 94
    'マーマレード': 74,  # 95
    'サフラン': 64,  # 96
    'アニス': 87,  # 97
    'エストラゴン': 66,  # 98
    'エッジ': 99,  # 99
    'リディア': 72,  # 100
    'ガーラント': 97,  # 101
    'カイン': 74,  # 102
    'グレイ': 73,  # 103
    'レオンハルト': 71,  # 104
    'フリオニール': 67,  # 105
    'サムソー': 98,  # 106
    'ハバティー': 54,  # 107
    'マリボー': 66,  # 108
    'ラクレット': 36,  # 109
    'エダム': 48,  # 110
    'リゴット': 99,  # 111
    'ブリー': 29,  # 112
    'マルガリータ': 72,  # 113
    'ジン': 97,  # 114
    'マラスキーノ': 74,  # 115
    'アクアビット': 69,  # 116
    'ラ ターシュ': 69,  # 117
    'バランタイン': 70,  # 118
    'カミュ': 73,  # 119
    'クイーン': 70,  # 120
    'プリンス': 100,  # 121
    'にせヒーロー': 90,  # 122
    'せいめいたい': 500,  # 123
    'だいじん': 90,  # 124
    'ココット': 24,  # 125
    'ヴィーナス': 82,  # 126
    'ゼウス': 85,  # 127
}


def general_max_hp(name: str | None) -> int | None:
    """The general's fixed max HP, or None when the name is not in the
    SFC roster (the egg-drop comparison is then not made)."""
    return GENERAL_HP.get(name)


def enemy_egg_triggers(enemy: str | None, *,
                       player_castle_defense: bool | None = False) -> EggTriggers:
    """Resolve base facts and the measured defense exception.

    False means a known ordinary fight; None means context was not observed.
    In the latter case retain only triggers shared by both possibilities.
    Unlisted names stay unknown. A known eggless enemy stays eggless even
    during defense; the override changes thought, not inventory.
    """
    entry = GENERAL_EGGS.get(enemy)
    if entry is None:
        return EggTriggers()
    has_egg, thought = entry
    if not has_egg:
        return EggTriggers(False, False, False, False, False)
    base = THOUGHT_TRIGGERS[thought]
    defense = THOUGHT_TRIGGERS[1]
    if player_castle_defense is True:
        return defense
    if player_castle_defense is None:
        return replace(base, **{
            field: getattr(base, field) if getattr(base, field) == getattr(defense, field) else None
            for field in ('opening_card_sum', 'clash_position', 'wall_critical', 'wall_mod4')
        })
    return base
