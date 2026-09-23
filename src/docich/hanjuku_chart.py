"""Chart data for the deterministic Hanjuku bot.

Source: azumag/hanjuku-sfc-speedrun ``charts/1.md`` and ``charts/overview.md``
(chapter 1: 主人公→キカンドン, ヴィーナス→ナキューメラ, ココット→ジョリンギ,
then ゴーメン/カストーラ/スペンソニア, the 1年4→5月 purchases and the クイーン
boss sequence). Castle coordinates are map-cursor cells measured in an
isolated emulator from each castle's roof anchor; they are chart facts, not
live RAM reads. The hero is the name the bot enters (``HERO``).

Only chapter 1 is charted here. Later chapters are reported as
``chart_unavailable`` and use the reviewed generic policy; they are never
claimed as chart-compliant.
"""
from __future__ import annotations

HERO = 'どうし'

# Map-cursor cells that select each castle (top-left of the 16x16 cursor).
CASTLES = {
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
}

# Orders. ``after`` is the event that unlocks an order: None at chapter start,
# ('captured', castle) after a verified win at that castle, or
# ('all_captured',) once every non-boss castle has been won.
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

# 月一 1ねん4のつき→5のつき (menu header shows 1ねん 5のつき).
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

BOSSES = {1: 'クイーン'}


def orders(chapter: int):
    return CHAPTER_1_ORDERS if chapter == 1 else ()


def tactics(chapter: int):
    return CHAPTER_1_TACTICS if chapter == 1 else ()


def castles(chapter: int) -> dict:
    return CASTLES.get(chapter, {})


def purchases(chapter: int):
    return CHAPTER_1_PURCHASES if chapter == 1 else None
