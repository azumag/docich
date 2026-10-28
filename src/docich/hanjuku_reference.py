"""Gameplay reference tables from gamecentergx / original charts (gcgx).

Static facts only: card damage tables, egg-drop rule, AI patterns, half-raw
level thresholds, event tables and summoned-monster skill names used by
tests, commentary and independent judgment. No network, no model calls.
Sources:

- https://gcgx.games/hanjuku/ (cards, bosses, castles, events, levels)
- games/hanjuku-sfc-speedrun/data/egg-drop-table.md (local transcription)
- https://wikiwiki.jp/hjksfc/エッグモンスター/攻撃データ (skill names, effects;
  transcribed once, matched by folded kana because the tile reader can drop a
  dakuten mark)
"""
from __future__ import annotations

from docich.hanjuku_egg_reference import general_max_hp, general_debut_chapter

# 切り札 basic stats: the whole gcgx kirihuda.html table (decimal ID 0..31).
# Owner 2026-10-03: 「全ての将軍と切り札のデータをちゃんと内部でデータとして持って」
# → 全32札を保持し、gcgx kirihuda.html と https://wikiwiki.jp/hjksfc/切り札
# （両出典は全列一致を確認）に統一する。旧 general_damage の未使用10件は正典値へ置換。
#   general_damage / monster_damage / boss_damage / soldier_damage
#       = 将軍・エッグモンスター・ボスへのダメージ、自軍兵士1人あたりのダメージ増加量
#   egg_drop = 卵落。`卵落 > 敵・味方将軍の最大HP合計 mod 16` で敵が卵を落とし、
#              以後その敵は召喚を使えない (egg_drop_threshold / can_drop_egg)
#   price     = 既存の実測価格はそのまま。未収録分は gcgx「入手場所(価格)」の先頭の
#              購入価格。None は G での購入先が無い（イベント・宝箱のみ）
#   effect    = gcgx「効果」列（空文字は効果なし）
# 敵卵の開幕判定に使う ID は CARD_IDS / ALL_CARD_IDS (合計 >= 48)。
CARDS: dict[str, dict] = {
    'イッテツーン': {'id': 0, 'general_damage': 10, 'monster_damage': 32, 'boss_damage': 16,
                     'soldier_damage': 0, 'egg_drop': 8, 'price': 1, 'effect': ''},
    'ダイチスイム': {'id': 1, 'general_damage': 6, 'monster_damage': 22, 'boss_damage': 8,
                     'soldier_damage': 3, 'egg_drop': 1, 'price': None, 'effect': ''},
    'ブラッキー': {'id': 2, 'general_damage': 22, 'monster_damage': 50, 'boss_damage': 20,
                   'soldier_damage': 0, 'egg_drop': 3, 'price': None, 'effect': ''},
    'フットバース': {'id': 3, 'general_damage': 6, 'monster_damage': 48, 'boss_damage': 2,
                     'soldier_damage': 3, 'egg_drop': 5, 'price': None,
                     'effect': 'エグモン、ボスの防御力半減'},
    'ダンスライン': {'id': 4, 'general_damage': 16, 'monster_damage': 5, 'boss_damage': 1,
                     'soldier_damage': 3, 'egg_drop': 1, 'price': 5, 'effect': ''},
    'グリンボー': {'id': 5, 'general_damage': 32, 'monster_damage': 46, 'boss_damage': 18,
                   'soldier_damage': 1, 'egg_drop': 4, 'price': 6, 'effect': ''},
    'カルゲンジー': {'id': 6, 'general_damage': 12, 'monster_damage': 6, 'boss_damage': 1,
                     'soldier_damage': 5, 'egg_drop': 1, 'price': 8,
                     'effect': 'エグモン、ボスの攻撃力半減'},
    'ピッグローラー': {'id': 7, 'general_damage': 5, 'monster_damage': 38, 'boss_damage': 4,
                       'soldier_damage': 5, 'egg_drop': 2, 'price': None, 'effect': ''},
    'ラピニアール': {'id': 8, 'general_damage': 20, 'monster_damage': 48, 'boss_damage': 16,
                     'soldier_damage': 3, 'egg_drop': 1, 'price': 12,
                     'effect': 'エグモン、ボスの攻撃力半減'},
    'カンケリン': {'id': 9, 'general_damage': 10, 'monster_damage': 50, 'boss_damage': 24,
                   'soldier_damage': 1, 'egg_drop': 1, 'price': None, 'effect': ''},
    'デッドガン': {'id': 10, 'general_damage': 0, 'monster_damage': 0, 'boss_damage': 0,
                   'soldier_damage': 0, 'egg_drop': 0, 'price': None,
                   'effect': '両軍全滅。城内戦で使用すると城Lvが1になる'},
    'ノリウツール': {'id': 11, 'general_damage': 0, 'monster_damage': 8, 'boss_damage': 8,
                     'soldier_damage': 0, 'egg_drop': 0, 'price': 18,
                     'effect': '使用者の現在HPの半分ダメージ。めをまわしてる！の追加効果'},
    'ブレイコウ': {'id': 12, 'general_damage': 0, 'monster_damage': 0, 'boss_damage': 0,
                   'soldier_damage': 0, 'egg_drop': 0, 'price': None,
                   'effect': 'てれている！！の追加効果'},
    'クースカン': {'id': 13, 'general_damage': 0, 'monster_damage': 56, 'boss_damage': 20,
                   'soldier_damage': 1, 'egg_drop': 0, 'price': 24,
                   'effect': '敵軍の将軍、兵士のHP半減。きぜつしてる！！の追加効果'},
    'ブンシーン': {'id': 14, 'general_damage': 7, 'monster_damage': 52, 'boss_damage': 32,
                   'soldier_damage': 7, 'egg_drop': 3, 'price': None, 'effect': ''},
    'ゼンマイン': {'id': 15, 'general_damage': 32, 'monster_damage': 96, 'boss_damage': 50,
                   'soldier_damage': 0, 'egg_drop': 3, 'price': 32, 'effect': ''},
    'バルムンク': {'id': 16, 'general_damage': 16, 'monster_damage': 255, 'boss_damage': 250,
                   'soldier_damage': 0, 'egg_drop': 5, 'price': None,
                   'effect': 'エッグモンスター即死'},
    'ミックミー': {'id': 17, 'general_damage': 64, 'monster_damage': 7, 'boss_damage': 32,
                   'soldier_damage': 0, 'egg_drop': 2, 'price': 40, 'effect': ''},
    'ファイアーボイス': {'id': 18, 'general_damage': 16, 'monster_damage': 16, 'boss_damage': 7,
                         'soldier_damage': 0, 'egg_drop': 4, 'price': None,
                         'effect': '敵兵士全滅'},
    'グルミー': {'id': 19, 'general_damage': 68, 'monster_damage': 96, 'boss_damage': 100,
                 'soldier_damage': 4, 'egg_drop': 15, 'price': None,
                 'effect': 'エグモン、ボスの攻撃力半減'},
    'ブラックホール': {'id': 20, 'general_damage': 0, 'monster_damage': 0, 'boss_damage': 1,
                       'soldier_damage': 0, 'egg_drop': 0, 'price': 52,
                       'effect': '両軍の兵士全滅'},
    'シュプレボイス': {'id': 21, 'general_damage': 0, 'monster_damage': 0, 'boss_damage': 0,
                       'soldier_damage': 0, 'egg_drop': 0, 'price': None,
                       'effect': '敵を退却させる'},
    'エンジェリン': {'id': 22, 'general_damage': 0, 'monster_damage': 0, 'boss_damage': 0,
                     'soldier_damage': 0, 'egg_drop': 0, 'price': 32,
                     'effect': '将軍のHP全回復、エッグの残り使用回数を5回にする、兵士の回復・復活'},
    'ころぼぐんだん': {'id': 23, 'general_damage': 1, 'monster_damage': 1, 'boss_damage': 1,
                       'soldier_damage': 1, 'egg_drop': 12, 'price': 5, 'effect': ''},
    'バグストーム': {'id': 24, 'general_damage': 0, 'monster_damage': 100, 'boss_damage': 50,
                     'soldier_damage': 0, 'egg_drop': 0, 'price': None,
                     'effect': '両軍の将軍、兵士のHP半減'},
    'リューキーシ': {'id': 25, 'general_damage': 24, 'monster_damage': 96, 'boss_damage': 60,
                     'soldier_damage': 12, 'egg_drop': 4, 'price': 68, 'effect': ''},
    'ドデカヘー': {'id': 26, 'general_damage': 18, 'monster_damage': 18, 'boss_damage': 70,
                   'soldier_damage': 18, 'egg_drop': 2, 'price': 72,
                   'effect': 'エグモン、ボスの防御力半減'},
    'マグネガキン': {'id': 27, 'general_damage': 48, 'monster_damage': 48, 'boss_damage': 80,
                     'soldier_damage': 4, 'egg_drop': 8, 'price': 34,
                     'effect': 'エグモン、ボスの攻撃力半減'},
    'キャトルミュー': {'id': 28, 'general_damage': 224, 'monster_damage': 224, 'boss_damage': 90,
                       'soldier_damage': 0, 'egg_drop': 0, 'price': None,
                       'effect': 'せきかしてる！！の追加効果'},
    'ビッグウェイブ': {'id': 29, 'general_damage': 64, 'monster_damage': 32, 'boss_damage': 100,
                       'soldier_damage': 14, 'egg_drop': 0, 'price': 88, 'effect': ''},
    'ハリケーン': {'id': 30, 'general_damage': 10, 'monster_damage': 240, 'boss_damage': 100,
                   'soldier_damage': 10, 'egg_drop': 0, 'price': 38,
                   'effect': 'めをまわしてる！の追加効果'},
    'ファバード': {'id': 31, 'general_damage': 240, 'monster_damage': 240, 'boss_damage': 100,
                   'soldier_damage': 20, 'egg_drop': 0, 'price': 40,
                   'effect': 'もえている！！の追加効果。自軍の将軍、兵士のHP半減'},
}

