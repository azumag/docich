"""Chart data for the deterministic Hanjuku bot.

Source: azumag/hanjuku-sfc-speedrun ``charts/1.md``–``12.md`` and
``overview.md``, plus gamecentergx castle/boss tables (gcgx). Castle
coordinates are map-cursor cells measured in an isolated emulator from each
castle's roof anchor; they are chart facts, not live RAM reads. The hero is
the name the bot enters (``HERO``).

Chapter 1 coordinates are measured. Chapters 2–12 ship with empty
``CASTLES`` until ``hanjuku_measure`` records them from labeled frames:
``orders()`` returns nothing for an unmeasured chapter (``chart_unavailable``),
so navigation never guesses coordinates. Orders, tactics and purchases for
those chapters are encoded and activate only when their source/target castles
are present in ``CASTLES``.
"""
from __future__ import annotations

HERO = 'どうし'

# Map-cursor cells that select each castle (top-left of the 16x16 cursor).
# Filled per chapter by measurement; empty dict means the chapter is gated.
CASTLES: dict[int, dict[str, tuple[int, int]]] = {
    1: {
        'ほんじょう': (731, 805),
        'キカンドン': (567, 725),
        'ナキューメラ': (711, 645),
        'ジョンリギ': (551, 574),
        'ゴーメン': (295, 606),
        'スペンソニア': (391, 382),
        'カストーラ': (599, 382),
        'けっかい': (265, 270),
    },
    # 2–12: measured at runtime / via hanjuku_measure; intentionally empty.
}

# Expected on-screen castle labels per chapter (gcgx + charts). Navigation
# still requires CASTLES; this list documents names for measurement and
# order encoding.
CASTLE_NAMES: dict[int, tuple[str, ...]] = {
    1: ('ほんじょう', 'キカンドン', 'ナキューメラ', 'ジョンリギ',
        'ゴーメン', 'スペンソニア', 'カストーラ', 'けっかい'),
    2: ('アルマムーン', 'ハドリバーグ', 'フーリック', 'グロン',
        'ドミノーラ', 'ウラノポリス', 'スペランザ', 'アウスパジア', 'ボス'),
    3: ('アルマムーン', 'グリン', 'コラダイン', 'イーピヨル', 'ドンダム',
        'メガパタ', 'パタゴーヌ', 'アバロン', 'リグア', 'ボス'),
    4: ('アルマムーン', 'マラコビア', 'フヌイヌム', 'ネクスドリア',
        'ビクトリア', 'ミヒラギアン', 'アルカ', 'ボス'),
    5: ('アルマムーン', 'オトラント', 'クロチェット', 'カルーン', 'ジュメルズ',
        'テラビル', 'ヒュペルボレア', 'ボッサール', 'アイアイエー',
        'バスカビル', 'プトレマイス', 'ボス'),
    6: ('アルマムーン', 'アルブラーカ', 'イカリア', 'カバルッサ', 'オケアナ',
        'バビラリー', 'ダーラント', 'セレーネ', 'グルーポフ', 'ユージア',
        'アブダレ', 'ボス'),
    7: ('アルマムーン', 'ベンゴディ', 'レオナール', 'ロキュータ',
        'リデンブロック', 'マンシー', 'プルートー', 'ハシオクラム',
        'フィロメラ', 'ボス'),
    8: ('あるまむーん', 'おかざき', 'しらかわ', 'えど', 'おだわら',
        'ながはま', 'いちじょうだに', 'しまばら', 'まつやま', 'ひめじ', 'ボス'),
    9: ('アルマムーン', 'ハヤク', 'タオ', 'サヌト', 'オシロガ', 'ドンドン',
        'オオキク', 'ナッテ', 'シマウ', 'カラ', 'イソ', 'イデ！', 'ボス'),
    10: ('アルマムーン', 'マカリア', 'ピランドリア', 'トルストリア', 'ファルゲ',
         'コッカーニュ', 'カルカル', 'カロナック', 'ケントルム', 'ジュアム', 'ボス'),
    11: ('アルマムーン', 'チリベット', 'タプロベイン', 'ジャンセニア', 'ゴンダル',
         'マランマ', 'タタール', 'クラボニア', 'トリフェ', 'アエンタール',
         'ネペンテ', 'ボス'),
    12: ('アルマムーン', 'ワフェルダ', 'ブラックランド', 'バラタリア', 'ラグナグ',
         'ユーフォニア', 'アファニア', 'ヘリオポリス', 'ディランダ', 'ボス'),
}

HOME_CASTLES: dict[int, str] = {
    1: 'ほんじょう',
    2: 'アルマムーン', 3: 'アルマムーン', 4: 'アルマムーン',
    5: 'アルマムーン', 6: 'アルマムーン', 7: 'アルマムーン',
    8: 'あるまむーん',
    9: 'アルマムーン', 10: 'アルマムーン', 11: 'アルマムーン',
    12: 'アルマムーン',
}

# Navigation key for the boss castle. On-screen boss entry text is always
# 「ボスじょう」 (special-cased in message_step); the map cell label used by
# CASTLES/orders is per-chapter. Chapter 1's measured cell is けっかい.
BOSS_CASTLES: dict[int, str] = {
    1: 'けっかい',
    2: 'ボス', 3: 'ボス', 4: 'ボス', 5: 'ボス', 6: 'ボス',
    7: 'ボス', 8: 'ボス', 9: 'ボス', 10: 'ボス', 11: 'ボス', 12: 'ボス',
}

