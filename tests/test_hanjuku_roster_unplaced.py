"""Month-roster generals missing from every castle list are unplaced (#3077).

g1075-1d5f65e9 2026-10-30 11:48 (chapter 4, month 28-9): the month roster
read this month named 14 generals, the castle lists held 8, and
``_unplaced_castle_generals`` read only あたしの家 statuses, which chapter 4
never recorded. The interim retake of フヌイヌム (lost 11:04) found no source
and held from 11:04, and no list that could hide the six missing generals
(v344 walks a list once it is opened) was ever opened again.
"""
from docich import hanjuku_policy as p

ROSTER = ['どうし', 'ミルフィーユ', 'アポロン', 'アルファルファ', 'ゼウス', 'セルリアク', 'ラズベリー',
          'クランベリー', 'ピスタチオ', 'キッシュ', 'ブカティーニ', 'ヴィーナス', 'ヘスティア', 'ミント']
MISSING = sorted(['アポロン', 'ヴィーナス', 'クランベリー', 'ヘスティア', 'ミルフィーユ', 'ミント'])


def live(month='28-9'):
    """hanjuku_bot.json at 11:48 (sorties trimmed to the one still marked en route nearby)."""
    return {'chapter': 4, 'month': '28-9', 'tick': 141575,
            'captured': ['ビクトリア', 'マラコビア', 'ネクスドリア', 'ミヒラギアン', 'アルカ'], 'lost': ['フヌイヌム'],
            'garrison': {'アルカ': ['セルリアク'], 'アルマムーン': ['どうし', 'ピスタチオ', 'キッシュ'],
                         'ネクスドリア': ['ゼウス'], 'ビクトリア': ['アルファルファ'], 'マラコビア': [],
                         'ミヒラギアン': ['ラズベリー']},
            'general_location_unknown': ['リースリング', 'ライム', 'エシャロット', 'キャンディー', 'ビシソワーズ',
                                         'ココット', 'キャラウェイ', 'アンディーブ', 'マカデミア', 'バジル',
                                         'ブカティーニ', 'フェデリーニ'],
            'recruit_roster': {'chapter': 4, 'complete': False, 'month': month, 'names': list(ROSTER),
                               'tick': 141407, 'wages': {}},
            'unplaced_refuted': {'castle': 'マラコビア', 'month': '27-10', 'names': []},
            'unplaced_refuted_castles': {'アルマムーン': {'castle': 'アルマムーン', 'month': '25-4', 'names': []},
                                         'マラコビア': {'castle': 'マラコビア', 'month': '27-10', 'names': []}},
            'sorties': {'I:d7d27e:3': {'general': 'クランベリー', 'source': 'アルマムーン', 'target': 'マラコビア',
                                       'status': 'en_route', 'tick': 137262}},
            'orders': {'4-B2': 'pending', '4-B3': 'pending', 'I:d7d27e:3': 'launched'}}


def test_roster_generals_in_no_list_are_unplaced():
    assert p._unplaced_castle_generals(live()) == MISSING


def test_the_retake_opens_a_list_that_may_hold_them():
    mem = live()
    orders = list(p.interim_candidates(mem).values())
    assert orders, 'the 11:04-11:48 hold: no interim candidate at all'
    retake = orders[0]
    assert retake['target'] == 'フヌイヌム' and retake['purpose'] == 'retake'
    # The hero's castle, where joiners stand, is opened with a missing general.
    assert retake['source'] == 'アルマムーン' and retake['general'] in MISSING


def test_a_roster_from_another_month_says_nothing():
    mem = live(month='28-8')
    assert p._unplaced_castle_generals(mem) == []
    assert not p.interim_candidates(mem)
