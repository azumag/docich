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

ENTRY = 'ポセイドンしょうぐんがリグアじょうにのりこんだ!!'


def live(now=69151):
    return {'chapter': 3, 'tick': now,
            'sorties': {
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
            },
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
    assert mem['sorties']['I:b59ab6:3']['status'] == 'arrived'


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
    mem['sorties']['I:b59ab6:3']['status'] = 'arrived'
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_a_record_without_a_tick_keeps_the_long_march_path_off():
    mem = live()
    mem['sorties']['old'] = {'general': 'ポセイドン', 'target': 'コラダイン', 'status': 'en_route'}
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_two_newest_records_at_the_same_tick_are_not_guessed():
    mem = live()
    mem['sorties']['twin'] = dict(mem['sorties']['I:b59ab6:3'])
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'none')


def test_within_the_busy_window_the_old_rules_still_apply():
    mem = live(now=68474 + 10)
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == ('I:b59ab6:3', 'matched')
    mem['sorties']['twin'] = dict(mem['sorties']['I:b59ab6:3'], tick=68480)
    assert _match_sortie(mem, 'リグア', 'ポセイドン') == (None, 'ambiguous')
