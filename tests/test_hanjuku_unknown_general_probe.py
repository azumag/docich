"""g684 4-3..4-6: generals marked location-unknown after a lost battle were
never looked for again, so every castle list read one general and nothing
sortied while castles fell."""
import json

from docich import hanjuku_policy as p
from test_hanjuku_retake_guard import generals


def stalled():
    return {'chapter': 1, 'month': '4-6', 'tick': 20000, '_records': [],
            'captured': ['ジョンリギ', 'ナキューメラ', 'キカンドン'],
            'lost': ['カストーラ', 'スペンソニア', 'ゴーメン'],
            'garrison': {'アルマムーン': ['どうし'], 'キカンドン': ['アンディーブ'],
                         'ナキューメラ': ['アマンディーヌ'], 'ジョンリギ': ['マカデミア']},
            'general_location_unknown': ['ゼウス', 'クミン'],
            'general_location_unknown_at': {'ゼウス': 19000, 'クミン': 19100},
            'house_eggs': {g: {'location': 'castle', 'month': '4-6', 'observed_tick': 19900}
                           for g in ('どうし', 'ゼウス', 'クミン', 'アンディーブ',
                                     'アマンディーヌ', 'マカデミア')}}


def probe(mem, step='I:unknown:1'):
    order = dict(next(o for o in p.interim_candidates(mem).values() if o.get('source_probe')),
                 step=step)
    mem.update(active=order['step'], orders={}, launched_orders={order['step']: order})
    return order


def test_status_after_the_loss_makes_unknown_generals_worth_a_list_read():
    mem = stalled()
    assert p._unplaced_castle_generals(mem) == ['クミン', 'ゼウス']
    candidates = p.interim_candidates(mem)
    assert candidates and all(o.get('source_probe') for o in candidates.values())
    # Looking is not placing: no garrison is invented for them.
    assert 'ゼウス' not in {g for gs in mem['garrison'].values() for g in gs}


def test_status_read_before_the_loss_does_not_place_the_general():
    mem = stalled()
    mem['general_location_unknown_at'] = {'ゼウス': 19950, 'クミン': 19950}
    assert p._unplaced_castle_generals(mem) == []
    assert p.interim_candidates(mem) == {}


def test_legacy_unknown_mark_without_loss_tick_uses_this_months_status():
    mem = stalled(); mem.pop('general_location_unknown_at')
    assert p._unplaced_castle_generals(mem) == ['クミン', 'ゼウス']


def test_field_status_keeps_the_unknown_general_unplaced_nowhere():
    mem = stalled()
    for g in ('ゼウス', 'クミン'):
        mem['house_eggs'][g]['location'] = 'field'
    assert p.interim_candidates(mem) == {}


def test_found_general_is_the_spare_that_sorties():
    mem = stalled(); order = probe(mem)
    source = order['source']
    names = [mem['garrison'][source][0], 'ゼウス']
    assert p.deploy_step(generals(names), mem) == [p.pad('a')]
    assert mem['garrison'][source] == names
    assert 'ゼウス' not in mem['general_location_unknown']
    assert 'ゼウス' not in mem['general_location_unknown_at']
    assert mem['sortie_general'][order['step']] in names
    assert mem['sortie_general'][order['step']] != p.NAME


def test_empty_handed_reads_rotate_castles_then_stop():
    mem = stalled(); sources = []
    for serial in range(6):
        candidates = p.interim_candidates(mem)
        if not candidates:
            break
        order = probe(mem, f'I:unknown:{serial}')
        sources.append(order['source'])
        assert p.deploy_step(generals(mem['garrison'][order['source']]), mem) == [p.pad('b'), p.pad('b')]
        mem = json.loads(json.dumps(mem))
    assert sources and len(sources) == len(set(sources))
    assert not p.interim_candidates(mem)
    # Next month's status read is a new reason to look again.
    mem['month'] = '4-7'; mem['tick'] += 100
    for info in mem['house_eggs'].values():
        info.update(month='4-7', observed_tick=mem['tick'])
    assert p.interim_candidates(mem)


def test_lost_battle_records_its_tick_and_a_list_or_chapter_clears_it():
    from test_hanjuku_defender_succession import memory, panel
    mem = memory(); mem['tick'] = 4321
    assert p.battle_step(panel(), mem) == []
    assert p.battle_step(panel(), mem)
    assert mem['general_location_unknown'] == ['クミン']
    assert mem['general_location_unknown_at'] == {'クミン': 4321}
    # Status read earlier than the loss is not evidence of where she went.
    mem.update(month='1-3', house_eggs={'クミン': {'location': 'castle', 'month': '1-3',
                                                  'observed_tick': 4000}})
    assert p._unplaced_castle_generals(mem) == []
    mem['house_eggs']['クミン']['observed_tick'] = 4400
    assert p._unplaced_castle_generals(mem) == ['クミン']
    p._garrison_move(mem, 'クミン', target='ジョンリギ')
    assert mem['general_location_unknown_at'] == {}
    mem['general_location_unknown'] = ['クミン']
    mem['general_location_unknown_at'] = {'クミン': 1}
    p._enter_chapter(mem, 2, reason='test')
    assert 'general_location_unknown_at' not in mem
