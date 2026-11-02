"""A defence won at a castle whose list is unread leaves the list unread.

g1075-1d5f65e9 2026-10-29 JST, chapter 4: the 23:54 recruit cleared
アルマムーン's list (the joiner stands in the hero's castle). The joiner
ビシソワーズ then won the 23:58-00:01 defence there and the list became
[ビシソワーズ], so from 00:02 the hero's castle looked like a one-general
castle (chart_defense_wait: 候補0件) while どうし, ヘーゼル, ピスタチオ and
シェーブル stood there too (00:39:31 garrison_seen). The retakes of
フヌイヌム and アルカ waited for the 00:39 adjusted chart.
"""
from docich import hanjuku_policy as policy

HOME = 'アルマムーン'
OTHERS = {'ネクスドリア': ['トレビス'], 'ビクトリア': ['アルファルファ'],
          'マラコビア': ['ラズベリー'], 'ミヒラギアン': ['アテナ']}


def defended(garrison):
    return {'chapter': 4, 'tick': 100, 'captured': [*OTHERS, 'アルカ'],
            'lost': ['フヌイヌム'], 'garrison': garrison,
            'battle': {'enemy': 'リーキ', 'ally': 'ビシソワーズ', 'enemy_hp': 0, 'ally_hp': 14,
                       'start_enemy_hp': 30, 'start_ally_hp': 27, 'castle': HOME,
                       'side': 'defense', 'step': None, 'cards_used': [], 'planned_cards': []}}


def finish(mem):
    for _ in range(2):              # the first call only migrates the card evidence
        policy.battle_end(mem, 'map')


def results(mem):
    return [r['resulting_event'] for r in mem.get('_records', []) if r['decision'] == 'battle_result']


def test_defence_win_does_not_turn_an_unread_list_into_one_general():
    mem = defended({'アルカ': ['ブカティーニ'], **OTHERS})       # the recruit popped HOME
    finish(mem)
    assert results(mem) == ['win']
    assert HOME not in mem['garrison']


def test_defence_win_still_adds_the_defender_to_a_read_list():
    mem = defended({HOME: ['どうし'], 'アルカ': ['ブカティーニ'], **OTHERS})
    finish(mem)
    assert results(mem) == ['win']
    assert mem['garrison'][HOME] == ['どうし', 'ビシソワーズ']


def test_the_retake_reads_the_hero_castle_instead_of_waiting():
    mem = defended({'アルカ': ['ブカティーニ'], **OTHERS})
    finish(mem)
    retakes = [c for c in policy.interim_candidates(mem).values() if c['purpose'] == 'retake']
    assert [(c['target'], c['source']) for c in retakes] == [('フヌイヌム', HOME)]
