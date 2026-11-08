"""#2965: an entry after a march longer than SORTIE_BUSY_TICKS binds its sortie.

g1075-1d5f65e9 2026-10-29: ポセイドン left グリン for リグア at observation 68474
and entered at 69151 (677 later). The entry was not bound, the battle ran as
side 'enemy' with the carried グリンボー unknown, and ポセイドン retreated.
The records below are the live ones for ポセイドン, ヘラ and シャルドネ.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows.battle import message_step
from docich.hanjuku.os_layer.world import SORTIE_BUSY_TICKS
from docich.hanjuku.strategy.orders import LONG_MARCH_TICKS, _match_sortie
from docich.hanjuku.state.regions import world as _world

ENTRY = 'ポセイドンしょうぐんがリグアじょうにのりこんだ!!'


def live(now=69151):
    return {'chapter': 3, 'tick': now,
            'world': {'sorties': {
                'I:b59ab6:3': {'general': 'ポセイドン', 'source': 'グリン', 'target': 'リグア',
                               'status': 'en_route', 'tick': 68474},
                'I:ae10cd:1': {'general': 'ポセイドン', 'source': 'アルマムーン', 'target': 'グリン',
                               'status': 'en_route', 'tick': 9450},
                'I:311340:1': {'general': 'ポセイドン', 'source': 'コラダイン', 'target': 'グリン',
                               'status': 'en_route', 'tick': 31314},
                'I:5926d2:1': {'general': 'ポセイドン', 'source': 'グリン', 'target': 'パタゴーヌ',
                               'status': 'arrived', 'tick': 24580},
                '3-Z2': {'general': 'ヘラ', 'source': 'グリン', 'target': 'リグア',
                         'status': 'en_route', 'tick': 1142},
                'A:b59ab675:K1': {'general': 'シャルドネ', 'source': 'グリン', 'target': None,
                                  'planned_target': 'リグア', 'status': 'launched_unconfirmed',
                                  'tick': 69310},
                'I:402f8c:2': {'general': 'シャルドネ', 'source': 'アルマムーン', 'target': 'ドンダム',
                               'status': 'en_route', 'tick': 28341},
            }},
            'launched': {'リグア': {'general': 'ポセイドン', 'step': 'I:b59ab6:3'}}}


def test_the_live_entry_after_677_observations_binds_the_newest_sortie():
    assert 69151 - 68474 >= SORTIE_BUSY_TICKS
    assert _match_sortie(live(), 'リグア', 'ポセイドン') == ('I:b59ab6:3', 'matched')


def test_the_entry_message_becomes_our_attack_with_its_step():
    mem = live()
    with chart.map_context(mem):
        assert message_step(SimpleNamespace(text=ENTRY), mem) is not None
    assert mem['attack'] == {'general': 'ポセイドン', 'castle': 'リグア', 'side': 'attack',
                             'step': 'I:b59ab6:3'}
    assert _world(mem)['sorties']['I:b59ab6:3']['status'] == 'arrived'


def test_an_unconfirmed_target_counts_by_its_planned_target():
    mem = live(now=69310 + 700)
    assert _match_sortie(mem, 'リグア', 'シャルドネ') == ('A:b59ab675:K1', 'matched')
    assert _match_sortie(mem, 'ドンダム', 'シャルドネ') == (None, 'none')


def test_an_older_record_of_the_same_general_never_binds():
    assert _match_sortie(live(), 'グリン', 'ポセイドン') == (None, 'none')
    assert _match_sortie(live(), 'リグア', 'ヘラ') == (None, 'none')


def test_a_march_beyond_the_long_bound_is_not_bound():
    mem = live(now=68474 + LONG_MARCH_TICKS)
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_a_newest_sortie_that_already_arrived_is_not_bound():
    mem = live()
    _world(mem)['sorties']['I:b59ab6:3']['status'] = 'arrived'
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_a_record_without_a_tick_keeps_the_long_march_path_off():
    mem = live()
    _world(mem)['sorties']['old'] = {'general': 'ポセイドン', 'target': 'コラダイン', 'status': 'en_route'}
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_two_newest_records_at_the_same_tick_are_not_guessed():
    mem = live()
    _world(mem)['sorties']['twin'] = dict(_world(mem)['sorties']['I:b59ab6:3'])
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_within_the_busy_window_the_old_rules_still_apply():
    mem = live(now=68474 + 10)
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == ('I:b59ab6:3', 'matched')
    _world(mem)['sorties']['twin'] = dict(_world(mem)['sorties']['I:b59ab6:3'], tick=68480)
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'ambiguous')


# g1133-a3499a00 2026-11-05: ピスタチオ left for ユージア at observation 28800
# and entered at 30451 (1651 later, 51 over the old 4x bound). The unmatched
# entry ran the whole battle as side 'enemy', disabling the value-aware
# retreat policy; he retreated at ally 9 vs enemy 7 with melee score ahead.

def g1133(now=30451):
    return {'chapter': 6, 'tick': now,
            'world': {'sorties': {
                'A:510711f6:Mc01022': {'general': 'ピスタチオ', 'source': 'アルマムーン',
                                        'target': 'ユージア', 'status': 'en_route',
                                        'tick': 28800},
            }},
            'launched': {'ユージア': {'general': 'ヘラ', 'step': 'A:42d50d12:Mc01022'}}}

PISTACHIO_ENTRY = 'ピスタチオしょうぐんがユージアじょうにのりこんだ!!'


def test_the_1651_observation_march_binds_its_sortie():
    assert 30451 - 28800 >= 4 * SORTIE_BUSY_TICKS
    assert _match_sortie(g1133(), 'ユージア', 'ピスタチオ') == ('A:510711f6:Mc01022', 'matched')


def test_the_1651_march_entry_is_our_attack_with_its_step():
    mem = g1133()
    with chart.map_context(mem):
        message_step(SimpleNamespace(text=PISTACHIO_ENTRY), mem)
    assert mem['attack'] == {'general': 'ピスタチオ', 'castle': 'ユージア',
                             'side': 'attack', 'step': 'A:510711f6:Mc01022'}


def test_a_known_general_beyond_the_bound_still_attacks():
    mem = g1133(now=28800 + LONG_MARCH_TICKS + 100)
    assert _match_sortie(mem, 'ユージア', 'ピスタチオ') == (None, 'none')
    with chart.map_context(mem):
        message_step(SimpleNamespace(text=PISTACHIO_ENTRY), mem)
    assert mem['attack']['side'] == 'attack'
    assert mem['attack']['step'] is None


def test_an_unknown_general_is_still_side_enemy():
    mem = g1133()
    with chart.map_context(mem):
        message_step(SimpleNamespace(text='ドリアンしょうぐんがユージアじょうにのりこんだ!!'), mem)
    assert mem['attack']['side'] == 'enemy'


def test_a_garrison_row_marks_the_general_as_ours():
    mem = {'chapter': 6, 'tick': 40000,
           'garrison': {'アルマムーン': ['どうし', 'ピスタチオ']}}
    with chart.map_context(mem):
        message_step(SimpleNamespace(text=PISTACHIO_ENTRY), mem)
    assert mem['attack']['side'] == 'attack'