# 卵落 values: CARDS が唯一の出典 (owner 2026-10-03: 全32札を保持)。既存19件の値は
# gcgx card table (fetched 2026-09-29) と同一のまま。A card drops the enemy egg
# when its 卵落 is strictly greater than the two generals' max-HP sum mod 16
# (see egg_drop_threshold / can_drop_egg).
EGG_DROP_VALUES: dict[str, int] = {name: card['egg_drop'] for name, card in CARDS.items()}

# Actual zero-based No., not price or this module's supported-card order.
# Local README examples + https://wikiwiki.jp/hjksfc/切り札 (No. column).
CARD_IDS: dict[str, int] = {
    'イッテツーン': 0, 'ダイチスイム': 1, 'ブラッキー': 2, 'フットバース': 3,
    'グリンボー': 5, 'ピッグローラー': 7, 'カンケリン': 9, 'ノリウツール': 11,
    'クースカン': 13, 'ゼンマイン': 15, 'ミックミー': 17, 'デッドガン': 10,
    'ブレイコウ': 12, 'ブンシーン': 14, 'ファイアーボイス': 18, 'ファバード': 31,
    'エンジェリン': 22, 'マグネガキン': 27, 'ハリケーン': 30,
}
# The whole gcgx kirihuda.html decimal ID table (0-31), for ID sums.
ALL_CARD_IDS: dict[str, int] = {**CARD_IDS,
    'ダンスライン': 4, 'カルゲンジー': 6, 'ラピニアール': 8, 'バルムンク': 16, 'グルミー': 19,
    'ブラックホール': 20, 'シュプレボイス': 21, 'ころぼぐんだん': 23, 'バグストーム': 24,
    'リューキーシ': 25, 'ドデカヘー': 26, 'キャトルミュー': 28, 'ビッグウェイブ': 29,
}

# Owner advice (2026-09-29, gcgx ai.html): the enemy uses its egg when the
# battle's card IDs total 48 or more, so a sortie carries 47 or less, e.g.
# クースカン+ミックミー×2 (47: クースカン then ミックミー wipes a general of
# HP<=69 with his soldiers), クースカン+ビッグウェイブ+イッテツーン (42),
# イッテツーン+グリンボー+ころぼぐんだん (28: cheap, ころぼぐんだん drops eggs
# often), エンジェリン×2+イッテツーン (44: エンジェリン fully heals).
RECOMMENDED_CARD_SETS = (
    ('クースカン', 'ミックミー', 'ミックミー'),
    ('クースカン', 'ビッグウェイブ', 'イッテツーン'),
    ('イッテツーン', 'グリンボー', 'ころぼぐんだん'),
    ('エンジェリン', 'エンジェリン', 'イッテツーン'),
)

# 強い切り札 (owner 2026-10-03: 「強い切り札を偶然手に入れている時などは、
# 強い将軍とたたかうときに積極的に利用するようにして下さい」)。
# gcgx kirihuda.html (fetched 2026-10-03) の将軍戦ダメージと効果から、強い将軍
# を倒せる札だけを携行・開幕使用の対象にする。将軍戦48以上、または将軍のHPを
# 半減させる効果を持つ札のみ: ミックミー64 / マグネガキン48 / クースカン(敵将軍・
# 兵士のHP半減) / ノリウツール(使用者の現在HP半分をダメージ)。
# 並びは使用優先順。ファバードは自軍も半減させる犠牲札のため対象外(救済の
# SURVIVAL_CARDS も含まない)、キャトルミューはレアイベント札の専用経路、
# エンジェリンは救済優先、デッドガン等の全滅/退却札は対象外。
STRONG_CARDS = ('クースカン', 'ミックミー', 'マグネガキン', 'ノリウツール')

# Castle level (wikiwiki.jp/hjksfc/城, 2026-09-29): the defender's egg monster
# gains +level defense and speed (also egg vs general), a defending general's
# charge speed +level, garrison capacity is level-1 (over it the AI sorties),
# and a defender loses one level per general killed. No bonus at boss castles.
CASTLE_LEVEL_DEFENSE_BONUS = True

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

