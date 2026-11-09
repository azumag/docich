"""Owner 2026-11-05: read a castle list instead of idling when nobody is known.

The bot had 43 overdue marches and an empty garrison, so the plan produced no
candidate at all. One unread castle is opened per plan (the sortie menu reads
the roster), and the deploy step sends whoever it finds at the boss.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world

ANY = 'しょうぐん'


def memory(garrison=None):
    owned = [c for c in chart.castles(6) if not chart.is_boss_castle(c)]
    mem = {
        'chapter': 6, 'month': '55-6', 'tick': 83000,
        'captured': list(owned), 'lost': [], 'orders': {},
        'garrison': {c: None for c in owned},
        'world': {'sorties': {}},
        'house_eggs': {}, 'general_location_unknown': [],
    }
    if garrison:
        _world(mem)['garrison'].update(garrison)
    return mem


def boss_waves(mem):
    return [o for o in replan.interim_candidates(mem).values()
            if o.get('target') == 'ボス']


def test_an_empty_bookkeeping_opens_one_castle_list_per_plan():
    waves = boss_waves(memory())
    probes = [o for o in waves if o.get('source_probe')]
    assert len(probes) == 1
    assert probes[0]['general'] == ANY
    assert probes[0]['purpose'] == 'attack'


def test_a_known_general_is_sent_instead_of_probing():
    mem = memory({'カバルッサ': ['ココット', 'ゼウス']})
    waves = boss_waves(mem)
    assert [o['general'] for o in waves if not o.get('source_probe')] == ['ココット']


def test_an_unread_castle_is_not_probed_while_other_generals_are_known():
    mem = memory({'カバルッサ': ['ココット']})
    for c in _world(mem)['garrison']:
        if c != 'カバルッサ':
            _world(mem)['garrison'][c] = None
    assert not [o for o in boss_waves(mem) if o.get('source_probe')]


def test_a_blind_boss_launch_mints_one_read_only_roster_probe():
    # docich#3515: a blind/kept boss probe actively asks for one roster read
    # of its source castle, even when other generals are known. The probe
    # order opens the list and settles the phantom without sending.
    mem = memory({'カバルッサ': ['ココット'], 'アルマムーン': ['ヘルメス']})
    from docich.hanjuku.state.regions import flow as _flow
    _flow(mem)['launch_probe'] = {'old-step': {'general': 'リーキ', 'source': 'カバルッサ',
                        'target': 'ボス', 'reads': 3, 'blind': True, 'kept': True, 'tick': 82900}}
    mem['roster_read_due'] = {'カバルッサ': 82900}
    _world(mem)['sorties'] = {'old-step': {'general': 'リーキ', 'source': 'カバルッサ', 'target': 'ボス',
                   'status': 'en_route', 'tick': 82900, 'launch_kept': True}}
    waves = boss_waves(mem)
    probes = [o for o in waves if o.get('roster_probe')]
    assert len(probes) == 1
    assert probes[0]['source'] == 'カバルッサ'
    assert probes[0]['general'] == ANY
    assert probes[0].get('source_probe') is True
    assert not [o for o in waves if o.get('roster_probe') and o.get('source') != 'カバルッサ']


def test_unread_garrison_does_not_crash_situation_digest():
    """A None roster is unread, not an iterable of general names."""
    mem = memory({'カバルッサ': ['ココット']})
    situation = replan._adjust_situation(mem)
    assert situation['garrison']['カバルッサ'] == ['ココット']
    assert all(isinstance(names, list) for names in situation['garrison'].values())
    assert any(names == [] for names in situation['garrison'].values())
