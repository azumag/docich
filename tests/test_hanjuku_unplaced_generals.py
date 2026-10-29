"""g684 2-5 13:45-14:18: joiners stood in a castle whose list was never reread,
every garrison read one general and nothing sortied for 30 minutes."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p


def memory(joiners_month='2-5'):
    owned = ['キカンドン', 'ジョンリギ']
    mem = {'chapter': 1, 'month': '2-5', 'tick': 6700, 'captured': owned,
           'garrison': {'アルマムーン': ['どうし'], 'キカンドン': ['アルファルファ'],
                        'ジョンリギ': ['ゼウス']},
           'house_eggs': {n: {'location': 'castle', 'month': '2-5'}
                          for n in ('どうし', 'アルファルファ', 'ゼウス')},
           'lost': ['ナキューメラ']}
    for n in ('アマンディーヌ', 'カシュー'):
        mem['house_eggs'][n] = {'location': 'castle', 'month': joiners_month}
    p._apply_world_flags(mem, {'アルマムーン': 'own', 'キカンドン': 'own', 'ジョンリギ': 'own',
                               'ナキューメラ': 'enemy'})
    return mem


def test_joiners_make_the_heros_castle_a_sortie_source():
    mem = memory()
    assert p._unplaced_castle_generals(mem) == ['アマンディーヌ', 'カシュー']
    # v305: one joiner leaving would leave the hero with one other; only a
    # probe that reads the real list may still open it.
    assert not [o for o in p.interim_candidates(mem).values() if o.get('source') == 'アルマムーン'
                and o['general'] != p.MOVE_ANY_GENERAL]
    mem['house_eggs']['ミント'] = {'location': 'castle', 'month': '2-5'}
    picks = [o for o in p.interim_candidates(mem).values() if o.get('source') == 'アルマムーン']
    assert picks and picks[0]['general'] == 'アマンディーヌ'
    order = {'step': 'A:x:P1', 'general': 'どうし', 'source': 'アルマムーン',
             'target': 'ナキューメラ', 'cards': [], 'after': None}
    assert p._source_spare_missing(order, mem) is False
    other = dict(order, step='A:x:P2', general='アルファルファ', source='キカンドン')
    assert p._source_spare_missing(other, mem) is True      # a real sole defender stays


def test_last_months_status_is_not_a_placement():
    mem = memory(joiners_month='2-4')
    assert p._unplaced_castle_generals(mem) == []
    order = {'step': 'A:x:P1', 'general': 'どうし', 'source': 'アルマムーン',
             'target': 'ナキューメラ', 'cards': [], 'after': None}
    assert p._source_spare_missing(order, mem) is True


def test_a_read_list_refutes_the_arrival_assumption_until_the_unplaced_set_changes():
    # g684 3-1 16:09-16:10: どうし alone at ジョンリギ, ゼウス/ミント unplaced;
    # the same retake order was issued and cancelled every 10 s.
    from docich.hanjuku_screen import Screen
    mem = memory()
    order = {'step': 'A:x:P1', 'general': 'どうし', 'source': 'アルマムーン',
             'target': 'ナキューメラ', 'cards': [], 'after': None, 'purpose': 'retake'}
    mem['orders'] = {'A:x:P1': 'pending'}
    assert p._source_spare_missing(order, mem) is False
    from unittest import mock
    with mock.patch.object(p, '_present_generals', return_value=['どうし']):
        p._keep_sortie_defender(Screen([], None, '', kind='general_list'), mem, order)
    assert p._source_spare_missing(order, mem) is True          # no re-issue loop
    assert not [o for o in p.interim_candidates(mem).values() if o.get('source') == 'アルマムーン'
                and o.get('general') in ('アマンディーヌ', 'カシュー')]
    mem['house_eggs']['ビーツ'] = {'location': 'castle', 'month': '2-5'}   # a new joiner
    assert p._source_spare_missing(order, mem) is False