# gcgx shogun.html 将軍一覧 (fetched 2026-10-03; SFC ID 0..127 の128名)。
# owner 2026-10-03: 「ここを参考に、この評をハードコードしてしまって、値を照らし
# 合わせよ。まずは戦闘の値とHPでわかる。補助的に卵補正、たまごのしゅるい、卵仕様
# の思考パターンなどが参考になる」。
# 値は (HP, 戦闘, 半熟レベル補正, エッグ種別, エッグ思考)。補正の一部は季節付き
# ('春+1') のため文字列で保持する。エッグ種別の '－' は卵なし。
# 照合は tests/test_hanjuku_strong_cards.py: HP は char.csv 由来の general_max_hp
# と全127名、思考は hanjuku_egg_reference.GENERAL_EGGS の2列目と全128名が一致する。
# gcgx が全角空白で書く 'ラ ターシュ' は char.csv の綴りに合わせた。
GENERAL_STATS: dict[str, tuple[int, int, str, str, int]] = {
    'しゅじんこう': (90, 14, '0', 'エラベル', 2),  # 0
    'キャラウェイ': (40, 4, '0', '－', 2),  # 1
    'クミン': (27, 2, '0', 'カラフル', 3),  # 2
    'コリアンダー': (38, 7, '1', '－', 2),  # 3
    'バジル': (40, 3, '1', '－', 2),  # 4
    'ミント': (32, 10, '0', '－', 2),  # 5
    'パプリカ': (34, 4, '-1', '－', 2),  # 6
    'シナモン': (39, 6, '-2', '－', 2),  # 7
    'ヘーゼル': (33, 4, '1', 'イビル', 3),  # 8
    'ガルバンゾー': (30, 3, '-1', 'イビル', 3),  # 9
    'ラズベリー': (45, 8, '0', '－', 3),  # 10
    'ピスタチオ': (49, 8, '-2', '－', 2),  # 11
    'マカデミア': (34, 4, '-1', '－', 2),  # 12
    'カシュー': (39, 1, '0', 'イビル', 3),  # 13
    'クランベリー': (51, 5, '-2', '－', 2),  # 14
    'ガスパチョ': (37, 3, '0', 'スーパー', 3),  # 15
    'ビシソワーズ': (27, 3, '1', 'ワンダー', 3),  # 16
    'タピオカ': (50, 7, '0', '－', 2),  # 17
    'キッシュ': (26, 5, '0', 'スーパー', 2),  # 18
    'ロックフォール': (57, 9, '0', '－', 3),  # 19
    'シェーブル': (27, 4, '2', 'ワンダー', 3),  # 20
    'アマンディーヌ': (59, 9, '0', '－', 2),  # 21
    'チコリ': (18, 3, '0', 'イビル', 3),  # 22
    'ビーツ': (29, 9, '0', '－', 3),  # 23
    'セルリアク': (32, 7, '-2', '－', 3),  # 24
    'アンディーブ': (36, 8, '1', '－', 1),  # 25
    'リーキ': (30, 9, '0', 'カラフル', 1),  # 26
    'トレビス': (66, 6, '0', '－', 3),  # 27
    'アルファルファ': (38, 5, '春+1', 'スーパー', 2),  # 28
    'タルタル': (40, 9, '夏+1', '－', 1),  # 29
    'ヘルメス': (63, 9, '0', '－', 1),  # 30
    'リースリング': (37, 5, '0', '－', 3),  # 31
    'デュオニソス': (54, 6, '-3', 'カラフル', 1),  # 32
    'アルテミス': (49, 7, '3', 'ワンダー', 3),  # 33
    'キャンディー': (26, 2, '0', 'いっぱつ', 3),  # 34
    'シャルドネ': (56, 8, '-1', '－', 3),  # 35
    'ソーピニヨン': (48, 10, '-2', 'イビル', 1),  # 36
    'ピオーネ': (46, 2, '0', 'ワンダー', 3),  # 37
    'ヘラ': (59, 10, '-1', '－', 1),  # 38
    'セミヨン': (36, 5, '-2', 'カラフル', 3),  # 39
    'ポワソン': (80, 9, '0', '－', 1),  # 40
    'デーメーテール': (49, 10, '-3', '－', 1),  # 41
    'ユイートル': (57, 8, '春-1', '－', 1),  # 42
    'ヘパイストス': (49, 9, '-2', 'スーパー', 3),  # 43
    'エシャロット': (46, 7, '1', 'ワンダー', 3),  # 44
    'ミュスカデ': (57, 9, '0', '－', 1),  # 45
    'アテナ': (66, 11, '-1', 'スーパー', 1),  # 46
    'エピィ': (57, 9, '-2', 'スロット', 1),  # 47
    'ポセイドン': (85, 15, '-3', '－', 1),  # 48
    'ペコリーノ': (88, 9, '0', '－', 1),  # 49
    'アポロン': (74, 14, '1', 'スーパー', 1),  # 50
    'ジェラート': (49, 6, '0', 'ワンダー', 3),  # 51
    'ミルフィーユ': (39, 6, '1', '－', 2),  # 52
    'ヘスティア': (25, 9, '0', 'イビル', 2),  # 53
    'キール': (65, 12, '-1', '－', 2),  # 54
    'ライム': (69, 10, '-2', '－', 2),  # 55
    'レモン': (27, 2, '2', 'ワンダー', 2),  # 56
    'フェットチーネ': (67, 7, '夏+1', 'ワンダー', 2),  # 57
    'バーミセリ': (89, 12, '0', '－', 2),  # 58
    'カペリーニ': (63, 7, '0', '－', 2),  # 59
    'ブカティーニ': (42, 8, '0', 'ワンダー', 2),  # 60
    'ナストリーニ': (66, 8, '0', '－', 2),  # 61
    'ラビオリ': (87, 8, '0', '－', 2),  # 62
    'フェデリーニ': (8, 3, '-3', '－', 2),  # 63
    'カシス': (73, 15, '0', 'いっぱつ', 2),  # 64
    'グレナデン': (81, 7, '0', '－', 2),  # 65
    'チキータ': (74, 15, '-2', 'いっぱつ', 2),  # 66
    'ランプータン': (27, 4, '1', 'かぼちゃ', 2),  # 67
    'ドリアン': (51, 7, '2', '－', 2),  # 68
    'マスカット': (27, 9, '0', 'スーパー', 2),  # 69
    'マンゴスチン': (85, 12, '0', '－', 3),  # 70
    'バタール': (99, 13, '-2', '－', 2),  # 71
    'バゲット': (37, 14, '0', 'スロット', 2),  # 72
    'ブリオッシュ': (60, 10, '0', 'カラフル', 3),  # 73
    'バトウラ': (55, 11, '2', '－', 2),  # 74
    'ブレッツェル': (71, 11, '0', 'ワンダー', 2),  # 75
    'マフィン': (15, 10, '-1', 'スロット', 2),  # 76
    'ベーグル': (91, 10, '-2', '－', 2),  # 77
    'ミモザ': (66, 12, '1', 'まねっこ', 0),  # 78
    'ショコラ': (56, 11, '3', 'まねっこ', 0),  # 79
    'プラリネ': (87, 13, '0', '－', 0),  # 80
    'ブラマンジェ': (81, 10, '-1', '－', 0),  # 81
    'シフォン': (62, 13, '-2', 'まねっこ', 0),  # 82
    'シュゼット': (90, 13, '0', '－', 2),  # 83
    'ブラウニー': (5, 1, '0', 'くさってる', 2),  # 84
    'キャロット': (60, 12, '0', 'いっぱつ', 3),  # 85
    'マサラ': (77, 11, '-1', '－', 2),  # 86
    'バラクーダ': (76, 9, '-2', '－', 2),  # 87
    'コンポート': (55, 10, '1', 'いっぱつ', 3),  # 88
    'ギー': (71, 14, '2', '－', 2),  # 89
    'グリッシーニ': (74, 15, '3', 'いっぱつ', 2),  # 90
    'シュガー': (49, 12, '2', 'いっぱつ', 2),  # 91
    'オレガノ': (65, 15, '0', 'イビル', 1),  # 92
    'ジキタリス': (26, 11, '2', '－', 3),  # 93
    'ローズマリー': (98, 14, '2', '－', 1),  # 94
    'マーマレード': (74, 11, '-1', '－', 1),  # 95
    'サフラン': (64, 12, '-2', 'カラフル', 1),  # 96
    'アニス': (87, 12, '0', '－', 1),  # 97
    'エストラゴン': (66, 14, '-2', 'スーパー', 1),  # 98
    'エッジ': (99, 13, '0', '－', 2),  # 99
    'リディア': (72, 15, '0', 'スーパー', 2),  # 100
    'ガーラント': (97, 14, '1', '－', 2),  # 101
    'カイン': (74, 15, '0', 'スーパー', 2),  # 102
    'グレイ': (73, 14, '-1', 'カラフル', 2),  # 103
    'レオンハルト': (71, 15, '1', 'エラベル', 2),  # 104
    'フリオニール': (67, 15, '2', 'エラベル', 2),  # 105
    'サムソー': (98, 12, '1', '－', 2),  # 106
    'ハバティー': (54, 11, '2', 'キング', 2),  # 107
    'マリボー': (66, 9, '0', 'まねっこ', 0),  # 108
    'ラクレット': (36, 10, '-1', 'かどまつ', 2),  # 109
    'エダム': (48, 12, '-2', 'ワンダー', 2),  # 110
    'リゴット': (99, 11, '1', '－', 2),  # 111
    'ブリー': (29, 11, '春-1', 'まねっこ', 0),  # 112
    'マルガリータ': (72, 13, '夏-1', 'キング', 2),  # 113
    'ジン': (97, 15, '秋-1', '－', 2),  # 114
    'マラスキーノ': (74, 12, '-2', 'ワンダー', 2),  # 115
    'アクアビット': (69, 14, '1', 'おそなえ', 2),  # 116
    'ラ ターシュ': (69, 14, '2', 'ベビー', 2),  # 117
    'バランタイン': (70, 13, '1', 'かぼちゃ', 2),  # 118
    'カミュ': (73, 15, '3', 'サイバー', 2),  # 119
    'クイーン': (70, 13, '0', 'スーパー', 3),  # 120
    'プリンス': (100, 14, '0', 'スーパー', 3),  # 121
    'にせヒーロー': (90, 14, '0', 'スーパー', 3),  # 122
    'せいめいたい': (500, 9, '0', 'スーパー', 3),  # 123
    'だいじん': (90, 14, '0', '－', 2),  # 124
    'ココット': (24, 8, '3', 'カラフル', 3),  # 125
    'ヴィーナス': (82, 12, '3', 'スーパー', 2),  # 126
    'ゼウス': (85, 13, '0', 'ワンダー', 2),  # 127
}

# 騎馬 (wikiwiki 将軍ページの「馬」列、2026-10-20 確認)。ここに無い将軍は
# 徒歩。騎馬は移動・突撃に有利なため、強制解雇の比較では同条件なら徒歩を先に
# 弱いとみなす (Issue #2367)。
GENERAL_HORSES: frozenset[str] = frozenset({
    'ココット', 'ピスタチオ', 'カシュー', 'キッシュ', 'シェーブル', 'タルタル',
    'ヘルメス', 'デーメーテール', 'ミュスカデ', 'エピィ', 'フェットチーネ',
    'バーミセリ', 'フェデリーニ', 'バタール', 'バゲット', 'ブリオッシュ',
    'バトウラ', 'ブレッツェル', 'マフィン', 'ベーグル', 'ブラウニー', 'マサラ',
    'コンポート', 'グリッシーニ', 'オレガノ', 'エストラゴン', 'エッジ',
    'ガーラント', 'バランタイン',
})