# Orders. ``after`` is the event that unlocks an order: None at chapter start,
# ('captured', castle) after a verified win at that castle, or
# ('all_captured',) once every non-boss castle has been won.
# Source/target names must match CASTLES keys (on-screen labels) for navigation.
CHAPTER_1_ORDERS = (
    {'step': '1-A1', 'general': HERO, 'source': 'ほんじょう', 'cards': (),
     'target': 'キカンドン', 'after': None,
     'note': '主人公切り札なし 左:キカンドン VSミント 白兵'},
    {'step': '1-V1', 'general': 'ヴィーナス', 'source': 'ほんじょう', 'cards': (),
     'target': 'ナキューメラ', 'after': None,
     'note': 'ヴィーナス切り札なし 右上:ナキューメラ コリアンダー 白兵'},
    {'step': '1-C1', 'general': 'ココット', 'source': 'ほんじょう', 'cards': (),
     'target': 'ジョンリギ', 'after': None,
     'note': 'ココット切り札なし 左上:ジョリンギ 白兵'},
    {'step': '1-A2', 'general': HERO, 'source': 'キカンドン', 'cards': ('フットバース',),
     'target': 'ゴーメン', 'after': ('captured', 'キカンドン'),
     'note': 'フットバースを持ち左ゴーメンへ 放置で白兵'},
    {'step': '1-V2', 'general': 'ヴィーナス', 'source': 'ナキューメラ', 'cards': ('フットバース',),
     'target': 'カストーラ', 'after': ('captured', 'ナキューメラ'),
     'note': 'フットバースを持って上カストーラへ 放置で白兵'},
    {'step': '1-C2', 'general': 'ココット', 'source': 'ジョンリギ',
     'cards': ('ダイチスイム', 'ダイチスイム', 'ブラッキー'),
     'target': 'スペンソニア', 'after': ('captured', 'ジョンリギ'),
     'note': 'ダイチスイムx2 ブラッキー 左上のスペンソニアへ'},
    {'step': '1-A3', 'general': HERO, 'source': 'ゴーメン', 'cards': (),
     'target': 'スペンソニア', 'after': ('captured', 'ゴーメン'),
     'note': 'スペンソニアに向かう(セーブはbot対象外)'},
    {'step': '1-B1', 'general': HERO, 'source': 'スペンソニア', 'cards': ('クースカン', 'ノリウツール'),
     'target': 'けっかい', 'after': ('all_captured',),
     'note': 'クースカン ノリウツールを持ちボス城へ 途中敵は無視'},
)

