"""A lost chart general does not hide the unread hero castle from a retake.

g1075-1d5f65e9 2026-10-30 JST, chapter 4: アルカ fell at 02:34:21 while
アルマムーン's list was unread (02:14:45 recruit). The chart order for アルカ
is 4-C1 with ココット, who was in ``general_location_unknown``, so
``_interim_source`` returned None before it looked at the unread home and
chart_defense_wait reported no candidate, although the complete roster
read at 02:31:31 for 25-9 no longer listed ココット. At 02:43:41 the monthly
fallback sent マラコビア's only defender instead.
"""
from docich import hanjuku_policy as policy

HOME = 'アルマムーン'


def memory():
    return {'chapter': 4, 'month': '25-9', 'tick': 1,
            'captured': ['ネクスドリア', 'ビクトリア', 'フヌイヌム', 'マラコビア', 'ミヒラギアン'],
            'lost': ['アルカ'],
            'garrison': {'ネクスドリア': ['トレビス'], 'ビクトリア': ['アルファルファ'],
                         'フヌイヌム': ['ヘーゼル'], 'マラコビア': ['ミュスカデ'],
                         'ミヒラギアン': ['アテナ']},
            'general_location_unknown': ['ココット'],
            'recruit_roster': {'chapter': 4, 'month': '25-9', 'complete': True,
                               'names': ['どうし', 'アテナ', 'アルファルファ', 'ミュスカデ',
                                         'トレビス', 'ヘーゼル', 'アンディーブ', 'タピオカ']}}


def retakes(mem):
    return [c for c in policy.interim_candidates(mem).values() if c['purpose'] == 'retake']


def test_the_retake_reads_the_unread_hero_castle_when_the_chart_general_is_lost():
    [order] = retakes(memory())
    assert (order['target'], order['source']) == ('アルカ', HOME)
    assert order['general'] == policy.MOVE_ANY_GENERAL and order['source_probe'] is True


def test_a_general_still_on_the_roster_is_waited_for():
    mem = memory()
    mem['recruit_roster']['names'].append('ココット')    # alive, only unlocated
    assert retakes(mem) == []


def test_last_month_roster_does_not_retire_the_chart_general():
    mem = memory()
    mem['recruit_roster']['month'] = '25-8'
    assert retakes(mem) == []


def test_a_read_hero_castle_still_decides_as_measured():
    mem = memory()
    mem['garrison'][HOME] = ['どうし', 'アンディーブ', 'タピオカ']
    assert retakes(mem) == []          # the hero keeps two others (owner 2026-10-29)