# gcgx shogun.html の将軍一覧から、GENERAL_STATS に無い表示項目だけを補う
# (fetched 2026-10-20; SFC ID 0..127 の128名)。値は (対抗%, 得意, 性格, 趣味)。
# 対抗は「自軍がエグモンを召喚している際に対抗してエッグを使用する確率」で、
# 一般的な卵使用率ではない。得意は季節・地形 ('夏２', '城攻' など) をそのまま
# 保持し、'－' は空文字。HP・戦闘・補正・エッグ種別・思考・初登場話数は
# GENERAL_STATS / GENERAL_DEBUT を正本とし、ここへ重複して持たない。
GENERAL_TRAITS: dict[str, tuple[int, str, str, str]] = {
    'しゅじんこう': (50, '', '甘えんぼ', 'つまみぐい'),  # 0
    'キャラウェイ': (50, '夏２', '変な奴', 'ヘヴィメタル'),  # 1
    'クミン': (50, '', '弱気', 'たてごと'),  # 2
    'コリアンダー': (50, '', '楽天家', 'ハニワ'),  # 3
    'バジル': (50, '森', '強気', 'ディスコ'),  # 4
    'ミント': (50, '冬１', '弱気', 'たべあるき'),  # 5
    'パプリカ': (50, '森', '自己陶酔', 'カジノ'),  # 6
    'シナモン': (50, '山', '楽天家', 'よあそび'),  # 7
    'ヘーゼル': (50, '水中', '和風', 'すいえい'),  # 8
    'ガルバンゾー': (50, '平地', '強気', 'サッカー'),  # 9
    'ラズベリー': (50, '森', '献身的', 'おんがく'),  # 10
    'ピスタチオ': (50, '秋１', 'オタク', 'れきし'),  # 11
    'マカデミア': (50, '', '弱気', 'サーフィン'),  # 12
    'カシュー': (50, '', '一般的', 'ボクシング'),  # 13
    'クランベリー': (50, '森', '身勝手', 'パラグライダー'),  # 14
    'ガスパチョ': (50, '', 'オタク', 'にんぎょう'),  # 15
    'ビシソワーズ': (50, '森', 'オタク', 'おっかけ'),  # 16
    'タピオカ': (50, '', '自己陶酔', 'サバイバルゲーム'),  # 17
    'キッシュ': (50, '洞窟', '身勝手', 'カルトクイズ'),  # 18
    'ロックフォール': (50, '', '自己陶酔', 'アイドル'),  # 19
    'シェーブル': (50, '平地', '献身的', 'ジョギング'),  # 20
    'アマンディーヌ': (50, '森', '弱気', 'ケーキづくり'),  # 21
    'チコリ': (75, '', '一般的', 'パッチワーク'),  # 22
    'ビーツ': (75, '', '超世間的', 'ぜん'),  # 23
    'セルリアク': (75, '城守', '泣き虫', 'カラオケ'),  # 24
    'アンディーブ': (25, '夏１', 'お調子者', 'ボディービル'),  # 25
    'リーキ': (50, '春２', '一般的', 'えいがかんしょう'),  # 26
    'トレビス': (25, '水中', '強気', 'ダイビング'),  # 27
    'アルファルファ': (50, '森', '和風', 'あいきどう'),  # 28
    'タルタル': (25, '', '楽天家', 'かていさいえん'),  # 29
    'ヘルメス': (25, '城攻', 'うぬぼれ屋', 'かたな'),  # 30
    'リースリング': (50, '冬２', '変な奴', 'ショッピング'),  # 31
    'デュオニソス': (25, '春２', '説教臭い', 'まじゅつ'),  # 32
    'アルテミス': (75, '春１', '強気', 'バンデット'),  # 33
    'キャンディー': (75, '森', '弱気', 'りょこう'),  # 34
    'シャルドネ': (25, '夏１', '強気', 'さけ'),  # 35
    'ソーピニヨン': (25, '', '一般的', 'ゴルフ'),  # 36
    'ピオーネ': (25, '', '変な奴', 'ハードロック'),  # 37
    'ヘラ': (25, '城守', '説教臭い', 'ぶつぞう'),  # 38
    'セミヨン': (75, '', '泣き虫', 'なつメロ'),  # 39
    'ポワソン': (25, '水中', '自己陶酔', 'ベースギター'),  # 40
    'デーメーテール': (25, '', '献身的', 'テコンドー'),  # 41
    'ユイートル': (25, '水中', '強気', 'エレキギター'),  # 42
    'ヘパイストス': (75, '', '説教臭い', 'しんやのドライブ'),  # 43
    'エシャロット': (25, '', '弱気', 'にちようだいく'),  # 44
    'ミュスカデ': (25, '平地', 'うぬぼれ屋', 'ゲームセンター'),  # 45
    'アテナ': (25, '城守', '強気', 'あさシャン'),  # 46
    'エピィ': (25, '冬２', '和風', 'カブキ'),  # 47
    'ポセイドン': (25, '水中', '一般的', 'パンクロック'),  # 48
    'ペコリーノ': (25, '', '一般的', 'じだいげき'),  # 49
    'アポロン': (25, '城攻', 'うぬぼれ屋', 'ワイン'),  # 50
    'ジェラート': (0, '森', 'うぬぼれ屋', 'ながでんわ'),  # 51
    'ミルフィーユ': (50, '森', '甘えんぼ', 'きつねがり'),  # 52
    'ヘスティア': (50, '水中', '献身的', 'ヨット'),  # 53
    'キール': (50, '', '楽天家', 'てんもんがく'),  # 54
    'ライム': (50, '', '献身的', 'ふくしょく'),  # 55
    'レモン': (50, '森', '泣き虫', 'こびじゅつ'),  # 56
    'フェットチーネ': (50, '春１', '泣き虫', 'しんりんよく'),  # 57
    'バーミセリ': (50, '山', '身勝手', 'やまあるき'),  # 58
    'カペリーニ': (50, '夏１', '身勝手', 'プロレス'),  # 59
    'ブカティーニ': (50, '', '身勝手', 'ローラーブレード'),  # 60
    'ナストリーニ': (50, '森', '自己陶酔', 'ピアノ'),  # 61
    'ラビオリ': (50, '', '自己陶酔', 'スケートボード'),  # 62
    'フェデリーニ': (50, '', '弱気', 'ケーキづくり'),  # 63
    'カシス': (50, '', '泣き虫', 'マージャン'),  # 64
    'グレナデン': (50, '', '身勝手', 'オカルト'),  # 65
    'チキータ': (50, '冬１', '変な奴', 'カエルのかいぼう'),  # 66
    'ランプータン': (50, '夏２', '一般的', 'スコッチ'),  # 67
    'ドリアン': (50, '夏１', '一般的', 'エアーガン'),  # 68
    'マスカット': (50, '秋１', '楽天家', 'ランバダ'),  # 69
    'マンゴスチン': (75, '', '自己陶酔', 'いじめ'),  # 70
    'バタール': (50, '', 'お調子者', 'パズル'),  # 71
    'バゲット': (50, '', 'オタク', 'すうがく'),  # 72
    'ブリオッシュ': (75, '', '弱気', 'がくもんぜんぱん'),  # 73
    'バトウラ': (50, '', '楽天家', 'ぶつりがく'),  # 74
    'ブレッツェル': (50, '', '楽天家', 'ひるね'),  # 75
    'マフィン': (50, '', '泣き虫', 'さんぽ'),  # 76
    'ベーグル': (50, '', '弱気', 'きかがく'),  # 77
    'ミモザ': (75, '森', '甘えんぼ', 'ダジャレ'),  # 78
    'ショコラ': (75, '', '変な奴', 'いっぱつギャグ'),  # 79
    'プラリネ': (75, '森', 'お調子者', 'ダイエット'),  # 80
    'ブラマンジェ': (75, '洞窟', '和風', 'こもんじょ'),  # 81
    'シフォン': (75, '', '一般的', 'れんきんじゅつ'),  # 82
    'シュゼット': (50, '', '一般的', '‥‥なビデオ'),  # 83
    'ブラウニー': (50, '', '弱気', 'おつきみ'),  # 84
    'キャロット': (75, '', '和風', 'いご'),  # 85
    'マサラ': (50, '', '和風', 'つり'),  # 86
    'バラクーダ': (50, '', '超世間的', 'しょうぎ'),  # 87
    'コンポート': (75, '', '超世間的', 'しゃきょう'),  # 88
    'ギー': (50, '春２', '一般的', 'はいく'),  # 89
    'グリッシーニ': (50, '', '強気', 'いくさ'),  # 90
    'シュガー': (50, '春１', '超世間的', 'ちゃのゆ'),  # 91
    'オレガノ': (25, '', '強気', 'トライアスロン'),  # 92
    'ジキタリス': (75, '', '一般的', 'かていいりょう'),  # 93
    'ローズマリー': (25, '森', '超世間的', 'カンフー'),  # 94
    'マーマレード': (25, '春２', 'オタク', 'パソコン'),  # 95
    'サフラン': (25, '', 'オタク', 'こんちゅう'),  # 96
    'アニス': (25, '森', '楽天家', 'かせきひょうほん'),  # 97
    'エストラゴン': (25, '', 'オタク', 'ポップアート'),  # 98
    'エッジ': (50, '', '楽天家', 'バーボン'),  # 99
    'リディア': (50, '森', '強気', 'アメフト'),  # 100
    'ガーラント': (50, '', '泣き虫', 'おはなみ'),  # 101
    'カイン': (50, '', '一般的', 'だいリーグ'),  # 102
    'グレイ': (50, '', '強気', 'せんそうゲーム'),  # 103
    'レオンハルト': (50, '城攻', '自己陶酔', 'かくとうぎ'),  # 104
    'フリオニール': (50, '城攻', 'うぬぼれ屋', 'ポエム'),  # 105
    'サムソー': (50, '', '一般的', 'ハッキング'),  # 106
    'ハバティー': (50, '', '献身的', 'ながでんわ'),  # 107
    'マリボー': (75, '', '献身的', 'キャンプファイア'),  # 108
    'ラクレット': (50, '森', '一般的', 'ジルバ'),  # 109
    'エダム': (50, '', '泣き虫', 'どくしょ'),  # 110
    'リゴット': (50, '', '献身的', 'ぶんつう'),  # 111
    'ブリー': (75, '', '一般的', 'ビリヤード'),  # 112
    'マルガリータ': (50, '森', 'うぬぼれ屋', 'ミントチョコ'),  # 113
    'ジン': (50, '', '一般的', 'ゴーカート'),  # 114
    'マラスキーノ': (50, '森', '自己陶酔', 'たまごりょうり'),  # 115
    'アクアビット': (50, '', '一般的', 'カクテル'),  # 116
    'ラ ターシュ': (50, '', '自己陶酔', 'サボテン'),  # 117
    'バランタイン': (50, '', '自己陶酔', 'インテリア'),  # 118
    'カミュ': (50, '森', '泣き虫', 'ドライフラワー'),  # 119
    'クイーン': (75, '', 'うぬぼれ屋', 'くつコレクション'),  # 120
    'プリンス': (75, '', 'うぬぼれ屋', 'アスレティック'),  # 121
    'にせヒーロー': (75, '', '甘えんぼ', 'いたずら'),  # 122
    'せいめいたい': (75, '', '一般的', 'かいでんぱ'),  # 123
    'だいじん': (50, '', '説教臭い', 'かんぷまさつ'),  # 124
    'ココット': (75, '平地', '泣き虫', 'じょうば'),  # 125
    'ヴィーナス': (50, '森', '一般的', 'テニス'),  # 126
    'ゼウス': (50, '城攻', '一般的', 'ぶどうぜんぱん'),  # 127
}


