"""Public map facts for registration review, not native navigation coordinates.

Values are the top-left of a flag bounding box in GCGX's 130x130 source
image. Names were cross-checked against the numbered castle Wiki diagrams
and the castle-ID table. Source images are not copied into this repository.
The approximate 8x model disagrees with existing native measurements and is
not a calibrated transform. Importing this catalog never populates CASTLES,
WORLD_MAP_OFFSET, Y_JUMP_OFFSET or HOUSE_VIEW.

Boss names below identify source locations, not parsed in-game labels or
known occupants. Four chapter-4 sites have random occupants; chapter 9's
boss moves. No boss position is asserted by this catalog.
"""

SOURCE_IMAGE_SIZE = (130, 130)
SOURCE_COORDINATES = 'flag_bbox_top_left'
SOURCE_REFERENCES = (
    'https://gcgx.games/hanjuku/',
    'https://wikiwiki.jp/hjksfc/%E5%9F%8E',
    'https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggCastle.html',
)

SOURCE_CASTLES: dict[int, dict[str, tuple[int, int]]] = {
    1: {
        'アルマムーン': (90, 97), 'ナキューメラ': (88, 77),
        'キカンドン': (70, 87), 'ジョンリギ': (68, 69),
        'カストーラ': (74, 45), 'スペンソニア': (48, 45), 'ゴーメン': (36, 73),
    },
    2: {
        'アルマムーン': (64, 103), 'ハドリバーグ': (92, 89),
        'フーリック': (44, 87), 'グロン': (94, 57), 'ドミノーラ': (60, 59),
        'ウラノポリス': (34, 59), 'スペランザ': (80, 41), 'アウスパジア': (36, 33),
    },
    3: {
        'アルマムーン': (88, 17), 'グリン': (106, 37), 'コラダイン': (82, 49),
        'イーピヨル': (96, 79), 'ドンダム': (70, 101), 'メガパタ': (56, 79),
        'パタゴーヌ': (42, 51), 'アバロン': (28, 89), 'リグア': (16, 33),
    },
    4: {
        'アルマムーン': (64, 63), 'マラコビア': (64, 21), 'フヌイヌム': (90, 39),
        'ネクスドリア': (88, 91), 'ビクトリア': (64, 107), 'ミヒラギアン': (38, 91),
        'アルカ': (38, 39),
    },
    5: {
        'アルマムーン': (14, 11), 'オトラント': (50, 13), 'クロチェット': (102, 13),
        'カルーン': (70, 35), 'ジュメルズ': (98, 49), 'テラビル': (56, 67),
        'ヒュペルボレア': (24, 53), 'ボッサール': (14, 111), 'アイアイエー': (52, 99),
        'バスカビル': (88, 89), 'プトレマイス': (110, 111),
    },
    6: {
        'アルマムーン': (48, 15), 'アルブラーカ': (10, 11), 'イカリア': (28, 33),
        'カバルッサ': (66, 33), 'オケアナ': (112, 43), 'バビラリー': (10, 73),
        'ダーラント': (46, 71), 'セレーネ': (76, 85), 'グルーポフ': (18, 115),
        'ユージア': (58, 107), 'アブダレ': (100, 109), 'トウトウ': (108, 71),
    },
    7: {
        'アルマムーン': (108, 11), 'ベンゴディ': (96, 47), 'レオナール': (60, 11),
        'ロキュータ': (26, 19), 'リデンブロック': (24, 53), 'マンシー': (42, 83),
        'プルートー': (10, 107), 'ハシオクラム': (52, 109), 'フィロメラ': (90, 97),
    },
    8: {
        'あるまむーん': (16, 111), 'おかざき': (48, 77), 'しらかわ': (64, 111),
        'えど': (106, 111), 'おだわら': (106, 79), 'ながはま': (20, 53),
        'いちじょうだに': (56, 45), 'しまばら': (20, 17), 'まつやま': (62, 13),
        'ひめじ': (98, 13),
    },
    9: {
        'アルマムーン': (26, 113), 'ハヤク': (18, 87), 'タオ': (34, 63),
        'サヌト': (18, 37), 'オシロガ': (34, 19), 'ドンドン': (44, 45),
        'オオキク': (50, 75), 'ナッテ': (34, 97), 'シマウ': (46, 115),
        'カラ': (62, 105), 'イソ': (62, 77), 'イデ！': (66, 49),
    },
    10: {
        'アルマムーン': (70, 107), 'マカリア': (98, 97), 'ピランドリア': (24, 93),
        'トルストリア': (48, 79), 'ファルゲ': (78, 71), 'コッカーニュ': (108, 61),
        'カルカル': (22, 51), 'カロナック': (28, 27), 'ケントルム': (96, 27),
        'ジュアム': (62, 17),
    },
    11: {
        'アルマムーン': (58, 103), 'チリベット': (24, 103), 'タプロベイン': (42, 85),
        'ジャンセニア': (106, 77), 'ゴンダル': (112, 103), 'マランマ': (16, 79),
        'タタール': (62, 57), 'クラボニア': (30, 43), 'トリフェ': (102, 41),
        'アエンタール': (18, 17), 'ネペンテ': (100, 19),
    },
    12: {
        'アルマムーン': (64, 101), 'ワフェルダ': (48, 83), 'ブラックランド': (78, 83),
        'ユーフォニア': (12, 65), 'アトランテ': (114, 65), 'アファニア': (48, 55),
        'ヘリオポリス': (78, 55), 'ファンゴル': (42, 35), 'ディランダ': (84, 35),
        'バラタリア': (32, 67), 'ラグナグ': (94, 67),
    },
}

SOURCE_BOSS_SITES: dict[int, tuple[str, ...]] = {
    1: ('茨の塔',), 2: ('偽アルマムーン城',), 3: ('完熟プリンス城',),
    4: ('四季王城(北西)', '四季王城(北東)', '四季王城(南西)', '四季王城(南東)'),
    5: ('完熟大魔城',), 6: ('完熟秘密基地',), 7: ('完熟幽霊城',),
    8: ('完熟天守閣',), 9: ('完熟兵器"Ｐ"',), 10: ('ホワイトハウス',),
    11: ('オヴァリウム',), 12: ('完熟神殿',),
}
RANDOM_BOSS_OCCUPANTS = frozenset({4})
MOVING_BOSS_CHAPTERS = frozenset({9})
SEASON_KINGS = frozenset({'スプリミリョーネ', 'サマカンテ', 'フォーリシア', 'ウイナッツォ'})


def source_page(chapter: int) -> str:
    if type(chapter) is not int or chapter not in SOURCE_CASTLES:
        raise ValueError('unknown chapter')
    return f'https://gcgx.games/hanjuku/{chapter:02d}.html'


def source_image(chapter: int) -> str:
    source_page(chapter)
    return f'https://gcgx.games/hanjuku/img/{chapter:02d}.png'