_H2 = HOME_CASTLES[2]
CHAPTER_2_ORDERS = (
    {'step': '2-Z1', 'general': 'ゼウス', 'source': _H2, 'cards': (),
     'target': 'フーリック', 'after': None,
     'note': 'ゼウス切り札なし フーリックへ 対ミュスカデ白兵'},
    {'step': '2-C1', 'general': 'ココット', 'source': _H2,
     'cards': ('フットバース', 'イッテツーン', 'イッテツーン'),
     'target': 'ドミノーラ', 'after': None,
     'note': 'ココット フットバースとイッテツーンx2でドミノーラへ 対エピィ'},
    {'step': '2-V1', 'general': 'ヴィーナス', 'source': _H2, 'cards': (),
     'target': 'ハドリバーグ', 'after': None,
     'note': 'ヴィーナス切り札なし ハドリバーグへ 対ポワソン白兵'},
    {'step': '2-S1', 'general': HERO, 'source': _H2,
     'cards': ('イッテツーン', 'イッテツーン'),
     'target': 'スペランザ', 'after': None,
     'note': '主人公 イッテツーンx2でスペランザへ 対デーメーテール白兵'},
    {'step': '2-Z2', 'general': 'ゼウス', 'source': 'フーリック',
     'cards': ('イッテツーン', 'イッテツーン', 'ブラッキー'),
     'target': 'ウラノポリス', 'after': ('captured', 'フーリック'),
     'note': 'ゼウス イッテツーンx2 ブラッキーでウラノポリスへ'},
    {'step': '2-V2', 'general': 'ヴィーナス', 'source': 'ハドリバーグ',
     'cards': ('イッテツーン', 'イッテツーン'),
     'target': 'グロン', 'after': ('captured', 'ハドリバーグ'),
     'note': 'ヴィーナス イッテツーンx2でグロン城へ 対セミヨン'},
    {'step': '2-Z3', 'general': 'ゼウス', 'source': 'ウラノポリス', 'cards': (),
     'target': 'アウスパジア', 'after': ('captured', 'ウラノポリス'),
     'note': 'ゼウス アウスパジアへ道なりに 対ユイートル白兵'},
    {'step': '2-B1', 'general': HERO, 'source': 'スペランザ',
     'cards': ('クースカン', 'イッテツーン', 'ノリウツール'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': '主人公 クースカン イッテツーン ノリウツールでボス城へ にせヒーロー'},
)

_H3 = HOME_CASTLES[3]
CHAPTER_3_ORDERS = (
    {'step': '3-Z1', 'general': 'ゼウス', 'source': _H3, 'cards': (),
     'target': 'グリン', 'after': None,
     'note': 'ゼウス切り札なし グリン城へ 対ポセイドン白兵'},
    {'step': '3-C1', 'general': 'ココット', 'source': _H3,
     'cards': ('グリンボー', 'グリンボー', 'ミックミー'),
     'target': 'メガパタ', 'after': None,
     'note': 'ココット メガパタ城へ 対ヘスティア・キール'},
    {'step': '3-V1', 'general': 'ヴィーナス', 'source': _H3,
     'cards': ('イッテツーン', 'グリンボー', 'グリンボー'),
     'target': 'コラダイン', 'after': None,
     'note': 'ヴィーナス コラダイン城へ 対ペコリーノ白兵→HP68でグリンボーx2'},
    {'step': '3-A1', 'general': HERO, 'source': _H3,
     'cards': ('イッテツーン', 'グリンボー', 'グリンボー'),
     'target': 'パタゴーヌ', 'after': ('captured', 'コラダイン'),
     'note': '主人公 コラダインからパタゴーヌ城へ 対ジェラート'},
    {'step': '3-V2', 'general': 'ヴィーナス', 'source': 'コラダイン',
     'cards': ('イッテツーン', 'グリンボー', 'ミックミー'),
     'target': 'イーピヨル', 'after': ('captured', 'コラダイン'),
     'note': 'ヴィーナス イーピヨル城へ'},
    {'step': '3-C2', 'general': 'ココット', 'source': 'メガパタ',
     'cards': ('イッテツーン', 'グリンボー', 'グリンボー'),
     'target': 'アバロン', 'after': ('captured', 'メガパタ'),
     'note': 'ココット アバロン城へ 対レモン'},
    {'step': '3-V3', 'general': 'ヴィーナス', 'source': 'イーピヨル',
     'cards': ('イッテツーン', 'グリンボー', 'ミックミー'),
     'target': 'ドンダム', 'after': ('captured', 'イーピヨル'),
     'note': 'ヴィーナス ドンダム城へ'},
    {'step': '3-Z2', 'general': 'ゼウス', 'source': 'グリン',
     'cards': ('イッテツーン', 'グリンボー', 'グリンボー'),
     'target': 'リグア', 'after': ('captured', 'グリン'),
     'note': 'ゼウス リグア城2マス右上でキャンプ 対ライム白兵'},
    {'step': '3-B1', 'general': HERO, 'source': 'パタゴーヌ',
     'cards': ('クースカン', 'クースカン', 'ゼンマイン'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': '主人公 クースカンx2 ゼンマインでボス城へ プリンス'},
)

_H4 = HOME_CASTLES[4]
CHAPTER_4_ORDERS = (
    {'step': '4-C1', 'general': 'ココット', 'source': _H4,
     'cards': ('グリンボー', 'グリンボー', 'ファバード'),
     'target': 'アルカ', 'after': None,
     'note': 'ココット アルカ城へ 対ラビオリ ファバード迎撃'},
    {'step': '4-A1', 'general': HERO, 'source': _H4,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ミヒラギアン', 'after': None,
     'note': '主人公 ミヒラギアン城攻略'},
    {'step': '4-V1', 'general': 'ヴィーナス', 'source': _H4,
     'cards': ('エンジェリン', 'マグネガキン', 'ファバード'),
     'target': 'ビクトリア', 'after': None,
     'note': 'ヴィーナス 右下ボス方面の城へ'},
    {'step': '4-Z1', 'general': 'ゼウス', 'source': _H4,
     'cards': ('エンジェリン', 'マグネガキン', 'ファバード'),
     'target': 'ネクスドリア', 'after': None,
     'note': 'ゼウス 卵あり 右上方面へ'},
    {'step': '4-C2', 'general': 'ココット', 'source': 'アルカ',
     'cards': ('ファバード', 'マグネガキン', 'マグネガキン'),
     'target': 'フヌイヌム', 'after': ('captured', 'アルカ'),
     'note': 'ココット 左上ボス城方面へ'},
    {'step': '4-A2', 'general': HERO, 'source': 'ミヒラギアン',
     'cards': ('エンジェリン', 'マグネガキン', 'ファバード'),
     'target': 'マラコビア', 'after': ('captured', 'ミヒラギアン'),
     'note': '主人公 左下ボス城方面へ'},
    {'step': '4-B1', 'general': HERO, 'source': 'マラコビア',
     'cards': ('エンジェリン', 'マグネガキン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': '四季王（季節ボス）へ 乗り込み前にセーブ前提はbot対象外'},
)

_H5 = HOME_CASTLES[5]
CHAPTER_5_ORDERS = (
    {'step': '5-C1', 'general': 'ココット', 'source': _H5,
     'cards': ('グリンボー', 'グリンボー', 'ファバード'),
     'target': 'テラビル', 'after': None,
     'note': 'ココット テラビル城へ 対マスカット'},
    {'step': '5-K1', 'general': '騎馬A', 'source': _H5,
     'cards': ('グリンボー', 'グリンボー', 'ファバード'),
     'target': 'ヒュペルボレア', 'after': None,
     'note': '騎馬A ヒュペルボレア城へ 対マンゴスチン ファバード'},
    {'step': '5-Z1', 'general': 'ゼウス', 'source': _H5, 'cards': ('ファバード',),
     'target': 'オトラント', 'after': None,
     'note': 'ゼウス オトラント城へ 対グレナデン ファバード'},
    {'step': '5-A1', 'general': HERO, 'source': _H5,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'カルーン', 'after': None,
     'note': '主人公 カルーン城へ'},
    {'step': '5-K2', 'general': '騎馬A', 'source': 'ヒュペルボレア',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ボッサール', 'after': ('captured', 'ヒュペルボレア'),
     'note': '騎馬A ボッサール城へ'},
    {'step': '5-Z2', 'general': 'ゼウス', 'source': 'オトラント',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'クロチェット', 'after': ('captured', 'オトラント'),
     'note': 'ゼウス クロチェット城へ 対ランプータン'},
    {'step': '5-C2', 'general': 'ココット', 'source': 'テラビル',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'アイアイエー', 'after': ('captured', 'テラビル'),
     'note': 'ココット アイアイエー城へ'},
    {'step': '5-A2', 'general': HERO, 'source': 'カルーン',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ジュメルズ', 'after': ('captured', 'カルーン'),
     'note': '主人公 ジュメルズ城へ 対ドリアン'},
    {'step': '5-C3', 'general': 'ココット', 'source': 'アイアイエー',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'バスカビル', 'after': ('captured', 'アイアイエー'),
     'note': 'ココット バスカビル城へ 対カシス'},
    {'step': '5-C4', 'general': 'ココット', 'source': 'バスカビル',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'プトレマイス', 'after': ('captured', 'バスカビル'),
     'note': 'ココット プトレマイス城へ 対チキータ'},
    {'step': '5-B1', 'general': 'ココット', 'source': 'プトレマイス',
     'cards': ('マグネガキン', 'マグネガキン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'ココット 完熟大魔王へ 開幕マグネガキン'},
)

_H6 = HOME_CASTLES[6]
CHAPTER_6_ORDERS = (
    {'step': '6-Z1', 'general': 'ゼウス', 'source': _H6, 'cards': ('ファバード',),
     'target': 'アルブラーカ', 'after': None,
     'note': 'ゼウス アルブラーカ城へ 対バタール ファバード'},
    {'step': '6-K1', 'general': '騎馬A', 'source': _H6,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ダーラント', 'after': None,
     'note': '騎馬A ダーラント城へ 対ブリオッシュ'},
    {'step': '6-C1', 'general': 'ココット', 'source': _H6,
     'cards': ('グリンボー', 'グリンボー', 'ファバード'),
     'target': 'バビラリー', 'after': None,
     'note': 'ココット バビラリー速攻 開幕グリンボー2発でマフィン'},
    {'step': '6-A1', 'general': HERO, 'source': _H6,
     'cards': ('グリンボー', 'グリンボー', 'ファバード'),
     'target': 'カバルッサ', 'after': None,
     'note': '主人公 カバルッサ城へ'},
    {'step': '6-A2', 'general': HERO, 'source': 'カバルッサ', 'cards': (),
     'target': 'オケアナ', 'after': ('captured', 'カバルッサ'),
     'note': '主人公 オケアナ城へ'},
    {'step': '6-K2', 'general': '騎馬A', 'source': 'ダーラント', 'cards': (),
     'target': 'セレーネ', 'after': ('captured', 'ダーラント'),
     'note': '騎馬A セレーネ城経由'},
    {'step': '6-Z2', 'general': 'ゼウス', 'source': 'アルブラーカ', 'cards': (),
     'target': 'イカリア', 'after': ('captured', 'アルブラーカ'),
     'note': 'ゼウス イカリア城へ 対バゲット'},
    {'step': '6-C2', 'general': 'ココット', 'source': 'バビラリー',
     'cards': ('グリンボー', 'グリンボー', 'ファバード'),
     'target': 'グルーポフ', 'after': ('captured', 'バビラリー'),
     'note': 'ココット グルーポフ城へ'},
    {'step': '6-K3', 'general': '騎馬A', 'source': 'セレーネ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ユージア', 'after': ('captured', 'セレーネ'),
     'note': '騎馬A ユージア城へ'},
    {'step': '6-A3', 'general': HERO, 'source': 'オケアナ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'アブダレ', 'after': ('captured', 'オケアナ'),
     'note': '主人公 アブダレ城へ'},
    {'step': '6-B1', 'general': 'ヴィーナス', 'source': 'グルーポフ',
     'cards': ('エンジェリン', 'マグネガキン', 'マグネガキン'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'ハードマン→ハードロボ エンジェリンとマグネガキン'},
)

_H7 = HOME_CASTLES[7]
CHAPTER_7_ORDERS = (
    {'step': '7-K1', 'general': '騎馬A', 'source': _H7,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ベンゴディ', 'after': None,
     'note': '騎馬A ベンゴディ城へ 対ショコラ'},
    {'step': '7-C1', 'general': 'ココット', 'source': _H7,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'レオナール', 'after': None,
     'note': 'ココット レオナール城へ 対ブラマンジェ'},
    {'step': '7-A1', 'general': HERO, 'source': _H7, 'cards': ('グリンボー', 'グリンボー', 'グリンボー'),
     'target': 'ベンゴディ', 'after': None,
     'note': '主人公 ベンゴディ城で防衛 グリンボーx3'},
    {'step': '7-K2', 'general': '騎馬A', 'source': 'ベンゴディ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'フィロメラ', 'after': ('captured', 'ベンゴディ'),
     'note': '騎馬A フィロメラ城へ 対プラリネ'},
    {'step': '7-C2', 'general': 'ココット', 'source': 'レオナール',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ロキュータ', 'after': ('captured', 'レオナール'),
     'note': 'ココット ロキュータ城へ 対シフォン'},
    {'step': '7-C3', 'general': 'ココット', 'source': 'ロキュータ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'リデンブロック', 'after': ('captured', 'ロキュータ'),
     'note': 'ココット リデンブロック城へ 対ブラウニー'},
    {'step': '7-K3', 'general': '騎馬A', 'source': 'フィロメラ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'マンシー', 'after': ('captured', 'フィロメラ'),
     'note': '騎馬A マンシー城へ'},
    {'step': '7-C4', 'general': 'ココット', 'source': 'リデンブロック',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'プルートー', 'after': ('captured', 'リデンブロック'),
     'note': 'ココット プルートー城へ 対ミモザ ファバード'},
    {'step': '7-C5', 'general': 'ココット', 'source': 'プルートー',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ハシオクラム', 'after': ('captured', 'プルートー'),
     'note': 'ココット ハシオクラム城へ'},
    {'step': '7-B1', 'general': '騎馬A', 'source': 'マンシー',
     'cards': ('マグネガキン', 'マグネガキン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'ランパイア 特攻用将軍 マグネガキンx2 ファバード'},
)

_H8 = HOME_CASTLES[8]
CHAPTER_8_ORDERS = (
    {'step': '8-K1', 'general': '騎馬A', 'source': _H8,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ながはま', 'after': None,
     'note': '騎馬A ながはま城へ'},
    {'step': '8-K2', 'general': '騎馬B', 'source': _H8,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'しらかわ', 'after': None,
     'note': '騎馬B しらかわ城へ 対グリッシーニ'},
    {'step': '8-A1', 'general': HERO, 'source': _H8,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'おかざき', 'after': None,
     'note': '主人公 おかざき城で防衛 対シュガー'},
    {'step': '8-K3', 'general': '騎馬C', 'source': _H8,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'いちじょうだに', 'after': None,
     'note': '騎馬C いちじょうだに城へ 対コンポート'},
    {'step': '8-K4', 'general': '騎馬B', 'source': 'しらかわ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'えど', 'after': ('captured', 'しらかわ'),
     'note': '騎馬B えど城へ'},
    {'step': '8-K5', 'general': '騎馬A', 'source': 'ながはま',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'おだわら', 'after': ('captured', 'ながはま'),
     'note': '騎馬A おだわら城へ'},
    {'step': '8-K6', 'general': '騎馬C', 'source': 'いちじょうだに',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'しまばら', 'after': ('captured', 'いちじょうだに'),
     'note': '騎馬C しまばら城へ'},
    {'step': '8-K7', 'general': '騎馬B', 'source': 'えど',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'まつやま', 'after': ('captured', 'えど'),
     'note': '騎馬B まつやま城へ'},
    {'step': '8-K8', 'general': '騎馬A', 'source': 'おだわら',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ひめじ', 'after': ('captured', 'おだわら'),
     'note': '騎馬A ひめじ城へ'},
    {'step': '8-B1', 'general': HERO, 'source': 'おかざき',
     'cards': ('ハリケーン', 'ハリケーン', 'ハリケーン'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'ノブナーガ ハリケーンx3（月イチ購入前提）'},
)

_H9 = HOME_CASTLES[9]
CHAPTER_9_ORDERS = (
    {'step': '9-K1', 'general': '騎馬A', 'source': _H9,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ハヤク', 'after': None,
     'note': '騎馬A ハヤク城へ 対エストラゴン'},
    {'step': '9-K2', 'general': '騎馬B', 'source': _H9,
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ハヤク', 'after': None,
     'note': '騎馬B ハヤク城へ 2人攻め'},
    {'step': '9-A1', 'general': HERO, 'source': _H9, 'cards': (),
     'target': _H9, 'after': None,
     'note': '主人公 アルマムーン城で待機'},
    {'step': '9-K3', 'general': '騎馬A', 'source': 'ハヤク',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'タオ', 'after': ('captured', 'ハヤク'),
     'note': '騎馬A タオ城へ 対マーマレード'},
    {'step': '9-K4', 'general': '騎馬B', 'source': 'ハヤク',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'タオ', 'after': ('captured', 'ハヤク'),
     'note': '騎馬B タオ城へ'},
    {'step': '9-K5', 'general': '騎馬A', 'source': 'タオ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'サヌト', 'after': ('captured', 'タオ'),
     'note': '騎馬A サヌト城へ'},
    {'step': '9-K6', 'general': '騎馬B', 'source': 'タオ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'サヌト', 'after': ('captured', 'タオ'),
     'note': '騎馬B サヌト城へ'},
    {'step': '9-K7', 'general': '騎馬A', 'source': 'サヌト',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'オシロガ', 'after': ('captured', 'サヌト'),
     'note': '騎馬A オシロガ城へ'},
    {'step': '9-K8', 'general': '騎馬B', 'source': 'サヌト',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ドンドン', 'after': ('captured', 'サヌト'),
     'note': '騎馬B ドンドン城へ'},
    {'step': '9-K9', 'general': '騎馬A', 'source': 'オシロガ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'オオキク', 'after': ('captured', 'オシロガ'),
     'note': '騎馬A オオキク城へ'},
    {'step': '9-K10', 'general': '騎馬B', 'source': 'ドンドン',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'ナッテ', 'after': ('captured', 'ドンドン'),
     'note': '騎馬B ナッテ城へ'},
    {'step': '9-K11', 'general': '騎馬A', 'source': 'オオキク',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'シマウ', 'after': ('captured', 'オオキク'),
     'note': '騎馬A シマウ城へ'},
    {'step': '9-K12', 'general': '騎馬B', 'source': 'ナッテ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'カラ', 'after': ('captured', 'ナッテ'),
     'note': '騎馬B カラ城へ'},
    {'step': '9-K13', 'general': '騎馬A', 'source': 'シマウ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'イソ', 'after': ('captured', 'シマウ'),
     'note': '騎馬A イソ城へ'},
    {'step': '9-K14', 'general': '騎馬B', 'source': 'カラ',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'イデ！', 'after': ('captured', 'カラ'),
     'note': '騎馬B イデ！城へ'},
    {'step': '9-B1', 'general': '騎馬A', 'source': 'イソ',
     'cards': ('マグネガキン', 'マグネガキン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'ハデス（エグモン） 騎馬のどちらかが攻略'},
)

_H10 = HOME_CASTLES[10]
CHAPTER_10_ORDERS = (
    {'step': '10-K1', 'general': '騎馬A', 'source': _H10, 'cards': (),
     'target': 'マカリア', 'after': None,
     'note': '騎馬A マカリア城へ 対リディア'},
    {'step': '10-K2', 'general': '騎馬B', 'source': _H10, 'cards': (),
     'target': 'ピランドリア', 'after': None,
     'note': '騎馬B ピランドリア城へ'},
    {'step': '10-C1', 'general': 'ココット', 'source': _H10, 'cards': (),
     'target': 'トルストリア', 'after': None,
     'note': 'ココット トルストリア城へ'},
    {'step': '10-A1', 'general': HERO, 'source': _H10,
     'cards': ('クースカン', 'ブラッキー', 'ファバード'),
     'target': 'ファルゲ', 'after': None,
     'note': '主人公 ファルゲ城攻略 対エッジ白兵'},
    {'step': '10-K3', 'general': '騎馬A', 'source': 'マカリア', 'cards': (),
     'target': 'コッカーニュ', 'after': ('captured', 'マカリア'),
     'note': '騎馬A コッカーニュ城へ'},
    {'step': '10-K4', 'general': '騎馬B', 'source': 'ピランドリア', 'cards': (),
     'target': 'カルカル', 'after': ('captured', 'ピランドリア'),
     'note': '騎馬B カルカル城へ'},
    {'step': '10-C2', 'general': 'ココット', 'source': 'トルストリア',
     'cards': ('グリンボー', 'クースカン', 'エンジェリン'),
     'target': 'カロナック', 'after': ('captured', 'トルストリア'),
     'note': 'ココット カロナック方面 対敵将軍迎撃'},
    {'step': '10-A2', 'general': HERO, 'source': 'ファルゲ',
     'cards': ('グリンボー', 'クースカン', 'エンジェリン'),
     'target': 'ケントルム', 'after': ('captured', 'ファルゲ'),
     'note': '主人公 右上方面へ 対敵将軍迎撃'},
    {'step': '10-B1', 'general': HERO, 'source': 'ケントルム',
     'cards': ('エンジェリン', 'マグネガキン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'だいとうりょう 将軍全員で攻略'},
)

_H11 = HOME_CASTLES[11]
CHAPTER_11_ORDERS = (
    {'step': '11-K1', 'general': '騎馬A', 'source': _H11, 'cards': (),
     'target': 'チリベット', 'after': None,
     'note': '騎馬A チリベット城へ'},
    {'step': '11-K2', 'general': '騎馬B', 'source': _H11, 'cards': (),
     'target': 'ゴンダル', 'after': None,
     'note': '騎馬B ゴンダル城へ 対ブリー'},
    {'step': '11-A1', 'general': HERO, 'source': _H11, 'cards': (),
     'target': 'タプロベイン', 'after': None,
     'note': '主人公 タプロベイン城へ'},
    {'step': '11-A2', 'general': HERO, 'source': 'タプロベイン',
     'cards': ('ブラッキー', 'クースカン', 'ファバード'),
     'target': 'タタール', 'after': ('captured', 'タプロベイン'),
     'note': '主人公 タタール城へ 対ハバティー ブラッキー'},
    {'step': '11-K3', 'general': '騎馬A', 'source': 'チリベット', 'cards': (),
     'target': 'マランマ', 'after': ('captured', 'チリベット'),
     'note': '騎馬A マランマ城へ 対サムソー'},
    {'step': '11-K4', 'general': '騎馬B', 'source': 'ゴンダル', 'cards': (),
     'target': 'ジャンセニア', 'after': ('captured', 'ゴンダル'),
     'note': '騎馬B ジャンセニア城へ'},
    {'step': '11-K5', 'general': '騎馬B', 'source': 'ジャンセニア', 'cards': (),
     'target': 'トリフェ', 'after': ('captured', 'ジャンセニア'),
     'note': '騎馬B トリフェ城へ 対マリボー'},
    {'step': '11-A3', 'general': HERO, 'source': 'タタール', 'cards': (),
     'target': 'クラボニア', 'after': ('captured', 'タタール'),
     'note': '主人公 クラボニア城へ 対リゴット'},
    {'step': '11-K6', 'general': '騎馬A', 'source': 'マランマ', 'cards': (),
     'target': 'アエンタール', 'after': ('captured', 'マランマ'),
     'note': '騎馬A アエンタール城へ'},
    {'step': '11-K7', 'general': '騎馬B', 'source': 'トリフェ', 'cards': (),
     'target': 'ネペンテ', 'after': ('captured', 'トリフェ'),
     'note': '騎馬B ネペンテ城へ'},
    {'step': '11-B1', 'general': 'ヴィーナス', 'source': 'ネペンテ',
     'cards': ('エンジェリン', 'マグネガキン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'エッグママ ヴィーナス・主人公・ココットで攻略'},
)

_H12 = HOME_CASTLES[12]
CHAPTER_12_ORDERS = (
    {'step': '12-C1', 'general': 'ココット', 'source': _H12, 'cards': (),
     'target': 'ワフェルダ', 'after': None,
     'note': 'ココット ワフェルダ城へ 対マルガリータ'},
    {'step': '12-K1', 'general': '騎馬A', 'source': _H12, 'cards': (),
     'target': 'ワフェルダ', 'after': None,
     'note': '騎馬A ワフェルダ城へ'},
    {'step': '12-K2', 'general': '騎馬B', 'source': _H12, 'cards': (),
     'target': 'ブラックランド', 'after': None,
     'note': '騎馬B ブラックランド城へ 対ジン'},
    {'step': '12-A1', 'general': HERO, 'source': _H12, 'cards': (),
     'target': 'ブラックランド', 'after': None,
     'note': '主人公 ブラックランド城へ'},
    {'step': '12-C2', 'general': 'ココット', 'source': 'ワフェルダ', 'cards': (),
     'target': 'バラタリア', 'after': ('captured', 'ワフェルダ'),
     'note': 'ココット バラタリア城へ 対カミュ'},
    {'step': '12-K3', 'general': '騎馬A', 'source': 'ワフェルダ', 'cards': (),
     'target': 'アファニア', 'after': ('captured', 'ワフェルダ'),
     'note': '騎馬A アファニア城へ 対マラスキーノ'},
    {'step': '12-K4', 'general': '騎馬B', 'source': 'ブラックランド', 'cards': (),
     'target': 'ラグナグ', 'after': ('captured', 'ブラックランド'),
     'note': '騎馬B ラグナグ城へ'},
    {'step': '12-A2', 'general': HERO, 'source': 'ブラックランド', 'cards': (),
     'target': 'ヘリオポリス', 'after': ('captured', 'ブラックランド'),
     'note': '主人公 ヘリオポリス城へ'},
    {'step': '12-K5', 'general': '騎馬A', 'source': 'アファニア', 'cards': (),
     'target': 'ユーフォニア', 'after': ('captured', 'アファニア'),
     'note': '騎馬A ユーフォニア城へ'},
    {'step': '12-A3', 'general': HERO, 'source': 'ヘリオポリス', 'cards': (),
     'target': 'ディランダ', 'after': ('captured', 'ヘリオポリス'),
     'note': '主人公 ディランダ城を拠点に'},
    {'step': '12-B1', 'general': '騎馬A', 'source': 'ディランダ',
     'cards': ('マグネガキン', 'ハリケーン', 'ファバード'),
     'target': 'ボス', 'after': ('all_captured',),
     'note': 'クーモン→スーモン→ボイルド ボス城攻略'},
)

# Battle tactics keyed by (chart step, enemy name). ``when_hp_at_most`` is
# the enemy HP read from the battle panel; ``open`` fires once at the start.
CHAPTER_1_TACTICS = (
    {'step': '1-V2', 'enemy': 'ガルバンゾー', 'card': 'フットバース', 'open': True,
     'note': 'ヴィーナス: ガルバンゾー出現時は開幕フットバース→白兵'},
    {'step': '1-C2', 'enemy': 'ガルバンゾー', 'card': 'ダイチスイム', 'when_hp_at_most': 13,
     'note': 'ココット: ガルバンゾーを白兵→敵HP13以下でダイチスイム'},
    {'step': '1-A2', 'enemy': 'ガルバンゾー', 'card': 'フットバース', 'when_hp_at_most': 24,
     'note': 'VSガルバンゾー 白兵→敵ＨＰ２４でフットバース'},
    {'step': '1-C2', 'enemy': 'タピオカ', 'card': 'ダイチスイム', 'open': True,
     'note': '開幕ダイチスイムとブラッキー使用→白兵'},
    {'step': '1-C2', 'enemy': 'タピオカ', 'card': 'ブラッキー', 'open': True,
     'note': '開幕ダイチスイムとブラッキー使用→白兵'},
    {'step': '1-B1', 'enemy': 'クイーン', 'card': 'クースカン', 'after_clash': True,
     'note': '白兵で一回ぶつかり合う→すぐにクースカン'},
    {'step': '1-B1', 'enemy': 'クイーン', 'card': 'ノリウツール', 'after_card': 'クースカン',
     'note': '続けてノリウツールを使用して撃破'},
)

CHAPTER_2_TACTICS = (
    {'step': '2-C1', 'enemy': 'エピィ', 'card': 'フットバース', 'open': True,
     'note': '開幕フットバースで卵を落とし白兵'},
    {'step': '2-B1', 'enemy': 'にせヒーロー', 'card': 'クースカン', 'open': True,
     'note': '①開幕クースカン'},
    {'step': '2-B1', 'enemy': 'にせヒーロー', 'card': 'イッテツーン', 'after_card': 'クースカン',
     'note': '②続けてイッテツーン'},
    {'step': '2-B1', 'enemy': 'にせヒーロー', 'card': 'ノリウツール', 'after_card': 'イッテツーン',
     'note': '③続けてノリウツールで撃破'},
)

CHAPTER_3_TACTICS = (
    {'step': '3-V1', 'enemy': 'ペコリーノ', 'card': 'グリンボー', 'when_hp_at_most': 68,
     'note': '白兵→HP68でグリンボーx2'},
    {'step': '3-A1', 'enemy': 'ジェラート', 'card': 'グリンボー', 'when_hp_at_most': 39,
     'note': '白兵→HP39でグリンボー'},
    {'step': '3-B1', 'enemy': 'プリンス', 'card': 'クースカン', 'open': True,
     'note': '①開幕クースカン'},
    {'step': '3-B1', 'enemy': 'プリンス', 'card': 'クースカン', 'after_card': 'クースカン',
     'note': '②続けてクースカン'},
    {'step': '3-B1', 'enemy': 'プリンス', 'card': 'ゼンマイン', 'when_hp_at_most': 25,
     'note': '④敵兵士1人でゼンマイン'},
)

CHAPTER_5_TACTICS = (
    {'step': '5-B1', 'enemy': 'だいまおう', 'card': 'マグネガキン', 'open': True,
     'note': '開幕マグネガキン'},
    {'step': '5-B1', 'enemy': 'だいまおう', 'card': 'ファバード', 'when_hp_at_most': 83,
     'note': 'HP83以下でファバード（食いしばり対策）'},
)

CHAPTER_6_TACTICS = (
    {'step': '6-C1', 'enemy': 'マフィン', 'card': 'グリンボー', 'open': True,
     'note': '開幕グリンボー2発で速攻'},
    {'step': '6-C1', 'enemy': 'マフィン', 'card': 'グリンボー', 'open': True,
     'note': '開幕グリンボー2発で速攻（2発目）'},
)

# 月一 purchases. Chapter 1 keeps the original dict shape; later chapters are
# tuples of month plans selected by the on-screen header in ``_plan``.
CHAPTER_1_PURCHASES = {
    'month': (1, 5),
    'chart_gold': 214,
    'cards': (('イッテツーン', 9), ('ノリウツール', 2), ('クースカン', 4), ('ゼンマイン', 1)),
    'soldiers': 41,
    # Under-budget order: the boss kit first, then the chart minimum of 6
    # イッテツーン, then the remainder in chart order.
    'priority': (('クースカン', 1), ('ノリウツール', 1), ('イッテツーン', 6),
                 ('ノリウツール', 1), ('クースカン', 3), ('イッテツーン', 3), ('ゼンマイン', 1)),
    'note': 'イッテツーンは最低でも６個は購入 足りない場合は兵士補充を減らす',
}

CHAPTER_2_PURCHASES = (
    {'month': (1, 6), 'chart_gold': 294,
     'cards': (('グリンボー', 11), ('ミックミー', 2)),
     'soldiers': 71,
     'priority': (('グリンボー', 11), ('ミックミー', 2)),
     'note': 'グリンボー11 ミックミー2 兵士71 残り77G 凶作はリセット(bot外)'},
)

CHAPTER_3_PURCHASES = (
    {'month': (1, 7), 'chart_gold': 475,
     'cards': (('グリンボー', 11),),
     'soldiers': 0,
     'priority': (('グリンボー', 11),),
     'note': 'グリンボー11 月イチ中華商人でクースカン21が買えるなら買う'},
    {'month': (1, 8), 'chart_gold': 807,
     'cards': (),
     'soldiers': 11,
     'priority': (),
     'note': '兵士11補充 夏バテ→バカンス'},
    {'month': (1, 11), 'chart_gold': 1738,
     'cards': (('ブラッキー', 21), ('グリンボー', 11)),
     'soldiers': 99,
     'priority': (('ブラッキー', 21), ('グリンボー', 11)),
     'note': 'ブラッキー21 グリンボー11 兵士99 洞窟商人は別途'},
)

CHAPTER_5_PURCHASES = (
    {'month': (1, 1), 'chart_gold': 200,
     'cards': (),
     'soldiers': 41,
     'priority': (),
     'note': '12月→1月 サンタはbot対象外 兵士41補充'},
)

CHAPTER_6_PURCHASES = (
    {'month': (2, 3), 'chart_gold': 500,
     'cards': (('クースカン', 31), ('グリンボー', 21)),
     'soldiers': 0,
     'priority': (('クースカン', 31), ('グリンボー', 21)),
     'note': '屋台クースカン31 グリンボー21'},
    {'month': (2, 4), 'chart_gold': 400,
     'cards': (),
     'soldiers': 21,
     'priority': (),
     'note': '兵士21補充'},
)

BOSSES = {
    1: 'クイーン', 2: 'にせヒーロー', 3: 'プリンス', 4: '四季王',
    5: 'だいまおう', 6: 'ハードマン', 7: 'ランパイア', 8: 'ノブナーガ',
    9: 'ハデス', 10: 'だいとうりょう', 11: 'エッグママ', 12: 'クーモン',
}

_ORDERS = {
    1: CHAPTER_1_ORDERS, 2: CHAPTER_2_ORDERS, 3: CHAPTER_3_ORDERS,
    4: CHAPTER_4_ORDERS, 5: CHAPTER_5_ORDERS, 6: CHAPTER_6_ORDERS,
    7: CHAPTER_7_ORDERS, 8: CHAPTER_8_ORDERS, 9: CHAPTER_9_ORDERS,
    10: CHAPTER_10_ORDERS, 11: CHAPTER_11_ORDERS, 12: CHAPTER_12_ORDERS,
}

_TACTICS = {
    1: CHAPTER_1_TACTICS, 2: CHAPTER_2_TACTICS, 3: CHAPTER_3_TACTICS,
    5: CHAPTER_5_TACTICS, 6: CHAPTER_6_TACTICS,
}

_PURCHASES = {
    2: CHAPTER_2_PURCHASES, 3: CHAPTER_3_PURCHASES,
    5: CHAPTER_5_PURCHASES, 6: CHAPTER_6_PURCHASES,
}


def home_castle(chapter: int) -> str:
    """The player's home castle label for ``chapter`` (on-screen name)."""
    return HOME_CASTLES.get(chapter, HOME_CASTLES[1])


def boss_castle(chapter: int) -> str:
    """Navigation label of the boss castle for ``chapter``.

    The on-screen boss entry text is always 「ボスじょう」; this name is the
    CASTLES/order target used for map navigation and order matching.
    """
    return BOSS_CASTLES.get(chapter, BOSS_CASTLES[1])


def orders(chapter: int):
    """Base orders for ``chapter``.

    Returns an empty tuple until the chapter has measured castles AND every
    order's source/target is present (``chart_unavailable`` / no navigation
    without coordinates). Chapter 1 is fully measured and always returns its
    chart orders.
    """
    castles = CASTLES.get(chapter) or {}
    if not castles:
        return ()
    return tuple(o for o in _ORDERS.get(chapter, ())
                 if o['source'] in castles and o['target'] in castles)


def all_orders(chapter: int):
    """Encoded orders without the measurement gate (tests, docs, review)."""
    return _ORDERS.get(chapter, ())


def tactics(chapter: int):
    return _TACTICS.get(chapter, ())


def castles(chapter: int) -> dict:
    return CASTLES.get(chapter, {})


def purchases(chapter: int):
    """Month purchase plans as a tuple of dicts (chapter 1 is one plan)."""
    if chapter == 1:
        return (CHAPTER_1_PURCHASES,)
    return _PURCHASES.get(chapter, ())


def purchase_for(chapter: int, year: int, month: int):
    """The plan matching an on-screen (year, month), or None."""
    key = (year, month)
    for plan in purchases(chapter):
        if tuple(plan.get('month') or ()) == key:
            return plan
    return None