# Attack data for summoned-monster skills (対モ威力 / 対将威力 / 攻撃回数 /
# 全体攻撃 / 自傷). Source: SFC half-cooked hero data wiki
# "エッグモンスター／攻撃データ"
# https://wikiwiki.jp/hjksfc/エッグモンスター/攻撃データ
# Each entry: monster name -> {'skill1': (name, vs_monster, vs_general,
#   hits, is_all, self_damage), 'skill2': (...)}.
# Only representative / policy-relevant rows are transcribed here; unknown
# monsters fall back to the generic opponent-aware heuristics below.
MONSTER_ATTACK_DATA: dict[str, dict[str, tuple]] = {
    'ワラワラ': {
        'skill1': ('９９かいパンチ', 4, 15, 99, False, False),
        'skill2': ('９９かいキック', 15, 4, 99, False, False),
    },
    'ウッドボール': {
        'skill1': ('たいあたり', 10, 10, 1, False, False),
        'skill2': ('ばくはつする', 0, 0, 1, True, True),
    },
}

# Skills that damage the user itself; excluded from normal attack selection.
# Source: SFC half-cooked hero data wiki "エッグモンスター／攻撃データ"
# (https://wikiwiki.jp/hjksfc/エッグモンスター/攻撃データ).
# Only skills explicitly marked 自爆 / 自分にNダメージ in that table are
# listed here.  'ばくはつする' (ウッドボール) is 自爆; 'ハードバズーカ'
# (ハードマン) is NOT self-damage — it is an ALL-target attack (回数=AL)
# with no 自爆 note, so it is intentionally omitted.
MONSTER_SELF_DAMAGE_SKILLS: frozenset[str] = frozenset({
    'ばくはつする',
})

# Summoned-monster skill tables (wikiwiki attack-data page, transcribed).
# Each unit knows exactly two skills; MONSTER_EFFECT_SKILLS is the global set
# of skills whose effect flag is non-empty (duplicates agree on the flag).
MONSTER_SKILLS: dict[str, tuple[str, ...]] = {
    'エッグスライム': ('どろどろ', 'ぐちゃぐちゃ'),
    'コロボックル': ('たたかう', 'あいさつする'),
    'バリゾーゴン': ('ばらす', 'わるぐち'),
    'ライトきょうだい': ('せかいでさいしょ', 'なかまわれ'),
    'モザイクマン': ('れんぞくこうげき', 'コマンダーＸ'),
    'ケロベロス': ('かみつく', 'しっぽをふる'),
    'ランプキン': ('へんなおどり', 'かぼちゃ'),
    'ウッドボール': ('たいあたり', 'ばくはつする'),
    'セクシーボンバー': ('ダイナマイト', 'ミサイルくん'),
    'おーでーん': ('ざんてつけん', 'グングニル'),
    'バルーンフィンチ': ('ふくらむ', 'シャウト'),
    'ボルシチ': ('ちゃんこ', 'げきからカレー'),
    'くちびるナイト': ('じょうねつのキス', 'メイクアップ'),
    'てつじん８ごう': ('コダイミサイル', 'サイシュウヘイキ'),
    'はんぎょじん': ('もりこうげき', 'あしひれアタック'),
    'グランドパパ': ('わらう', 'いかる'),
    'グランドパパ(怒)': ('だいげきど', 'ひっくりかえる'),
    'ガートルード': ('であい', 'そして わかれ'),
    'デス': ('しにがみのカマ', 'タマシイヌキ'),
    'さすらいマンボー': ('あのひのおもい', 'さすらいのうた'),
    'しろまどうし': ('ケアル', 'ソーリー'),
    'ワラワラ': ('９９かいパンチ', '９９かいキック'),
    'ピスクピグプレム': ('いばらのむち', 'わかくさのかおり'),
    'ムーンマッスル': ('ムーンライト', 'ダンベルボム'),
    'スカイプリンセス': ('ナパームだん', 'さいるいだん'),
    'ハデデス': ('ギャグ', 'ダジャレ'),
    'おやすみメリー': ('バリカンでかる', 'ヴァイオリン'),
    'さんようちゅう': ('ぺろぺろなめる', 'もぐりこむ'),
    'ムンクゴースト': ('シッポビンタ', 'さけび'),
    'メイジュース': ('かるくずつき', 'メイクイーン'),
    'ファイナルゼリー': ('けんかをうる', 'へばりつく'),
    'やよい': ('そちゃ', 'おちゃうけ'),
    'オイジュース': ('ずつき', 'じゃがいも'),
    'カメレオンマン': ('たべちゃうぞー', 'とけこむぞー'),
    'ダークエルフ': ('ひとだまくん', 'ダークフォース'),
    'ウゴカザル': ('なぐれっ！', 'かきむしれ！'),
    'プチデビル': ('とびげり', 'リトルモアモア'),
    'キャンドロー': ('ロウをたらす', 'かなしいはなし'),
    'なめくじおとこ': ('たんをはく', 'ねんえきネバネバ'),
    'キノコやろう': ('トリップダケ', 'ワライダケ'),
    'ゲーラス': ('ひっかく', 'クサイいきをはく'),
    'クレクレ': ('ムシャムシャくう', 'しょうかえき'),
    'とうめいにんげん': ('イタズラがき', 'イナイイナイバア'),
    'ラビットサタン': ('デスのカマ', 'ラビットキック'),
    'スモーキーガスト': ('けむにまく', 'ちっそくスモーク'),
    'フランソワーズ': ('にげまどう', 'すねる'),
    'アモン': ('きゅうしゅう', 'あんこく'),
    'デビルウーマン': ('モーニングスター', 'とっつかまえる'),
    'ルキュフェル': ('６まいのつばさ', 'だらくさせる'),
    'バブリー': ('バブルストーム', 'シルキーミスト'),
    'バール': ('もうどく', 'マリオネット'),
    'フラフラ': ('フラフラアタック', 'バタバタアタック'),
    'ゾンビ': ('ゾンビパンチ', 'ケアル'),
    'ニンフ': ('うっふんウインク', 'にくたいび'),
    'エルフドラゴン': ('かわいいツメ', 'かわいくはばたく'),
    'ドラゴンたろう': ('ツメとキバ', 'キックとパンチ'),
    'ベビーモス': ('でんぐりがえし', 'おぶさる'),
    'モーグリ': ('トライデント', 'とっしん'),
    'ハーフドラゴン': ('しっぽ', 'キック'),
    'コマイヌ': ('かみつく', 'おどす'),
    'ドラゴンフライ': ('シャチホコ', 'エビフリャー'),
    'ローラーキラー': ('ダッシュプレス', 'メガトンプレス'),
    'たまごキャリー': ('うっちゃり', 'たまご'),
    'ぼーぼーどり': ('ほのおのはね', 'へるファイア'),
    'マシンナイト': ('ロケットパンチ', 'すもうタックル'),
    'ダディ': ('ボディプレス', 'にくあつ'),
    'ドラゴンパピー': ('ほのおをはく', 'なつく'),
    'ヘビーモス': ('けんかをする', 'しっぽをふる'),
    'テュポーン': ('クジラアタック', 'しおふき'),
    'ハニワゴーレム': ('ハニードリル', 'ハニーはりて'),
    'あ た し ♥': ('く ち づ け♥', 'か た ら い♥'),
    'ぞうさんだいおう': ('パオーのはな', 'パオーのおなか'),
    'サイクロプス': ('にくだんこうげき', 'ほうがんなげ'),
    'にんげんライダー': ('ドラゴンアタック', 'にんげんアタック'),
    'てんりゅう': ('イカズチ', 'ほうこう'),
    'ちきゅうちゃん': ('だいじしん', 'ハルマゲドン'),
    'コロコロムシ': ('せなかにはりつく', 'いとをだす'),
    'あいのししゃ': ('あいのひかり', 'だきしめる'),
    'マミー': ('ファラオのさばき', 'ほうたいのまい'),
    'メトロノーム': ('もっとはやく！！', 'もっとおそく！！'),
    'フリージーボーイ': ('エアコン', 'れいとう'),
    'ニンニクマン': ('でまえいっちょ', 'ニオウンデス'),
    'バッティングマン': ('ホームラン', 'せんぼんノック'),
    'エルフ': ('こうきゅう', 'かえんのまい'),
    'サンドワーム': ('ドリルでほる', 'てぬきこうじ'),
    'コーヒービート': ('マラカスビート', 'エスプレッソ'),
    'なると': ('ぎざぎざ', 'うずまき'),
    'エクスカリバー': ('エクスカリバる', 'マサムネる'),
    'ヒュドラ': ('まきつく', 'ウォーター'),
    'ユニコーン': ('つのでつく', 'うしろあしキック'),
    'ガーコイル': ('スプリング', 'するどいツメ'),
    'スロウケンタ': ('ばていしゅりけん', 'ぴたんこアロー'),
    'メデューサ': ('おうふくビンタ', 'せきぞうにおなり'),
    'たまごまじん': ('どつく', 'いかずちをおとす'),
    'アマゾン': ('サラマンドのけん', 'ガラハドのけん'),
    'シカルドラゴン': ('ステッキでたたく', 'せっきょう'),
    'フロストベビー': ('バブー', 'オギャー'),
    'アレス': ('けんできる', 'けんをかざす'),
    'レッドドラゴン': ('ウイング', 'マグマふんしゃ'),
    'カバドラゴン': ('きこうだん', 'ぼんじこうせん'),
    'ハデス': ('デビルオーラ', 'サイコバーン'),
    'エッグマン': ('エッグチョップ', 'エッグキック'),
    'エッグマンナイト': ('エッグソード', 'エッグビーム'),
    'しんエッグマン': ('メガチョップ', 'メガキック'),
    'キングエッグマン': ('キングこづち', 'スーパー…ビーム'),
    'エッグベビー': ('バブーチョップ', 'オギャービーム'),
    'あけまつ': ('ごあいさつ', 'おもてなし'),
    'おめでとり': ('ミカンをおす', 'あたためる'),
    'スプリミョーネ': ('つくしんボム', 'はるのうた'),
    'フォーリシア': ('かれはのまい', 'うらみうた'),
    'ウイナッツォ': ('あついおでん', 'こたつでねむれ'),
    'サマカンテ': ('ホットないちげき', 'ひとなつのこい'),
    'だいまおう': ('かんぺきこうげき', 'てんぺんちい'),
    'ハードマン': ('ハードアタック', 'ハードバズーカ'),
    'ハードロボ': ('かたゆでぎり', 'エッグスライサー'),
    'ランパイア': ('デッドリーネイル', 'エキスキッス'),
    'ノブナーガ': ('おけはざまぎり', 'ナーガいもの'),
    'だいとうりょう': ('じょうりゅうけん', 'ズグラーク！'),
    'エッグママ': ('ぼせいほんのう', 'せんのう'),
    'クーモン': ('ちょうみりょう', 'くうもん！'),
    'スーモン': ('マントをとじる', 'すうもん！'),
}
MONSTER_EFFECT_SKILLS: frozenset[str] = frozenset({
    'あたためる', 'あのひのおもい', 'あんこく', 'いかる', 'いとをだす', 'うずまき',
    'うっふんウインク', 'うらみうた', 'おけはざまぎり', 'おちゃうけ', 'おどす', 'おぶさる',
    'おもてなし', 'か た ら い♥', 'かえんのまい', 'かなしいはなし', 'かぼちゃ', 'かわいくはばたく',
    'きゅうしゅう', 'く ち づ け♥', 'くうもん！', 'ぐちゃぐちゃ', 'げきからカレー', 'こたつでねむれ',
    'ごあいさつ', 'さいるいだん', 'さけび', 'さすらいのうた', 'しおふき', 'しっぽをふる',
    'じゃがいも', 'じょうねつのキス', 'じょうりゅうけん', 'すうもん！', 'せきぞうにおなり', 'せっきょう',
    'せんのう', 'たまご', 'だいじしん', 'だきしめる', 'だらくさせる', 'ちっそくスモーク',
    'ちょうみりょう', 'つのでつく', 'とけこむぞー', 'とっしん', 'どつく', 'なかまわれ',
    'なつく', 'にくあつ', 'にんげんアタック', 'ねんえきネバネバ', 'はるのうた', 'ばくはつする',
    'ばらす', 'ひっくりかえる', 'ひとだまくん', 'ひとなつのこい', 'ぴたんこアロー', 'ふくらむ',
    'へばりつく', 'へるファイア', 'へんなおどり', 'ぺろぺろなめる', 'ほうこう', 'ほうたいのまい',
    'まきつく', 'もぐりこむ', 'もっとおそく！！', 'れいとう', 'わかくさのかおり', 'わるぐち',
    'イタズラがき', 'イナイイナイバア', 'エアコン', 'エキスキッス', 'エクスカリバる', 'エスプレッソ',
    'エッグビーム', 'エビフリャー', 'オギャービーム', 'ガラハドのけん', 'キングこづち', 'クサイいきをはく',
    'グングニル', 'ケアル', 'コマンダーＸ', 'サイコバーン', 'サイシュウヘイキ', 'サラマンドのけん',
    'シャウト', 'シルキーミスト', 'ソーリー', 'ゾンビパンチ', 'タマシイヌキ', 'ダジャレ',
    'ダークフォース', 'デスのカマ', 'デビルオーラ', 'トリップダケ', 'ナーガいもの', 'ニオウンデス',
    'ハルマゲドン', 'バブー', 'バリカンでかる', 'パオーのおなか', 'ファラオのさばき', 'ホットないちげき',
    'ホームラン', 'マリオネット', 'ミカンをおす', 'ミサイルくん', 'ムシャムシャくう', 'メイクアップ',
    'メイクイーン', 'メガトンプレス', 'モーニングスター', 'リトルモアモア', 'ワライダケ',
    'ヴァイオリン',
})
# Owner-confirmed heals (2026-09-28): バルーンフィンチ's ふくらむ was picked
# 71 times in a row at full HP, so the enemy never took damage (g407 loop).
MONSTER_HEAL_SKILLS: frozenset[str] = frozenset({'ふくらむ', 'ケアル'})

