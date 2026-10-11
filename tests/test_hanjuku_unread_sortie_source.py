"""g684: a refuted home must not hide the unread Jonrigi garrison."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from test_hanjuku_retake_guard import generals


def stalled():
    mem = {'chapter': 1, 'month': '4-2', 'tick': 17900, '_records': [],
           'captured': ['ジョンリギ', 'ナキューメラ', 'ゴーメン', 'キカンドン'],
           'lost': ['カストーラ', 'スペンソニア'],
           'garrison': {'アルマムーン': ['マカデミア'], 'キカンドン': ['アンディーブ'],
                        'ゴーメン': ['ヴィーナス'], 'ナキューメラ': ['アマンディーヌ']},
           'house_eggs': {g: {'location': 'castle', 'month': '4-2'}
                          for g in ('どうし', 'ゼウス', 'クミン', 'タルタル')}}
    mem['unplaced_refuted'] = {'castle': 'アルマムーン', 'month': '4-2',
                               'names': p._unplaced_castle_generals(mem)}
    return mem


def test_refuted_home_rotates_to_unread_owned_castle_without_inventing_placement():
    mem = stalled(); before = deepcopy(mem['garrison'])
    candidates = p.interim_candidates(mem)
    assert candidates
    castora = next(o for o in candidates.values() if o['target'] == 'カストーラ')
    assert castora['source'] == 'ジョンリギ'
    assert castora['source_probe'] is True
    assert mem['garrison'] == before
    assert not mem.get('sorties')


def activate(mem):
    order = dict(next(iter(p.interim_candidates(mem).values())), step='I:unread:1')
    mem.update(active=order['step'], orders={}, launched_orders={order['step']: order})
    return order


@pytest.mark.parametrize('names', [[], ['どうし'], ['どうし', 'どうし']])
def test_real_list_cannot_send_the_unread_castles_last_defender(names):
    mem = stalled(); activate(mem)
    assert p.deploy_step(generals(names), mem) == [p.pad('b'), p.pad('b')]
    assert mem['garrison']['ジョンリギ'] == names
    assert all(o['source'] != 'ジョンリギ' for o in p.interim_candidates(mem).values())
    assert not mem.get('sorties')


def test_two_observed_generals_choose_spare_and_keep_hero_at_castle():
    mem = stalled(); order = activate(mem)
    screen = generals(['ゼウス', 'どうし'])
    assert p.deploy_step(screen, mem) == [p.pad('a')]
    assert mem['garrison']['ジョンリギ'] == ['ゼウス', 'どうし']
    assert mem['general_override'][order['step']] == 'ゼウス'
    assert not mem.get('sorties')  # selecting is not a dispatch/arrival receipt
    p._garrison_move(mem, 'ゼウス', source='ジョンリギ')
    assert mem['garrison']['ジョンリギ'] == ['どうし']


def test_a_lost_or_known_empty_castle_is_never_a_probe_source():
    mem = stalled(); mem['captured'].remove('ジョンリギ')
    assert all(o['source'] != 'ジョンリギ' for o in p.interim_candidates(mem).values())
    mem['captured'].append('ジョンリギ'); mem['garrison']['ジョンリギ'] = []
    assert all(o['source'] != 'ジョンリギ' for o in p.interim_candidates(mem).values())


def test_nearest_unread_castle_is_chosen_deterministically():
    mem = stalled(); mem['garrison'].pop('キカンドン')
    mem['house_eggs'] = {}
    source, _ = p._interim_source(mem, 'カストーラ', None, p._owned(mem), set())
    assert source == 'ジョンリギ'


def test_old_monthly_status_does_not_prevent_actual_unread_list_inspection():
    mem = stalled()
    for info in mem['house_eggs'].values(): info['month'] = '4-1'
    assert p._unplaced_castle_generals(mem) == []
    assert p.interim_candidates(mem)['retake_1']['source'] == 'ジョンリギ'


def test_negative_list_from_another_castle_does_not_erase_home_refutation():
    import json
    mem = stalled(); activate(mem)
    assert p.deploy_step(generals([]), mem) == [p.pad('b'), p.pad('b')]
    mem = json.loads(json.dumps(mem))
    assert p._arrival_castle(mem, p._owned(mem)) is None
    assert all(o['source'] not in {'ジョンリギ', 'アルマムーン'}
               for o in p.interim_candidates(mem).values())
    mem['month'] = '4-3'
    for info in mem['house_eggs'].values(): info['month'] = '4-3'
    assert p._arrival_castle(mem, p._owned(mem)) == 'アルマムーン'
    p._enter_chapter(mem, 2, reason='test')
    assert 'unplaced_refuted_castles' not in mem and 'unplaced_refuted' not in mem


def test_defender_derived_single_name_is_not_a_complete_castle_list():
    from docich.hanjuku_screen import Screen
    mem = stalled(); p._garrison_move(mem, 'クミン', target='ジョンリギ')
    # Home was read again after that defender became placed.
    mem['unplaced_refuted']['names'] = p._unplaced_castle_generals(mem)
    order = next(o for o in p.interim_candidates(mem).values() if o['target'] == 'カストーラ')
    assert order['source'] == 'ジョンリギ' and order['source_probe'] is True
    mem.update(active='I:probe:1', orders={}, launched_orders={})
    order = dict(order, step=mem['active']); mem['launched_orders'][order['step']] = order
    mem['chart_plan'] = {'orders': [order]}
    assert p.next_order(mem) is order
    assert p._source_spare_missing(order, mem, allow_probe=True) is False
    assert p._source_spare_missing(order, mem) is True
    # A hotloaded old card panel cannot use the list-opening exception.
    assert p.deploy_step(Screen([], None, '', kind='card_select'), mem) == [p.pad('b'), p.pad('b')]


def test_all_negative_castle_lists_stop_probing_instead_of_alternating_home():
    mem = stalled(); sources = []
    for serial in range(5):
        candidates = p.interim_candidates(mem)
        if not candidates: break
        order = dict(next(iter(candidates.values())), step=f'I:probe:{serial}')
        source = order['source']; sources.append(source)
        mem.update(active=order['step'], orders={}, launched_orders={order['step']: order})
        names = mem['garrison'].get(source) or ['クミン']
        assert p.deploy_step(generals(names), mem) == [p.pad('b'), p.pad('b')]
    assert len(sources) == len(set(sources))
    assert not p.interim_candidates(mem)