#食いしばり (endure): lethal card damage clamps to HP-1 when
# damage <= current_hp + 16.
ENDURE_HEADROOM = 16

# These four enemies have general panels but take the boss column of card
# damage, without the allied-soldier bonus.  The chapter-9 chart names the
# summoned ハデス, so chart.BOSSES is not a complete list of general bosses.
# Sources: https://blog.livedoor.jp/hanjukueiyu/archives/1035528547.html
#          https://gcgx.games/hanjuku/03.html and /09.html (soldiers: HP100)
BOSS_GENERALS = frozenset({'クイーン', 'にせヒーロー', 'プリンス', 'せいめいたい'})
GENERAL_SOLDIER_HP = 10
GENERAL_BOSS_SOLDIER_HP = {'クイーン': 10, 'にせヒーロー': 10,
                           'プリンス': 100, 'せいめいたい': 100}
MAX_BATTLE_SOLDIERS = 6


def endure_safe_kill_hp(card_damage: int) -> int:
    """Max enemy HP where ``card_damage`` still kills through 食いしばり."""
    # Need damage > hp + 16, i.e. hp <= damage - 17? Chart: ファバード100 →
    # kill at HP83 or less (84+ survives). 100 - 17 = 83. Yes.
    return card_damage - (ENDURE_HEADROOM + 1)


def card_damage_estimate(card: str, *, target_kind: str,
                         enemy_name: str | None = None, enemy_hp=None,
                         ally_hp=None, ally_soldiers=None, enemy_soldiers=None) -> dict:
    """Conservative single-card damage from the *current* battle observation.

    Ordinary damage is absorbed by enemy soldiers before reaching the
    general.  Their individual remaining HP is not visible: use the full HP
    of every remaining soldier, never reduce it because a card was selected.
    Unread allied/enemy counts use 0/6, respectively.  The caller must discard
    stale observations rather than passing them as current counts or HP.

    ``lethal=True`` means the conservative estimate is sufficient; False
    means it does not prove a kill (damaged soldiers may absorb less).  None
    means a required target/HP/effect is unknown.  This is a prediction, not
    proof that a card was used, hit, killed, or dropped an egg.

    Card formula and soldier absorption examples:
    https://karzu.exblog.jp/24199919/ (battle/card section)
    https://blog.livedoor.jp/hanjukueiyu/archives/1035528547.html
    Special effects: https://gcgx.games/hanjuku/kirihuda.html
    Boss/general exceptions: https://wikiwiki.jp/hjksfc/切り札
    """
    def current_hp(value):
        return value if type(value) is int and 0 < value <= 9999 else None

    def current_soldiers(value):
        return value if type(value) is int and 0 <= value <= MAX_BATTLE_SOLDIERS else None

    hp = current_hp(enemy_hp)
    own_hp = current_hp(ally_hp)
    own_count = current_soldiers(ally_soldiers)
    enemy_count = current_soldiers(enemy_soldiers)
    own_lower = own_count if own_count is not None else 0
    enemy_upper = enemy_count if enemy_count is not None else MAX_BATTLE_SOLDIERS
    basis = []
    if own_count is None:
        basis.append('unread_ally_soldiers_use_zero')
    if enemy_count is None:
        basis.append('unread_enemy_soldiers_use_six')
    if hp is None:
        basis.append('current_enemy_hp_unknown')
    if target_kind == 'general' and enemy_name in BOSS_GENERALS:
        target_kind = 'boss_general'
        basis.append('general_boss_damage_column')
    if (target_kind == 'general' and enemy_name not in GENERAL_STATS
            or target_kind == 'boss_general' and enemy_name not in BOSS_GENERALS
            or target_kind not in {'general', 'boss_general', 'egg_monster', 'boss_monster'}):
        target_kind = 'unknown'

    row = CARDS.get(card)
    result = {'card': card, 'target_kind': target_kind,
              'raw_damage_min': None, 'enemy_soldier_hp_upper': None,
              'damage_lower_bound': None, 'remaining_hp_upper': None,
              'lethal': None, 'effect_kind': 'unknown',
              'self_harm': card in {'デッドガン', 'バグストーム', 'ファバード', 'ブラックホール'},
              'ally_soldiers_lower': own_lower, 'enemy_soldiers_upper': enemy_upper,
              'basis': basis}
    if row is None or target_kind == 'unknown':
        basis.append('unknown_card' if row is None else 'unknown_target')
        return result

    is_general = target_kind in {'general', 'boss_general'}
    soldier_hp = (GENERAL_BOSS_SOLDIER_HP[enemy_name]
                  if target_kind == 'boss_general' else GENERAL_SOLDIER_HP)
    pool = enemy_upper * soldier_hp if is_general else 0
    result['enemy_soldier_hp_upper'] = pool
    if is_general:
        basis.append('remaining_soldiers_at_full_hp')

    effect = 'fixed'
    if card == 'デッドガン':
        result['effect_kind'] = 'mutual_annihilation'
        basis.append('not_a_safe_single_target_kill')
        return result
    if card in {'クースカン', 'バグストーム'} and is_general:
        result['effect_kind'] = ('half_enemy_hp' if card == 'クースカン' else 'half_both_hp')
        # The bonus can apply before or after halving; omit it from the
        # guaranteed amount.  Never infer a soldier-HP reduction in live state.
        result['damage_lower_bound'] = hp // 2 if hp is not None else None
        result['remaining_hp_upper'] = (hp + 1) // 2 if hp is not None else None
        result['lethal'] = False if hp is not None else None
        basis.append('half_hp_rounded_up_ignore_uncertain_bonus')
        return result
    if card == 'バルムンク' and target_kind == 'egg_monster':
        result.update(effect_kind='instant_kill', damage_lower_bound=hp,
                      remaining_hp_upper=0 if hp is not None else None,
                      lethal=True if hp is not None else None)
        basis.append('egg_monster_instant_kill_not_endure_damage')
        return result
    if card == 'ノリウツール':
        effect = 'hp_scaled'
        if target_kind == 'general':
            raw = own_hp // 2 if own_hp is not None else None
        elif target_kind == 'boss_general':
            # Boss formula: 8 + one quarter of the user's current HP.
            raw = 8 + own_hp // 4 if own_hp is not None else 8
        else:
            # Monster-type bosses lack a primary example establishing the
            # HP-scaled addition.  Do not use it to promise a lethal hit.
            raw = row['boss_damage' if target_kind == 'boss_monster' else 'monster_damage']
        basis.append('current_ally_hp_scaling' if own_hp is not None else 'ally_hp_unknown')
    else:
        column = ('boss_damage' if target_kind in {'boss_general', 'boss_monster'}
                  else 'monster_damage' if target_kind == 'egg_monster' else 'general_damage')
        bonus = (own_lower * row['soldier_damage']
                 if target_kind in {'general', 'egg_monster'} else 0)
        raw = row[column] + bonus
        basis.append('boss_damage_no_soldier_bonus' if target_kind in {'boss_general', 'boss_monster'}
                     else 'base_plus_current_ally_soldiers')
    if card == 'エンジェリン':
        effect = 'heal'
    elif card == 'シュプレボイス':
        effect = 'retreat'
    elif card in {'ファイアーボイス', 'ブラックホール'}:
        effect = 'soldier_clear'
        # Clear/damage order is not established: subtract the original pool.
        basis.append('do_not_preapply_soldier_clear')
    elif card == 'クースカン':
        effect = 'damage_and_stun'
    result.update(raw_damage_min=raw, effect_kind=effect)
    if raw is None:
        return result
    damage = max(0, raw - pool)
    if not is_general:
        # Monster/boss monster food-endure is not a general-boss rule: the
        # Prince chart kills HP25 + one HP25 soldier with exactly 50 damage.
        if hp is None:
            basis.append('endure_needs_current_hp')
            return result
        if hp <= damage <= hp + ENDURE_HEADROOM:
            damage = hp - 1
            basis.append('monster_endure_leaves_one_hp')
    result['damage_lower_bound'] = damage
    if hp is not None:
        result['remaining_hp_upper'] = max(0, hp - damage)
        result['lethal'] = damage >= hp
    return result


def enemy_egg_likely(card_ids: list[int]) -> bool:
    """True when a summon's card ID sum meets the gcgx enemy-egg rule."""
    return sum(card_ids) >= ENEMY_EGG_CARD_ID_SUM


def egg_drop_threshold(max_hp_sum: int) -> int:
    """Minimum 卵落 value required to drop an egg given combined max HPs."""
    return max_hp_sum % EGG_DROP_MOD + 1


def egg_drop_plan(enemy_max_hp: int, ally_max_hp: int) -> dict:
    """The egg-drop arithmetic the sortie and battle layers share (#1553).

    ``卵落 > (敵・味方将軍の最大HP合計) mod 16`` で敵の卵が落ちる (gcgx
    kirihuda)。出撃時の携行選択と戦闘時の ``battle_egg_plan`` が同じ
    ``max_hp_sum`` / ``threshold`` を算出できるように一括する。最大HPが読め
    ない側がある時は呼び出し側が fail-closed で使わない。
    """
    total = enemy_max_hp + ally_max_hp
    return {'max_hp_sum': total, 'threshold': egg_drop_threshold(total)}


def egg_drop_value(card: str) -> int | None:
    """The card's 卵落 value, or None when it is outside the measured table."""
    return EGG_DROP_VALUES.get(card)


def egg_drop_remainder(max_hp_sum: int) -> int:
    """The max-HP-sum value the 卵落 comparison uses: sum mod 16 (#2364).

    The display names this ``余り``; ``egg_drop_threshold`` is its +1 form
    (the smallest 卵落 that passes). Keeping both avoids teaching the card an
    off-by-one rule: equality with the remainder does not drop the egg.
    """
    return max_hp_sum % EGG_DROP_MOD


def card_drop_prediction(card: str, max_hp_sum: int | None) -> dict:
    """One card's display/decision prediction from the shared 卵落 arithmetic.

    ``drops`` is None whenever a required input is unknown; a missing value is
    never filled with 0. The battle tactics use ``can_drop_egg`` on the same
    inputs, so the card cannot show a different verdict from the decision.
    """
    value = EGG_DROP_VALUES.get(card)
    if type(max_hp_sum) is not int or max_hp_sum <= 0 or value is None:
        return {'name': card, 'id': ALL_CARD_IDS.get(card), 'egg_drop': value,
                'drops': None}
    return {'name': card, 'id': ALL_CARD_IDS.get(card), 'egg_drop': value,
            'drops': value > egg_drop_remainder(max_hp_sum)}


def can_drop_egg(card: str, max_hp_sum: int) -> bool:
    """True when this card's 卵落 exceeds the HP-sum remainder (gcgx rule).

    ``卵落 > 敵・味方将軍の最大HP合計 mod 16`` drops the enemy's egg, which
    makes its summons unusable for the rest of the battle.
    """
    return card_drop_prediction(card, max_hp_sum)['drops'] is True


def egg_droppers(cards, max_hp_sum: int) -> list[str]:
    """This kit's cards that drop this general's egg, strongest 卵落 first.

    将軍 (最大HP合計) と切り札 (卵落) の組み合わせで候補を絞る。判明しない札は
    候補にしない (fail-closed)。同値は ID の小さい札が先 (携行ID予算を守りやすい)。
    """
    return sorted((card for card in dict.fromkeys(cards) if can_drop_egg(card, max_hp_sum)),
                  key=lambda card: (-EGG_DROP_VALUES[card], ALL_CARD_IDS.get(card, 99), card))


def egg_drop_prediction(enemy_name: str | None, ally_name: str | None) -> dict:
    """卵落予測の完全な計算結果。右ステータス表示と戦術で共用する。

    threshold = (敵最大HP + 味方最大HP) % 16
    成立条件 = 札の卵落値 > threshold

    最大HPが不明な場合は threshold=None で返す (fail-closed)。
    0で穴埋めしない。
    """
    e_hp = general_max_hp(enemy_name)
    a_hp = general_max_hp(ally_name)
    if e_hp is None or a_hp is None:
        return {'enemy_max_hp': e_hp, 'ally_max_hp': a_hp,
                'max_hp_sum': None, 'threshold': None, 'droppers': [],
                '判定不能': True, '理由': 'max_hp_unknown'}
    total = e_hp + a_hp
    threshold = egg_drop_remainder(total)
    return {'enemy_max_hp': e_hp, 'ally_max_hp': a_hp,
            'max_hp_sum': total, 'threshold': threshold, 'droppers': [],
            '判定不能': False, '理由': None}


def card_egg_drop_info(card: str, enemy_name: str | None,
                       ally_name: str | None) -> dict:
    """札ごとの卵落判定。表示と戦術で同じ計算結果を使う。

    「計算上落とせる」「敵が現在卵を持つ/使える」「実際に落とした」を区別する。
    この関数は計算上の予測のみ。実際の使用・落下は呼び出し側が別途管理。
    """
    pred = egg_drop_prediction(enemy_name, ally_name)
    verdict = card_drop_prediction(card, pred['max_hp_sum'])
    value = verdict['egg_drop']
    if verdict['drops'] is None:
        return {**pred, 'card': card, '卵落値': value, 'drops': None,
                '状態': '判定不能'}
    drops = verdict['drops']
    return {**pred, 'card': card, '卵落値': value, 'drops': drops,
            '状態': '計算上落とせる' if drops else '不成立'}


def right_status_data(enemy_name: str | None, ally_name: str | None,
                      held_cards: list[str] | None = None,
                      carried_cards: list[str] | None = None) -> dict:
    """右ステータスオーバーレイ表示用データを集約する。

    敵・味方将軍の詳細、所持切り札、卵落予測をまとめる。
    held_cards: 現在の所持品（在庫全体）。carried_cards: この戦闘へ携行していて
    実際に使える札。carried_cards が None は「携行不明」であり、在庫を携行済みや
    使用候補へ昇格させない (携行 None・droppers 空)。空リストは「携行なし」。
    """
    pred = egg_drop_prediction(enemy_name, ally_name)
    held = list(held_cards or [])
    carried = None if carried_cards is None else list(carried_cards)
    droppers = (egg_droppers(carried, pred['max_hp_sum'])
                if carried is not None and pred['max_hp_sum'] is not None else [])
    cards_info = []
    for card in dict.fromkeys(held):
        info = card_egg_drop_info(card, enemy_name, ally_name)
        info['携行'] = None if carried is None else card in carried
        cards_info.append(info)
    return {
        'enemy': general_full_view(enemy_name),
        'ally': general_full_view(ally_name),
        'egg_drop': pred,
        'droppers': droppers,
        'cards': cards_info,
    }


# 将軍詳細 (gcgx shogun.html 2026-10-20 取得)。
# GENERAL_STATS にない項目を補う。対抗は召喚応答確率、得意は季節・地形。
# 右ステータスオーバーレイ表示用。
GENERAL_DETAILS: dict[str, tuple[str, str, str, str]] = {
    name: (f'{counter}%', aptitude or '－', personality, hobby)
    for name, (counter, aptitude, personality, hobby) in GENERAL_TRAITS.items()
}


def general_details(name: str | None) -> dict | None:
    """将軍の詳細データ (対抗、得意、性格、趣味)。無ければ None。"""
    if name is None:
        return None
    row = GENERAL_DETAILS.get(name)
    if row is None:
        return None
    return {'name': name, '対抗': row[0], '得意': row[1],
            '性格': row[2], '趣味': row[3]}


def general_full_view(name: str | None) -> dict | None:
    """将軍の完全な表示データ。GENERAL_STATS + GENERAL_DEBUT + GENERAL_DETAILS。

    右ステータスオーバーレイ表示用。最大HP・戦闘・対抗確率・思考・
    エッグ種類・卵補正・得意・初登場話数・性格・趣味をまとめる。
    """
    if name is None:
        return None
    stats = general_strength(name)
    hp = general_max_hp(name)
    debut = general_debut_chapter(name)
    details = general_details(name)
    if stats is None and hp is None and details is None:
        return None
    result: dict = {'name': name}
    if stats is not None:
        result['戦闘'] = stats[1]
        result['卵補正'] = stats[2]
        result['エッグ種類'] = stats[3]
        result['思考'] = stats[4]
    if hp is not None:
        result['最大HP'] = hp
    if debut is not None:
        result['初登場話数'] = debut
    if details is not None:
        result.update({'対抗': details['対抗'], '得意': details['得意'],
                       '性格': details['性格'], '趣味': details['趣味']})
    return result


# 強い将軍の主判定 (owner 2026-10-03: 「まずは戦闘の値とHPでわかる」)。
STRONG_GENERAL_COMBAT_GAP = 3      # 敵の戦闘が味方をこの値以上上回る
STRONG_GENERAL_HP_GAP = 20         # 敵の最大HPが味方をこの値以上上回る


def general_strength(name: str | None):
    """gcgx shogun.html の (HP, 戦闘, 補正, エッグ種別, 思考)。表に無ければ None。"""
    return GENERAL_STATS.get(name) if name else None


def general_profile(name: str | None) -> dict | None:
    """A general's display record from the static tables (#2364).

    Nine requested fields plus the name: 戦闘・対抗・思考・エッグ種類・卵補正・
    得意・初登場話数・性格・趣味, and the fixed max HP. Existing tables remain
    the single source: GENERAL_STATS (HP/戦闘/補正/エッグ/思考), GENERAL_TRAITS
    (対抗/得意/性格/趣味), GENERAL_DEBUT (初登場話数), GENERAL_EGGS (卵の有無).
    An unlisted name returns None instead of a zero-filled profile, so callers
    can show 判定不能 rather than inventing stats.
    """
    if not isinstance(name, str) or name not in GENERAL_STATS:
        return None
    stats, traits = GENERAL_STATS.get(name), GENERAL_TRAITS.get(name)
    if stats is None or traits is None:
        return None
    from .hanjuku_egg_reference import GENERAL_DEBUT, GENERAL_EGGS
    hp, combat, egg_bonus, egg_type, thought = stats
    counter, aptitude, personality, hobby = traits
    return {'name': name, 'max_hp': hp, 'combat': combat, 'counter': counter,
            'thought': thought, 'egg_type': egg_type, 'egg_bonus': egg_bonus,
            'aptitude': aptitude, 'debut': GENERAL_DEBUT.get(name),
            'personality': personality, 'hobby': hobby,
            'has_egg': (GENERAL_EGGS.get(name) or (None, None))[0]}


# 強制解雇で選ぶ「弱い将軍」の順序 (Issue #2367、owner 2026-10-20 20:45 採用)。
# 採用した順序:
#   1. 卵なし (静的なエッグ種別 '－')
#   2. HP × 戦闘 が小さい (HP は最大HP。現在HPの負傷は使わない)
#   3. 騎馬でない (GENERAL_HORSES に無い)
#   4. 最大HP が低い
#   5. 戦闘が低い
#   6. 名前 (同値時の決定的な最終順)
# 卵種別どうしの優劣は未確定のため順位に使わない。未読・残回数0・破損は
# ここでは扱わない (静的な種別だけを見るので「卵なし」と混同しない)。
# HP×戦闘はオーナー提案の候補式であり、この順序をテストで固定する。
def general_weakness(name: str | None):
    """弱い順の比較キー。静的データに無い名前は None (順位を確定しない)。"""
    # 一覧の表示名 `どうし` は静的データでは `しゅじんこう`。賃金表
    # (`fixed_wage`) と撤退判定 (`judgment.retreat`) と同じ別名規則を、
    # ここでも一箇所だけ適用する。他の将軍のキーは変わらない。
    canonical = 'しゅじんこう' if name == 'どうし' else name
    stats = general_strength(canonical)
    hp = general_max_hp(canonical)
    if stats is None or hp is None:
        return None
    max_hp, combat, _bonus, egg, _thought = stats
    if max_hp != hp:
        return None  # 二つの静的HP表が食い違う名前は比較しない
    eggless = egg == '－'
    return (0 if eggless else 1, max_hp * combat,
            1 if (canonical in GENERAL_HORSES) else 0, max_hp, combat, canonical)


def weakest_general(names):
    """候補一覧から最も弱い将軍を選ぶ。未知名が1つでもあれば確定しない。

    返り値は (name, evidence)。name が None のときは、未知名や空の一覧を
    evidence['unresolved'] に残し、既定カーソルを最弱とみなさない。
    """
    evidence = {'basis': 'eggless,hp_x_combat,not_mounted,max_hp,combat,name',
                'candidates': [], 'unresolved': []}
    if (not isinstance(names, (list, tuple)) or not names
            or len(set(names)) != len(names)):
        evidence['unresolved'] = ['empty_or_duplicate_list']
        return None, evidence
    ranked = []
    for name in names:
        key = general_weakness(name) if isinstance(name, str) else None
        if key is None:
            evidence['unresolved'].append(name)
            continue
        ranked.append((key, name))
    evidence['candidates'] = [{'name': name, 'key': list(key)} for key, name in ranked]
    if evidence['unresolved'] or len(ranked) != len(names):
        return None, evidence
    ranked.sort()
    return ranked[0][1], evidence


def strong_general(enemy: str | None, ally: str | None):
    """「強い将軍」判定。返り値は (verdict, evidence)。

    主判定は戦闘と最大HP (gcgx shogun.html)。敵が戦闘を3以上、または最大HPを20
    以上上回る、あるいは戦闘・HPの両方で味方を上回れば強い。逆に味方が両方で
    上回れば弱い。

    どちらが優勢か割れた・同値のときは、owner の指名した補助値を
    補正 → エッグ種別 → 思考 の順に見て決める。

    verdict は bool または None。None はどちらかが表に無く判定不能 (後期ボス等)。
    積極利用の根拠にはせず、evidence['rule'] == 'unknown' を記録する。
    """
    e = general_strength(enemy)
    a = general_strength(ally)
    evidence = {'rule': 'unknown', 'enemy': _strength_dict(enemy, e),
                'ally': _strength_dict(ally, a)}
    if e is None or a is None:
        return None, evidence
    e_hp, e_combat, e_bonus, e_egg, e_thought = e
    a_hp, a_combat, a_bonus, a_egg, a_thought = a
    if (e_combat - a_combat >= STRONG_GENERAL_COMBAT_GAP
            or e_hp - a_hp >= STRONG_GENERAL_HP_GAP
            or (e_combat > a_combat and e_hp > a_hp)):
        rule = ('combat' if e_combat - a_combat >= STRONG_GENERAL_COMBAT_GAP
                else 'hp' if e_hp - a_hp >= STRONG_GENERAL_HP_GAP else 'dominant')
        evidence['rule'] = rule
        return True, evidence
    if (a_combat - e_combat >= STRONG_GENERAL_COMBAT_GAP
            or a_hp - e_hp >= STRONG_GENERAL_HP_GAP
            or (e_combat < a_combat and e_hp < a_hp)):
        evidence['rule'] = 'weaker'
        return False, evidence
    # 主判定が割れた: owner の補助値を順に見る。
    if _bonus_value(e_bonus) != _bonus_value(a_bonus):
        evidence['rule'] = 'bonus'
        return _bonus_value(e_bonus) > _bonus_value(a_bonus), evidence
    if (e_egg != '－') != (a_egg != '－'):
        evidence['rule'] = 'egg'
        return e_egg != '－', evidence
    evidence['rule'] = 'thought'
    return e_thought >= a_thought, evidence


def _bonus_value(raw: str) -> int:
    """半熟レベル補正。季節付き ('春-1') も符号だけを取り出す。"""
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return 0
    return -int(digits) if '-' in raw else int(digits)


def _strength_dict(name: str | None, row) -> dict | None:
    if row is None:
        return {'name': name}
    hp, combat, bonus, egg, thought = row
    return {'name': name, 'hp': hp, 'combat': combat, 'bonus': bonus,
            'egg': egg, 'thought': thought}
